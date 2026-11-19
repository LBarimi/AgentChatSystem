import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from relay import managed_cli, managed_options
from relay.cli_activity import CliActivity
from relay.errors import ApiError
from relay.server import Room


class FakeTurn:
    def __init__(self, args, cwd, *, observer=None):
        self.args = args
        self.cwd = cwd
        self.stopped = Event()
        self.holding = Event()
        self.child = None
        self.observer = observer
        self.session = next((arg for arg in args if len(arg) == 36 and arg.count('-') == 4), str(uuid4()))

    def read(self, prompt, timeout):
        if 'hold the turn' in prompt:
            self.holding.set()
            self.stopped.wait(5)
            raise managed_cli.CliError('turn stopped')
        if '--print' in self.args:
            return '\n'.join(json.dumps(event) for event in (
                {'type': 'system', 'subtype': 'init', 'model': 'claude-opus-5-5'},
                {'type': 'rate_limit_event', 'rate_limit_info': {
                    'unifiedWindows': {'five_hour': {'utilization': 0.12}, 'seven_day': {'utilization': 0.42}},
                }},
                {'type': 'result', 'session_id': self.session, 'result': 'Child finding'},
            ))
        body = 'Ready'
        if '--output-schema' in self.args:
            if 'delegate this' in prompt:
                child = prompt.split('"id": "', 1)[1].split('"', 1)[0]
                body = json.dumps({'message': 'Check the project', 'child_id': child})
            else:
                body = json.dumps({'message': 'Parent summary', 'child_id': None})
        return '\n'.join(json.dumps(event) for event in (
            {'type': 'thread.started', 'thread_id': self.session},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': body}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 4}},
        ))

    def run(self, role, prompt, timeout, *, idle_timeout=None):
        return managed_cli.result(role, self.read(prompt, timeout))

    def stop(self):
        self.stopped.set()

    def reap(self):
        pass


class ManagedAgentsTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.room = Room(self.directory / 'room.sqlite3')
        self.addCleanup(self.room.db.close)
        self.addCleanup(self.room.managed.close)
        self.addCleanup(patch.stopall)
        patch.object(managed_cli, 'executable', side_effect=lambda role: role).start()
        patch.object(managed_cli, 'Turn', FakeTurn).start()
        patch('relay.managed_agents.codex_quota', return_value={'percent': 24, 'resets_at': None}).start()
        self.channel = self.room.create_channel({'name': 'Team'})['id']

    def create(self, role, name, parent=None, channel=None):
        return self.room.managed.create({
            'role': role, 'name': name, 'cwd': str(self.directory), 'model': '',
            'effort': '', 'parent_id': parent, 'channel_id': channel or self.channel,
        }, 'en')['id']

    def until(self, predicate):
        deadline = monotonic() + 5
        with self.room.changed:
            while not predicate():
                time_left = deadline - monotonic()
                self.assertGreater(time_left, 0)
                self.room.changed.wait(min(time_left, 0.05))

    def ready(self, agent):
        self.until(lambda: self.room.managed.get(agent)['session'])

    def task(self, body, recipient='all', address='127.0.0.1'):
        return self.room.post({'sender': 'user', 'recipient': recipient, 'body': body, 'channel_id': self.channel},
                              user_address=address)['message']

    def test_creates_resumes_delegates_and_updates_usage_at_turn_completion(self):
        with self.room.changed:
            parent = self.create('codex', 'Coordinator')
            self.assertTrue(next(channel for channel in self.room.channels() if channel['id'] == self.channel)['working'])
        self.ready(parent)
        self.assertEqual(self.room.participants(self.channel)[0]['usage']['percent'], 24)
        child = self.create('claude', 'Reviewer', parent)
        self.ready(child)
        self.task('delegate this')
        self.until(lambda: self.room.snapshot(self.channel)['messages'][-1]['body'] == 'Parent summary')
        snapshot = self.room.snapshot(self.channel, viewer_address='127.0.0.1')
        self.assertEqual([m['sender'] for m in snapshot['messages']], [parent, child, 'user', parent, child, parent])
        self.assertEqual([p['usage']['percent'] for p in snapshot['participants']], [24, 42])
        self.assertEqual(snapshot['participants'][1]['model_settings']['model'], 'Opus 5.5')
        self.assertFalse(next(channel for channel in snapshot['channels'] if channel['id'] == self.channel)['working'])
        self.assertEqual(snapshot['participants'][1]['parent_id'], parent)
        self.assertEqual([item['is_manager'] for item in snapshot['participants']], [1, 0])
        self.assertNotIn('cwd', self.room.snapshot(self.channel, viewer_address='192.168.1.10')['participants'][0])
        self.assertNotIn('prompt', self.room.setup(parent, 9081, channel_id=self.channel))

    def test_parent_delete_stops_child_and_removes_both_cards(self):
        parent = self.create('codex', 'Coordinator')
        self.ready(parent)
        child = self.create('codex', 'Implementer', parent)
        self.ready(child)
        self.task('hold the turn', child)
        self.until(lambda: child in self.room.managed.processes and self.room.managed.processes[child].holding.is_set())
        running = self.room.managed.processes[child]
        self.room.remove_participant(parent, self.channel)
        self.assertTrue(running.stopped.is_set())
        self.assertEqual(self.room.participants(self.channel), [])
        self.until(lambda: not self.room.managed.threads)
        snapshot = self.room.snapshot(self.channel)
        self.assertEqual(snapshot['total_messages'], 3)
        self.assertEqual({item['id']: item['is_manager'] for item in snapshot['archived_participants']},
                         {parent: 1, child: 0})

    def test_claude_folder_reviews_are_directed_to_file_tools(self):
        prompts = []

        class CapturingTurn(FakeTurn):
            def read(self, prompt, timeout):
                if '--print' in self.args and 'Inspect this folder' in prompt:
                    prompts.append(prompt)
                return super().read(prompt, timeout)

        with patch.object(managed_cli, 'Turn', CapturingTurn):
            parent = self.create('codex', 'Coordinator')
            self.ready(parent)
            child = self.create('claude', 'Reviewer', parent)
            self.ready(child)
            task = self.task('Inspect this folder', child)
            self.until(lambda: self.room.snapshot(self.channel)['messages'][-1]['reply_to'] == task['id'])
            self.assertEqual(len(prompts), 1)
            self.assertIn('Glob and Grep', prompts[0])
            self.assertIn('read relevant current files with Read', prompts[0])
            self.assertIn('Use Git only when the task asks', prompts[0])

    def test_cli_activity_is_saved_with_task_and_available_to_local_chat(self):
        class ObservedTurn(FakeTurn):
            def read(self, prompt, timeout):
                if self.observer and 'inspect the project' in prompt:
                    for event in (
                        {'type': 'assistant', 'message': {'content': [
                            {'type': 'thinking', 'thinking': 'private reasoning'},
                            {'type': 'tool_use', 'id': 't1', 'name': 'PowerShell', 'input': {
                                'description': 'List project files', 'command': 'private command',
                            }},
                        ]}},
                        {'type': 'user', 'message': {'content': [{
                            'type': 'tool_result', 'tool_use_id': 't1', 'is_error': True,
                            'content': 'It requires approval; private tool output',
                        }]}},
                    ):
                        self.observer(json.dumps(event))
                return super().read(prompt, timeout)

        with patch.object(managed_cli, 'Turn', ObservedTurn):
            parent = self.create('codex', 'Coordinator')
            self.ready(parent)
            child = self.create('claude', 'Reviewer', parent)
            self.ready(child)
            task = self.task('inspect the project', child)['id']
            self.until(lambda: self.room.snapshot(self.channel)['messages'][-1]['reply_to'] == task)
            activity = self.room.managed.activity(self.channel, child, task, 0)['events']
            self.assertEqual([event['kind'] for event in activity], [
                'cliActivityStarted', 'cliActivityCall', 'cliActivityDenied', 'cliActivityFinished',
            ])
            self.assertEqual(activity[2]['detail'], 'PowerShell · List project files')
            self.assertNotIn('private', str(activity))
            self.assertEqual(self.room.managed.activity(self.channel, None, task, activity[1]['id'])['events'], activity[2:])
            with self.assertRaises(ApiError):
                self.room.managed.activity('general', None, task, 0)
            local = self.room.snapshot(self.channel, viewer_address='127.0.0.1')['messages'][-2]
            visitor = self.room.snapshot(self.channel, viewer_address='192.168.1.10')['messages'][-2]
            self.assertTrue(local['has_cli_activity'])
            self.assertNotIn('has_cli_activity', visitor)
            with self.room.changed:
                persisted = sqlite3.connect(self.directory / 'room.sqlite3')
                try:
                    self.assertEqual(persisted.execute('SELECT COUNT(*) FROM cli_activity WHERE message_id=?', (task,)).fetchone()[0], 4)
                finally:
                    persisted.close()
            self.room.delete_message({'channel_id': self.channel, 'message_id': task}, '127.0.0.1')
            with self.room.changed:
                self.assertFalse(self.room.db.execute('SELECT 1 FROM cli_activity WHERE message_id=?', (task,)).fetchone())

    def test_stop_only_cancels_the_selected_task_and_preserves_the_session(self):
        parent = self.create('codex', 'Coordinator')
        self.ready(parent)
        child = self.create('claude', 'Reviewer', parent)
        self.ready(child)
        with self.room.changed:
            session = self.room.managed.get(child)['session']
        message = self.task('hold the turn', child)
        self.until(lambda: child in self.room.managed.processes and self.room.managed.processes[child].holding.is_set())
        with self.assertRaises(ApiError):
            self.room.managed.stop_task(child, 'general', message['id'])
        with self.assertRaises(ApiError):
            self.room.managed.stop_task(child, self.channel, message['id'] + 1)
        self.assertEqual(self.room.managed.stop_task(child, self.channel, message['id']), {'state': 'stopping'})
        self.until(lambda: self.room.managed.get(child)['active_message'] is None)
        with self.room.changed:
            stopped = self.room.snapshot(self.channel)['messages'][-1]
            self.assertEqual(stopped['reply_to'], message['id'])
            self.assertIn('stopped', stopped['body'])
            agent = self.room.managed.get(child)
            self.assertEqual(agent['session'], session)
            self.assertFalse(agent['error'])
            self.assertFalse(next(channel for channel in self.room.channels() if channel['id'] == self.channel)['working'])
        another = self.task('second task', child)
        self.until(lambda: self.room.snapshot(self.channel)['messages'][-1]['reply_to'] == another['id'])

    def test_rejects_external_tasks_and_cross_channel_parent(self):
        parent = self.create('codex', 'Coordinator')
        self.ready(parent)
        with self.assertRaises(ApiError):
            self.create('claude', 'Reviewer', parent, 'general')
        with self.assertRaises(ApiError) as rejected:
            self.task('LAN task', parent, '192.168.1.10')
        self.assertEqual(rejected.exception.status, 403)
        with self.assertRaises(ApiError):
            self.task('LAN broadcast', address='192.168.1.10')
        with self.assertRaises(ApiError):
            self.room.delete_channel(self.channel, allow_managed=False)

    def test_uses_the_same_cli_lookup_for_local_buttons_and_creation(self):
        parent = self.create('codex', 'Coordinator')
        self.ready(parent)
        with patch.object(managed_cli, 'executable', side_effect=lambda role: 'codex' if role == 'codex' else None):
            self.assertEqual(self.room.snapshot(self.channel, viewer_address='127.0.0.1')['cli_available'],
                             {'codex': True, 'claude': False})
            self.assertEqual(self.room.snapshot(self.channel, viewer_address='192.168.1.10')['cli_available'], {})
            with self.assertRaises(ApiError) as missing:
                self.create('claude', 'Reviewer', parent)
            self.assertEqual(missing.exception.key, 'managedCliMissing')

    def test_offers_login_only_after_an_auth_failure_and_retries_same_card(self):
        authenticated = False
        commands = []

        class StartupTurn(FakeTurn):
            def read(self, prompt, timeout):
                nonlocal authenticated
                commands.append(self.args[:2])
                if self.args == ['codex', 'login']:
                    authenticated = True
                    return ''
                if not authenticated:
                    error = {'type': 'turn.failed', 'error': {'message': '401 Unauthorized: Missing bearer authentication'}}
                    raise managed_cli.CliError('CLI failed', output=json.dumps(error))
                return super().read(prompt, timeout)

        with patch.object(managed_cli, 'Turn', StartupTurn):
            parent = self.create('codex', 'Coordinator')
            self.until(lambda: self.room.managed.get(parent)['error'])
            card = self.room.participants(self.channel, local=True)[0]
            self.assertEqual((card['state'], card['auth_state']), ('auth', 'idle'))
            self.assertEqual(commands, [['codex', 'exec']])
            self.room.managed.login(parent, self.channel)
            self.ready(parent)
            self.assertEqual(commands, [['codex', 'exec'], ['codex', 'login'], ['codex', 'exec']])
            self.assertEqual(self.room.participants(self.channel)[0]['id'], parent)

    def test_generic_startup_failure_offers_retry_without_login(self):
        class InvalidModelTurn(FakeTurn):
            def read(self, prompt, timeout):
                raise managed_cli.CliError('CLI exited', diagnostics='Error: unknown model')

        with patch.object(managed_cli, 'Turn', InvalidModelTurn):
            parent = self.create('codex', 'Coordinator')
            self.until(lambda: self.room.managed.get(parent)['error'])
            card = self.room.participants(self.channel, local=True)[0]
            self.assertEqual(card['state'], 'failed')
            self.assertEqual(card['error'], 'managedModelRejected')
            self.assertNotIn('auth_state', card)
            with self.assertRaises(ApiError):
                self.room.managed.login(parent, self.channel)
        self.room.managed.retry(parent, self.channel)
        self.ready(parent)

    def test_task_has_two_hour_limit_and_reports_timeout_to_chat(self):
        durations = []

        class SlowTurn(FakeTurn):
            def read(self, prompt, timeout):
                durations.append(timeout)
                if 'task exceeds limit' in prompt:
                    raise subprocess.TimeoutExpired(self.args, timeout)
                return super().read(prompt, timeout)

        with patch.object(managed_cli, 'Turn', SlowTurn):
            parent = self.create('codex', 'Coordinator')
            self.ready(parent)
            child = self.create('claude', 'Reviewer', parent)
            self.ready(child)
            message = self.task('task exceeds limit', child)
            self.until(lambda: self.room.managed.get(child)['error'] == 'managedTimedOut')
            reply = self.room.snapshot(self.channel)['messages'][-1]
            self.assertEqual(reply['reply_to'], message['id'])
            self.assertIn('two hours', reply['body'])
            self.assertEqual(durations, [120, 120, 7200])

    def test_expired_login_can_recover_without_resetting_session(self):
        class ExpiredTurn(FakeTurn):
            def read(self, prompt, timeout):
                if self.args == ['codex', 'login']:
                    return ''
                if 'expired login' in prompt:
                    raise managed_cli.CliError('CLI failed', diagnostics='401 Unauthorized')
                return super().read(prompt, timeout)

        with patch.object(managed_cli, 'Turn', ExpiredTurn):
            agent = self.create('codex', 'Coordinator')
            self.ready(agent)
            session = self.room.managed.get(agent)['session']
            self.task('expired login')
            self.until(lambda: self.room.managed.get(agent)['error'] == 'managedAuthRequired')
            for recipient in ('all', agent):
                with self.assertRaises(ApiError) as blocked:
                    self.task('new task while signed out', recipient)
                self.assertEqual(blocked.exception.key, 'managedAuthRequired')
            self.room.managed.login(agent, self.channel)
            self.until(lambda: self.room.managed.get(agent)['error'] is None)
            self.assertEqual(self.room.managed.get(agent)['session'], session)


class ManagedProcessTest(unittest.TestCase):
    def test_codex_command_activity_classifies_denial_without_saving_output(self):
        records = []
        observe = CliActivity('codex', lambda kind, detail: records.append((kind, detail)))
        command = {'type': 'command_execution', 'command': 'type listing.txt'}
        observe(json.dumps({'type': 'item.started', 'item': command}))
        observe(json.dumps({'type': 'item.completed', 'item': command | {
            'status': 'failed', 'exit_code': 1, 'aggregated_output': 'Access is denied. Debug output',
        }}))
        self.assertEqual(records, [
            ('cliActivityCall', 'type listing.txt'), ('cliActivityDenied', 'type listing.txt'),
        ])

    def test_codex_disallows_interactive_requests_with_workspace_access(self):
        agent = {'role': 'codex', 'session': 'existing-session', 'model': '', 'effort': ''}
        args = managed_cli.command('codex', agent)
        self.assertEqual(args[:3], ['codex', 'exec', 'resume'])
        for flag in ('approval_policy="never"', 'sandbox_mode="workspace-write"',
                     'sandbox_workspace_write.writable_roots=[]', 'sandbox_workspace_write.network_access=false',
                     'tools.experimental_request_user_input.enabled=false', 'features.request_permissions_tool=false',
                     'features.tool_call_mcp_elicitation=false'):
            self.assertIn(flag, args)

    def test_watchdog_allows_progress_and_stops_a_silent_cli(self):
        session = str(uuid4())
        program = (
            'import json, sys, time\n'
            'assert sys.stdin.read() == "task"\n'
            'for _ in range(10):\n'
            '    print(json.dumps({"type":"progress"}), flush=True)\n'
            '    time.sleep(0.13)\n'
            'print(json.dumps({"type":"result","session_id":sys.argv[1],"result":"Done"}),flush=True)\n'
        )
        with TemporaryDirectory() as directory:
            active = managed_cli.Turn([sys.executable, '-c', program, session], directory)
            try:
                self.assertEqual(active.run('claude', 'task', timeout=8, idle_timeout=0.7)[1], 'Done')
            finally:
                active.stop()
                active.reap()
            silent = managed_cli.Turn([sys.executable, '-c', 'import time; time.sleep(10)'], directory)
            try:
                with self.assertRaises(managed_cli.CliError) as caught:
                    silent.run('claude', '', 8, idle_timeout=0.25)
                self.assertEqual(caught.exception.key, 'managedNoProgress')
            finally:
                silent.stop()
                silent.reap()

    def test_activity_is_observed_before_process_returns_final_result(self):
        session = str(uuid4())
        program = (
            'import json, sys, time\n'
            'from pathlib import Path\n'
            'print(json.dumps({"type":"progress"}), flush=True)\n'
            'while not Path("continue").exists(): time.sleep(.02)\n'
            'print(json.dumps({"type":"result","session_id":sys.argv[1],"result":"Done"}),flush=True)\n'
        )
        with TemporaryDirectory() as directory:
            seen = []

            def observe(line):
                seen.append(json.loads(line)['type'])
                (Path(directory) / 'continue').touch()

            turn = managed_cli.Turn([sys.executable, '-c', program, session], directory, observer=observe)
            try:
                self.assertEqual(turn.run('claude', '', timeout=5, idle_timeout=2)[1], 'Done')
                self.assertEqual(seen, ['progress', 'result'])
            finally:
                turn.stop()
                turn.reap()

    def test_claude_approves_workspace_edits_with_platform_shell_limits(self):
        agent = {'role': 'claude', 'session': 'existing-session', 'model': 'sonnet', 'effort': 'high'}
        attachments = Path.cwd() / 'var' / 'acs-turn-1234'
        for platform in ('nt', 'posix'):
            with self.subTest(platform=platform), patch.object(managed_cli.os, 'name', platform):
                args = managed_cli.command('claude', agent, attachments=attachments)
                settings = json.loads(args[args.index('--settings') + 1])
                self.assertEqual(args[args.index('--permission-mode') + 1], 'acceptEdits')
                self.assertEqual(args[args.index('--permission-prompts') + 1], 'none')
                self.assertEqual(args[args.index('--resume') + 1], 'existing-session')
                self.assertNotIn('--dangerously-skip-permissions', args)
                permissions = settings['permissions']
                self.assertTrue(permissions['blockReadsOutsideWorkingDirectories'])
                self.assertTrue(permissions['blockWritesOutsideWorkingDirectories'])
                self.assertEqual(permissions['additionalDirectories'], [str(attachments)])
                self.assertEqual(permissions['deny'], ['Edit(//**/acs-turn-1234/**)'])
                if platform == 'nt':
                    self.assertNotIn('sandbox', settings)
                else:
                    self.assertTrue(settings['sandbox']['autoAllowBashIfSandboxed'])
                    self.assertFalse(settings['sandbox']['allowUnsandboxedCommands'])
                    self.assertEqual(settings['sandbox']['filesystem']['denyWrite'], [str(attachments)])

        plain = managed_cli.command('claude', agent)
        self.assertNotIn('additionalDirectories', json.loads(plain[plain.index('--settings') + 1])['permissions'])

    def test_claude_error_requires_auth_but_model_message_does_not(self):
        error_result = {'type': 'result', 'is_error': True, 'result': 'Not logged in · Please run /login'}
        with self.assertRaises(managed_cli.CliError) as rejected:
            managed_cli.result('claude', json.dumps(error_result))
        self.assertEqual(managed_cli.failure_key('claude', rejected.exception), 'managedAuthRequired')
        failed = managed_cli.CliError('CLI exited', output=json.dumps({
            'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Please run /login'},
        }), diagnostics='Error: network timeout')
        self.assertEqual(managed_cli.failure_key('codex', failed), 'managedRunFailed')

    def test_classifies_specific_cli_errors(self):
        for detail, expected in (
            ('Error: invalid value high for --effort', 'managedEffortRejected'),
            ('API Error: model_not_found', 'managedModelRejected'),
            ('Error: Permission denied', 'managedPermissionDenied'),
            ('API Error: 429 Too Many Requests', 'managedRateLimited'),
        ):
            with self.subTest(detail=detail):
                output = json.dumps({'type': 'result', 'is_error': True, 'result': detail})
                with self.assertRaises(managed_cli.CliError) as raised:
                    managed_cli.result('claude', output, failed=True)
                self.assertEqual(raised.exception.key, expected)

    def test_large_intermediate_output_does_not_hide_final_result(self):
        session = str(uuid4())
        script = (
            'import json, sys\n'
            'sys.stdin.read()\n'
            'print(json.dumps({"type":"system","subtype":"init","model":"claude-opus-5-5"}))\n'
            'for _ in range(12):\n'
            '    print(json.dumps({"type":"user","message":"x"*1000000}))\n'
            'print(json.dumps({"type":"user","message":"y"*9000000}))\n'
            'print(json.dumps({"type":"result","session_id":sys.argv[1],"result":"Completed"}))\n'
        )
        with TemporaryDirectory() as directory:
            turn = managed_cli.Turn([sys.executable, '-c', script, session], directory)
            try:
                self.assertEqual(turn.run('claude', 'task', timeout=10), (session, 'Completed', 'claude-opus-5-5', None))
            finally:
                turn.stop()
                turn.reap()

    def test_cancellation_terminates_the_process_and_its_child(self):
        child = (
            'from pathlib import Path; from time import sleep; '
            'Path("started").touch(); sleep(1.5); Path("survived").touch()'
        )
        parent = 'import subprocess, sys; subprocess.run([sys.executable, "-c", sys.argv[1]])'
        with TemporaryDirectory() as directory:
            turn = managed_cli.Turn([sys.executable, '-c', parent, child], directory)
            try:
                root = Path(directory)
                deadline = monotonic() + 5
                while not (root / 'started').exists():
                    self.assertLess(monotonic(), deadline)
                    sleep(0.01)
                with self.assertRaises(subprocess.TimeoutExpired):
                    turn.read('', timeout=0.05)
                turn.stop()
                self.assertIsNotNone(turn.process.wait(timeout=2))
                sleep(1.5)
                self.assertFalse((root / 'survived').exists())
            finally:
                turn.stop()
                turn.reap()


class ManagedCreationOptionsTest(unittest.TestCase):
    def test_claude_uses_cli_models_and_allows_defaults_if_metadata_is_unavailable(self):
        @contextmanager
        def session(args, cwd):
            packets = []
            event = {'type': 'control_response', 'response': {
                'request_id': 'acs-picker', 'subtype': 'success', 'response': {'models': [
                    {'value': 'default', 'displayName': 'Default', 'resolvedModel': 'claude-opus-5-5',
                     'supportedEffortLevels': ['low', 'max']},
                    {'value': 'opus[1m]', 'displayName': 'Opus', 'resolvedModel': 'claude-opus-5-5[1m]',
                     'supportedEffortLevels': ['high']},
                ]},
            }}

            def receive(matches):
                self.assertEqual(packets[0]['request']['subtype'], 'initialize')
                self.assertTrue(matches(event))
                return event

            yield packets.append, receive

        catalog = managed_options.ModelOptions(Path.cwd())
        with patch.object(managed_cli, 'require_executable', return_value='claude'), \
                patch.object(managed_options, 'metadata_session', session):
            models = catalog.get('claude')['models']
            self.assertEqual([option['id'] for option in models], ['', 'opus[1m]'])
            self.assertEqual([option['name'] for option in models], ['Opus 5.5', 'Opus 5.5 (1M)'])
            catalog.validate('claude', 'opus[1m]', 'high')
        with patch.object(managed_cli, 'require_executable', return_value='claude'), \
                patch.object(managed_options, 'metadata_session', side_effect=managed_cli.CliError('Unavailable')):
            self.assertEqual(catalog.get('claude', refresh=True), {'models': [], 'available': False})
            catalog.validate('claude', '', '')

    def test_lists_all_codex_pages_and_validates_effort_for_selected_model(self):
        requests = []

        def model(name, levels, default=False):
            return {
                'model': name, 'displayName': name, 'isDefault': default,
                'supportedReasoningEfforts': [{'reasoningEffort': level} for level in levels],
                'defaultReasoningEffort': levels[0],
            }

        @contextmanager
        def session(args, cwd):
            self.assertEqual(args[1:3], ['app-server', '--listen'])

            def receive(matches):
                packet = requests[-1]
                response = {}
                if packet['method'] == 'model/list':
                    if packet['params']['cursor'] is None:
                        response = {'data': [model('gpt-a', ['low', 'ultra'], True)], 'nextCursor': '2'}
                    else:
                        self.assertEqual(packet['params']['cursor'], '2')
                        response = {'data': [model('gpt-b', ['medium', 'max'])]}
                event = {'id': packet['id'], 'result': response}
                self.assertTrue(matches(event))
                return event

            yield requests.append, receive

        catalog = managed_options.ModelOptions(Path.cwd())
        with patch.object(managed_cli, 'require_executable', return_value='codex'), \
                patch.object(managed_options, 'metadata_session', session):
            models = catalog.get('codex')['models']
            self.assertEqual([item['id'] for item in models], ['gpt-a', 'gpt-b'])
            self.assertTrue(models[0]['default_model'])
            catalog.validate('codex', 'gpt-a', 'ultra')
            with self.assertRaises(ApiError) as rejected:
                catalog.validate('codex', 'gpt-b', 'ultra')
            self.assertEqual(rejected.exception.key, 'managedEffortInvalid')
            self.assertEqual(len(requests), 4)

    def test_folder_picker_lists_only_directories_and_reports_parent(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            nested = root / '문서'
            nested.mkdir()
            (root / 'file.txt').touch()
            listing = managed_options.folders('', root)
            self.assertEqual(listing['folders'], [{'name': '문서', 'path': str(nested)}])
            self.assertEqual(managed_options.folders(str(nested), root)['parent'], str(root))
            with self.assertRaises(ApiError):
                managed_options.folders(str(root / 'file.txt'), root)
