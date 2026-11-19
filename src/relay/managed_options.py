"""Model and folder choices for the local agent creation form."""
from contextlib import contextmanager
from queue import Queue, Empty
from threading import Lock, Thread
from time import monotonic
from pathlib import Path
import json
import os
import re
import subprocess

from relay import managed_cli
from relay.errors import ApiError


MODEL_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9.::_/\[\]-]{0,127}')
EFFORT_ID = re.compile(r'[a-z][a-z0-9_-]{0,31}')
ROLES = ('codex', 'claude')


def folder_path(value, *, browsing=False):
    try:
        if not isinstance(value, str) or not value or '\0' in value:
            raise ValueError
        path = Path(value)
        if not path.is_absolute() or (browsing and os.name == 'nt' and path.drive.startswith('\\')):
            raise ValueError
        path = path.resolve(strict=True)
        if (browsing and os.name == 'nt' and path.drive.startswith('\\')) or not path.is_dir():
            raise ValueError
        return path
    except (OSError, RuntimeError, ValueError):
        raise ApiError('Enter an existing absolute folder path.', key='managedFolderInvalid') from None


def folders(value, project):
    current = folder_path(str(project) if value in (None, '') else value, browsing=True)
    roots = [
        {'label': 'managedProjectFolder', 'path': str(project)},
        {'label': 'managedHomeFolder', 'path': str(Path.home())},
    ]
    if os.name == 'nt':
        from ctypes import c_wchar_p, windll

        mask = windll.kernel32.GetLogicalDrives()
        drive_type = windll.kernel32.GetDriveTypeW
        drive_type.argtypes = [c_wchar_p]
        for index in range(26):
            if mask & (1 << index):
                root = f'{chr(65 + index)}:\\'
                if drive_type(root) in (2, 3, 5, 6):
                    roots.append({'path': root})
    else:
        roots.append({'path': '/'})
    entries = []
    try:
        with os.scandir(current) as children:
            for child in children:
                try:
                    if child.is_dir():
                        child.name.encode('utf-8')
                        entries.append({'name': child.name, 'path': child.path})
                except (OSError, UnicodeError):
                    continue
    except OSError:
        raise ApiError('Could not list folders here.', 403, key='managedFolderUnreadable') from None
    entries.sort(key=lambda child: child['name'].casefold())
    return {
        'path': str(current), 'parent': str(current.parent) if current.parent != current else None,
        'roots': roots, 'folders': entries,
    }


@contextmanager
def metadata_session(args, cwd):
    turn = managed_cli.Turn(args, cwd, stream=True)
    events = Queue()
    deadline = monotonic() + 12

    def read_events():
        try:
            for line in iter(lambda: turn.process.stdout.readline(2 * 1024 * 1024), ''):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    events.put(event)
        finally:
            events.put(None)

    def send(event):
        turn.process.stdin.write(json.dumps(event) + '\n')
        turn.process.stdin.flush()

    def receive(matches):
        while monotonic() < deadline:
            event = events.get(timeout=max(0, deadline - monotonic()))
            if event is None:
                break
            if matches(event):
                return event
        raise managed_cli.CliError('The CLI did not return model metadata')

    reader = Thread(target=read_events, daemon=True)
    reader.start()
    try:
        yield send, receive
    finally:
        turn.stop()
        reader.join(timeout=2)
        turn.reap()


def codex_models(program, cwd):
    with metadata_session([program, 'app-server', '--listen', 'stdio://'], cwd) as (send, receive):
        send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {
            'name': 'agentchatsystem', 'title': 'AgentChatSystem', 'version': '1.0.0',
        }}})
        receive(lambda event: event.get('id') == 1)['result']
        send({'method': 'initialized'})
        cursor = None
        for request_id in range(2, 22):
            send({'id': request_id, 'method': 'model/list', 'params': {
                'includeHidden': False, 'limit': 50, 'cursor': cursor,
            }})
            page = receive(lambda event: event.get('id') == request_id)['result']
            for model in page['data']:
                if model.get('hidden'):
                    continue
                yield {
                    'id': model['model'], 'name': model.get('displayName', model['model']),
                    'efforts': [level['reasoningEffort'] for level in model.get('supportedReasoningEfforts', [])],
                    'default_effort': model.get('defaultReasoningEffort', ''),
                    'default_model': model.get('isDefault') is True,
                }
            next_cursor = page.get('nextCursor')
            if not next_cursor:
                return
            if next_cursor == cursor:
                break
            cursor = next_cursor
        raise managed_cli.CliError('Model pagination did not complete')


def claude_models(program, cwd):
    # Same initialize exchange as the Claude Agent SDK. No user turn is sent.
    packet = {'type': 'control_request', 'request_id': 'acs-picker', 'request': {'subtype': 'initialize'}}
    args = [program, '--print', '--input-format', 'stream-json', '--output-format', 'stream-json',
            '--verbose', '--no-session-persistence', '--settings', '{"disableAllHooks":true}',
            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}']
    with metadata_session(args, cwd) as (send, receive):
        send(packet)
        event = receive(lambda event: event.get('type') == 'control_response'
                        and event.get('response', {}).get('request_id') == 'acs-picker')
        response = event['response']
        if response.get('subtype') != 'success':
            raise managed_cli.CliError('The CLI did not return model metadata')
        for model in response['response']['models']:
            yield {
                'id': '' if model['value'] == 'default' else model['value'],
                'name': model.get('displayName', model['value']),
                'efforts': model.get('supportedEffortLevels') or [],
                'default_effort': '',
                'default_model': model['value'] == 'default',
            }
        return


class ModelOptions:
    def __init__(self, project):
        self.project = project
        self.cached = {}
        self.locks = {role: Lock() for role in ROLES}

    def get(self, role, *, refresh=False):
        program = managed_cli.require_executable(role)
        with self.locks[role]:
            cached = self.cached.get(role)
            if not refresh and cached and cached[0] == program and monotonic() < cached[1]:
                return cached[2]
            choices = []
            try:
                discover = codex_models if role == 'codex' else claude_models
                seen = set()
                for option in discover(program, self.project):
                    model = option['id']
                    if not isinstance(model, str) or (model and not MODEL_ID.fullmatch(model)) or model in seen:
                        continue
                    efforts = list(dict.fromkeys(level for level in option['efforts']
                                                 if isinstance(level, str) and EFFORT_ID.fullmatch(level)))
                    default = option['default_effort']
                    if default and isinstance(default, str) and EFFORT_ID.fullmatch(default) and default not in efforts:
                        efforts.insert(0, default)
                    seen.add(model)
                    choices.append({
                        'id': model, 'name': str(option['name'])[:160], 'efforts': efforts,
                        'default_effort': default if default in efforts else '',
                        'default_model': option['default_model'],
                    })
            except (OSError, ValueError, TypeError, KeyError, AttributeError, Empty,
                    subprocess.TimeoutExpired, managed_cli.CliError):
                choices = []
            result = {'models': choices, 'available': bool(choices)}
            self.cached[role] = (program, monotonic() + (30 if choices else 5), result)
            return result

    def validate(self, role, model, effort):
        if not isinstance(model, str) or (model and not MODEL_ID.fullmatch(model)):
            raise ApiError('Choose a model from the CLI list.', key='managedModelInvalid')
        if not isinstance(effort, str) or (effort and not EFFORT_ID.fullmatch(effort)):
            raise ApiError('Choose a supported reasoning level.', key='managedEffortInvalid')
        if not model and not effort:
            return
        options = self.get(role)['models']
        option = next((choice for choice in options if choice['id'] == model), None)
        if option is None:
            raise ApiError('Choose a model from the CLI list.', key='managedModelInvalid')
        if effort and effort not in option['efforts']:
            raise ApiError('Choose a supported reasoning level.', key='managedEffortInvalid')
