"""Run a single turn in a managed Codex or Claude Code conversation."""
from collections import deque
import json
import os
from pathlib import Path
import re
import signal
import subprocess
from tempfile import TemporaryFile
from threading import Thread
from time import monotonic
from uuid import UUID

from relay.errors import ApiError


MAX_EVENT_BYTES = 8 * 1024 * 1024


class CliError(Exception):
    def __init__(self, message, *, output='', diagnostics='', key=None):
        super().__init__(message)
        self.output = output
        self.diagnostics = diagnostics
        self.key = key


def executable(role):
    # Use native programs on Windows so untrusted form fields never reach cmd.exe.
    name = role + ('.exe' if os.name == 'nt' else '')
    for directory in os.get_exec_path():
        path = Path(directory) / name
        if path.is_absolute() and path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def require_executable(role):
    if role not in ('codex', 'claude'):
        raise ApiError('Choose Codex or Claude.', key='errorRole')
    program = executable(role)
    if not program:
        provider = 'Codex' if role == 'codex' else 'Claude Code'
        raise ApiError('Install the CLI and add its executable to PATH.', 503, key='managedCliMissing', provider=provider)
    return program


def login_command(role):
    program = require_executable(role)
    return [program, 'login'] if role == 'codex' else [program, 'auth', 'login']


def failure_key(role, error):
    """Classify diagnostics and provider error events without using model replies."""
    if error.key:
        return error.key
    errors = [error.diagnostics]
    for line in error.output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            # Some CLI versions report a plain-text error when no session starts.
            if re.match(r'^\s*(?:error:|not logged in\b|please run /login\b)', line, re.I):
                errors.append(line)
            continue
        if not isinstance(event, dict):
            continue
        if role == 'codex' and event.get('type') in ('error', 'turn.failed'):
            detail = event.get('error') or event.get('message') or ''
            errors.append(detail.get('message', '') if isinstance(detail, dict) else detail)
        elif role == 'claude' and event.get('type') == 'result' and event.get('is_error'):
            errors.append(event.get('result', ''))
            if isinstance(event.get('errors'), list):
                errors.extend(event['errors'])
            if event.get('permission_denials'):
                errors.append('permission_denied')
    detail = '\n'.join(text for text in errors if isinstance(text, str))
    for key, pattern in (
        ('managedAuthRequired', r'^\s*(?:error:\s*)?not logged in\b|\bplease run /login\b|'
         r'\b(?:run|use) `?codex login\b|\b401\s+(?:unauthorized|authentication_error)\b|'
         r'\b(?:authentication_error|invalid_api_key|token_expired|token_revoked)\b'),
        ('managedRateLimited', r'\brate.limit(?:ed|_error| exceeded)?\b|\b429\s+too many requests\b|'
         r'\busage limit\b|\binsufficient_quota\b'),
        ('managedEffortRejected', r'\b(?:invalid|unknown|unsupported)\b[^\r\n]*\b(?:effort|reasoning level)\b|'
         r'\b(?:effort|reasoning level)\b[^\r\n]*\b(?:unsupported|not supported|not a valid value)\b'),
        ('managedModelRejected', r'\b(?:unknown|invalid|unsupported) model\b|\bmodel_not_found\b|'
         r'\bmodel\b[^\r\n]*\b(?:not found|not supported|not available|does not exist|may not exist)\b'),
        ('managedPermissionDenied', r'\bpermission.denied\b|\b(?:permission_error|access is denied|EACCES|EPERM)\b'),
    ):
        if re.search(pattern, detail, re.I | re.M):
            return key
    return 'managedRunFailed'


def command(program, agent, schema=None, *, attachments=None):
    session = agent['session']
    model = agent['model']
    if agent['role'] == 'codex':
        args = [program, 'exec']
        if session:
            args.append('resume')
        args += ['--json', '--skip-git-repo-check', '-c', 'approval_policy="never"',
                 '-c', 'sandbox_mode="workspace-write"', '-c', 'sandbox_workspace_write.writable_roots=[]',
                 '-c', 'sandbox_workspace_write.network_access=false', '-c', 'allow_login_shell=false',
                 '-c', 'features.default_mode_request_user_input=false',
                 '-c', 'tools.experimental_request_user_input.enabled=false',
                 '-c', 'features.request_permissions_tool=false', '-c', 'features.tool_call_mcp_elicitation=false']
        if agent['effort']:
            args += ['-c', 'model_reasoning_effort=' + json.dumps(agent['effort'])]
        if model:
            args += ['--model', model]
        if schema:
            args += ['--output-schema', str(schema)]
        if session:
            args.append(session)
        return args + ['-']
    permissions = {
        'blockReadsOutsideWorkingDirectories': True,
        'blockWritesOutsideWorkingDirectories': True,
    }
    settings = {'permissions': permissions}
    if attachments:
        # ACS passes only the current turn's copies of user attachments.
        permissions['additionalDirectories'] = [str(attachments)]
        permissions['deny'] = [f'Edit(//**/{attachments.name}/**)']
    if os.name != 'nt':
        settings['sandbox'] = {
            'enabled': True, 'autoAllowBashIfSandboxed': True,
            'allowUnsandboxedCommands': False,
        }
        if attachments:
            settings['sandbox']['filesystem'] = {'denyWrite': [str(attachments)]}
    args = [program, '--print', '--output-format', 'stream-json', '--verbose',
            '--permission-mode', 'acceptEdits', '--permission-prompts', 'none',
            '--settings', json.dumps(settings)]
    if agent['effort']:
        args += ['--effort', agent['effort']]
    if model:
        args += ['--model', model]
    if session:
        args += ['--resume', session]
    return args


def result(role, output, *, failed=False, diagnostics=''):
    session = body = quota = last = None
    model = ''
    completed = False
    errors = deque(maxlen=16)
    for line in output.splitlines() if isinstance(output, str) else output:
        try:
            event = json.loads(line)
        except ValueError:
            if line.strip():
                errors.append(line[:4096])
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get('type')
        if role == 'codex':
            if kind == 'thread.started':
                session = event.get('thread_id')
            elif kind == 'item.completed' and isinstance(event.get('item'), dict):
                if event['item'].get('type') == 'agent_message':
                    body = event['item'].get('text')
            elif kind == 'turn.completed':
                completed = True
            elif kind in ('error', 'turn.failed'):
                if len(line) <= 65_536:
                    errors.append(line)
                if kind == 'turn.failed':
                    completed = False
        elif kind == 'system' and event.get('subtype') == 'init':
            model = event.get('model') or model
        elif kind == 'assistant' and event.get('parent_tool_use_id') is None:
            message = event.get('message')
            if isinstance(message, dict):
                model = message.get('model') or model
        elif kind == 'rate_limit_event':
            quota = claude_quota(event.get('rate_limit_info')) or quota
        elif kind == 'result':
            last = event
            if event.get('is_error') and len(line) <= 65_536:
                errors.append(line)
    if last:
        completed = not last.get('is_error')
        session, body = last.get('session_id'), last.get('result')
    if failed or not completed:
        failure = CliError('Unsuccessful or invalid CLI response', output='\n'.join(errors), diagnostics=diagnostics)
        failure.key = failure_key(role, failure)
        if failure.key == 'managedRunFailed' and not failed and not errors:
            failure.key = 'managedResponseInvalid'
        raise failure
    try:
        if not isinstance(body, str) or not body.strip():
            raise ValueError
        return str(UUID(session)), body.strip(), model if isinstance(model, str) else '', quota
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise CliError('Invalid final CLI response', key='managedResponseInvalid') from error


def quota_window(percent, resets_at):
    if type(percent) not in (int, float) or not 0 <= percent <= 100:
        return None
    return {'percent': percent, 'resets_at': resets_at if type(resets_at) is int and resets_at > 0 else None}


def claude_quota(info):
    if not isinstance(info, dict):
        return None
    windows = info.get('unifiedWindows')
    values = [value for key, value in windows.items() if key in ('five_hour', 'seven_day')] if isinstance(windows, dict) else []
    values.append(info)
    candidates = []
    for window in values:
        if not isinstance(window, dict):
            continue
        utilization = window.get('utilization')
        if type(utilization) in (int, float) and 0 <= utilization <= 1:
            candidates.append(quota_window(utilization * 100, window.get('resetsAt')))
    return max(candidates, key=lambda item: item['percent'], default=None)


class Turn:
    def __init__(self, args, cwd, *, stream=False, observer=None):
        self.output = None if stream else TemporaryFile()
        self.error_output = None
        self.guard = None
        self.reader = None
        self.capture_failed = False
        self.stopped = False
        try:
            self.error_output = TemporaryFile()
            self.guard = WindowsJob() if os.name == 'nt' else None
            self.process = subprocess.Popen(
                args, cwd=Path(cwd) if cwd else None, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE if stream or observer else self.output,
                stderr=self.error_output, encoding='utf-8', errors='replace',
                start_new_session=os.name != 'nt',
                creationflags=(subprocess.CREATE_NO_WINDOW | 0x04) if self.guard else 0,  # CREATE_SUSPENDED
            )
            if self.guard:
                try:
                    self.guard.add(self.process)
                except OSError:
                    self.process.kill()
                    self.process.communicate()
                    raise
            if observer:
                source, self.process.stdout = self.process.stdout, None
                self.reader = Thread(target=self._capture_output, args=(source, observer), daemon=True)
                self.reader.start()
        except BaseException:
            if self.guard:
                self.guard.close()
            if self.error_output:
                self.error_output.close()
            if self.output:
                self.output.close()
            raise

    def _capture_output(self, source, observer):
        oversized = False
        try:
            with source:
                while line := source.readline(MAX_EVENT_BYTES + 1):
                    self.output.write(line.encode('utf-8', 'replace'))
                    self.output.flush()
                    if not oversized and len(line) <= MAX_EVENT_BYTES:
                        observer(line)
                    oversized = not line.endswith('\n')
        except OSError:
            self.capture_failed = True

    def stop(self):
        # Called under the room lock, including after a successful turn.
        if self.stopped:
            return
        if self.guard:
            self.guard.close()
        else:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.stopped = True

    def read(self, prompt, timeout):
        self.process.communicate(prompt, timeout=timeout)
        self.output.seek(0)
        output = self.output.read(MAX_EVENT_BYTES + 1)
        if len(output) > MAX_EVENT_BYTES:
            raise CliError('CLI output exceeds the response limit')
        output = output.decode('utf-8', errors='replace')
        if self.process.returncode:
            raise CliError('CLI exited unsuccessfully', output=output, diagnostics=self._diagnostics())
        return output

    def run(self, role, prompt, timeout, *, idle_timeout=None):
        if idle_timeout is None:
            self.process.communicate(prompt, timeout=timeout)
        else:
            self._wait_for_output(prompt, timeout, idle_timeout)
        self._finish_capture()
        self.output.seek(0)
        diagnostics = self._diagnostics()
        return result(role, self._output_lines(), failed=bool(self.process.returncode), diagnostics=diagnostics)

    def _finish_capture(self):
        if not self.reader:
            return
        self.reader.join(timeout=2)
        if self.reader.is_alive():
            self.stop()
            self.reader.join(timeout=2)
        if self.reader.is_alive() or self.capture_failed:
            raise CliError('Could not collect CLI output')

    def _wait_for_output(self, prompt, timeout, idle_timeout):
        started = monotonic()
        deadline, idle_deadline = started + timeout, started + idle_timeout
        sizes = (0, 0)
        while True:
            wait = max(0, min(2, deadline - monotonic(), idle_deadline - monotonic()))
            try:
                self.process.communicate(prompt, timeout=wait)
                return
            except subprocess.TimeoutExpired:
                # Popen retains unsent input internally after a timeout.
                prompt = None
                now = monotonic()
                if now >= deadline:
                    raise subprocess.TimeoutExpired(self.process.args, timeout) from None
                # Query file sizes without seeking the child process's file handles.
                updated = (os.fstat(self.output.fileno()).st_size, os.fstat(self.error_output.fileno()).st_size)
                if updated != sizes:
                    sizes = updated
                    idle_deadline = now + idle_timeout
                elif now >= idle_deadline:
                    raise CliError('CLI produced no new output', key='managedNoProgress') from None

    def _diagnostics(self):
        end = self.error_output.seek(0, os.SEEK_END)
        self.error_output.seek(max(0, end - 65_536))
        return self.error_output.read(65_536).decode('utf-8', errors='replace')

    def _output_lines(self):
        while line := self.output.readline(MAX_EVENT_BYTES + 1):
            if len(line) <= MAX_EVENT_BYTES:
                yield line.decode('utf-8', errors='replace')
            else:
                while line and not line.endswith(b'\n'):
                    line = self.output.readline(MAX_EVENT_BYTES + 1)

    def reap(self):
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)
        finally:
            if self.reader:
                self.reader.join(timeout=2)
            self.process.stdin.close()
            if self.process.stdout:
                self.process.stdout.close()
            if self.output:
                self.output.close()
            self.error_output.close()


class WindowsJob:
    """Close the Job handle to terminate the CLI and its child processes."""
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w

        class BasicLimits(c.Structure):
            _fields_ = [
                ('per_process_time', c.c_int64), ('per_job_time', c.c_int64),
                ('flags', w.DWORD), ('min_ws', c.c_size_t), ('max_ws', c.c_size_t),
                ('active_limit', w.DWORD), ('affinity', c.c_size_t),
                ('priority', w.DWORD), ('scheduling', w.DWORD),
            ]

        class ExtendedLimits(c.Structure):
            _fields_ = [('basic', BasicLimits), ('io', c.c_uint64 * 6),
                        ('memory', c.c_size_t * 4)]

        self.api = c.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [w.LPVOID, w.LPCWSTR]
        self.api.CreateJobObjectW.restype = w.HANDLE
        self.api.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, w.LPVOID, w.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.api.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            error = c.WinError(c.get_last_error())
            self.close()
            raise error

    def add(self, process):
        import ctypes as c
        from ctypes import wintypes as w

        class ThreadEntry(c.Structure):
            _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('thread_id', w.DWORD),
                        ('owner', w.DWORD), ('priority', w.LONG), ('delta', w.LONG), ('flags', w.DWORD)]

        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise c.WinError(c.get_last_error())
        # Popen closes the initial thread handle. Find it while the process is still
        # suspended so even CLI children created immediately inherit the Job.
        self.api.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
        self.api.CreateToolhelp32Snapshot.restype = w.HANDLE
        for function in (self.api.Thread32First, self.api.Thread32Next):
            function.argtypes = [w.HANDLE, c.POINTER(ThreadEntry)]
        self.api.OpenThread.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.api.OpenThread.restype = w.HANDLE
        self.api.ResumeThread.argtypes = [w.HANDLE]
        self.api.ResumeThread.restype = w.DWORD
        snapshot = self.api.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            entry = ThreadEntry()
            entry.size = c.sizeof(entry)
            found = self.api.Thread32First(snapshot, c.byref(entry))
            while found:
                if entry.owner == process.pid:
                    handle = self.api.OpenThread(2, False, entry.thread_id)  # THREAD_SUSPEND_RESUME
                    if not handle:
                        raise c.WinError(c.get_last_error())
                    try:
                        if self.api.ResumeThread(handle) == 0xFFFFFFFF:
                            raise c.WinError(c.get_last_error())
                    finally:
                        self.api.CloseHandle(handle)
                    return
                found = self.api.Thread32Next(snapshot, c.byref(entry))
            raise OSError('Could not find the suspended CLI thread')
        finally:
            self.api.CloseHandle(snapshot)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None
