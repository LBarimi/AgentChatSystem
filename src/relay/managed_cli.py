"""Run a single turn in a managed Codex or Claude Code conversation."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
from tempfile import TemporaryFile
from uuid import UUID

from relay.errors import ApiError


class CliError(Exception):
    def __init__(self, message, *, output='', diagnostics=''):
        super().__init__(message)
        self.output = output
        self.diagnostics = diagnostics


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


def authentication_required(role, error):
    """Examine CLI error events and diagnostics, never a successful model reply."""
    errors = [error.diagnostics]
    for line in error.output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            # Some CLI versions report a plain-text error when no session starts.
            if re.match(r'^\s*(?:error:\s*)?(?:not logged in|please run /login)\b', line, re.I):
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
    pattern = (r'^\s*(?:error:\s*)?not logged in\b|\bplease run /login\b|'
               r'\b(?:run|use) `?codex login\b|\b401\s+(?:unauthorized|authentication_error)\b|'
               r'\b(?:authentication_error|invalid_api_key|token_expired|token_revoked)\b')
    return any(re.search(pattern, detail, re.I | re.M) for detail in errors if isinstance(detail, str))


def command(program, agent, schema=None):
    session = agent['session']
    model = agent['model']
    if agent['role'] == 'codex':
        args = [program, 'exec']
        if session:
            args.append('resume')
        args += ['--json', '--skip-git-repo-check', '-c', 'approval_policy="never"',
                 '-c', 'sandbox_mode="workspace-write"', '-c',
                 'model_reasoning_effort=' + json.dumps(agent['effort'])]
        if model:
            args += ['--model', model]
        if schema:
            args += ['--output-schema', str(schema)]
        if session:
            args.append(session)
        return args + ['-']
    args = [program, '--print', '--output-format', 'json',
            '--permission-mode', 'default', '--effort', agent['effort']]
    if model:
        args += ['--model', model]
    if session:
        args += ['--resume', session]
    return args


def result(role, output):
    try:
        if role == 'codex':
            session = body = usage = None
            completed = False
            for line in output.splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if event['type'] == 'thread.started':
                    session = event['thread_id']
                elif event['type'] == 'item.completed' and event['item']['type'] == 'agent_message':
                    body = event['item']['text']
                elif event['type'] == 'turn.completed':
                    completed, usage = True, event.get('usage')
            if not completed:
                raise ValueError
        else:
            event = json.loads(output)
            if event.get('type') != 'result' or event.get('is_error'):
                raise ValueError
            session, body, usage = event['session_id'], event['result'], event.get('usage')
        if not isinstance(body, str) or not body.strip():
            raise ValueError
        return str(UUID(session)), body.strip(), tokens(role, usage)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise CliError('Unsuccessful or invalid CLI response', output=output) from error


def tokens(role, usage):
    if not isinstance(usage, dict):
        return None
    counts = {}
    for key in ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens'):
        value = usage.get(key)
        if type(value) is int and 0 <= value < 10**12:
            counts[key] = value
    if 'input_tokens' not in counts or 'output_tokens' not in counts:
        return None
    input_tokens = counts['input_tokens']
    if role == 'claude':
        input_tokens += counts.get('cache_creation_input_tokens', 0) + counts.get('cache_read_input_tokens', 0)
    return {'input': input_tokens, 'output': counts['output_tokens']}


class Turn:
    def __init__(self, args, cwd):
        self.output = TemporaryFile()
        self.error_output = None
        self.guard = None
        try:
            self.error_output = TemporaryFile()
            self.guard = WindowsJob() if os.name == 'nt' else None
            self.process = subprocess.Popen(
                args, cwd=Path(cwd) if cwd else None, stdin=subprocess.PIPE, stdout=self.output,
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
        except BaseException:
            if self.guard:
                self.guard.close()
            if self.error_output:
                self.error_output.close()
            self.output.close()
            raise
        self.stopped = False

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
        output = self.output.read(8 * 1024 * 1024 + 1)
        if len(output) > 8 * 1024 * 1024:
            raise CliError('CLI output exceeds the response limit')
        output = output.decode('utf-8', errors='replace')
        if self.process.returncode:
            self.error_output.seek(max(0, self.error_output.seek(0, os.SEEK_END) - 65536))
            diagnostics = self.error_output.read(65536).decode('utf-8', errors='replace')
            raise CliError('CLI exited unsuccessfully', output=output, diagnostics=diagnostics)
        return output

    def reap(self):
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)
        finally:
            self.process.stdin.close()
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
