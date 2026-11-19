import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from relay import managed_cli
from relay.errors import ApiError
from relay.server import Room


class FakeTurn:
    def __init__(self, args, cwd):
        self.args = args
        self.cwd = cwd
        self.stopped = Event()
        self.holding = Event()
        self.child = None
        self.session = next((arg for arg in args if len(arg) == 36 and arg.count('-') == 4), str(uuid4()))

    def read(self, prompt, timeout):
        if 'hold the turn' in prompt:
            self.holding.set()
            self.stopped.wait(5)
            raise managed_cli.CliError('turn stopped')
        if '--print' in self.args:
            return json.dumps({'type': 'result', 'session_id': self.session, 'result': 'Child finding',
                               'usage': {'input_tokens': 2, 'cache_read_input_tokens': 3, 'output_tokens': 1}})
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
        patch.object(managed_cli, 'require_login').start()
        patch.object(managed_cli, 'executable', side_effect=lambda role: role).start()
        patch.object(managed_cli, 'Turn', FakeTurn).start()
        self.channel = self.room.create_channel({'name': 'Team'})['id']

    def create(self, role, name, parent=None, channel=None):
        return self.room.managed.create({
            'role': role, 'name': name, 'cwd': str(self.directory), 'model': '',
            'effort': 'medium', 'parent_id': parent, 'channel_id': channel or self.channel,
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
        parent = self.create('codex', 'Coordinator')
        self.ready(parent)
        self.assertEqual(self.room.participants(self.channel)[0]['usage']['total'], 14)
        child = self.create('claude', 'Reviewer', parent)
        self.ready(child)
        self.task('delegate this')
        self.until(lambda: self.room.snapshot(self.channel)['messages'][-1]['body'] == 'Parent summary')
        snapshot = self.room.snapshot(self.channel, viewer_address='127.0.0.1')
        self.assertEqual([m['sender'] for m in snapshot['messages']], [parent, child, 'user', parent, child, parent])
        self.assertEqual([p['usage']['total'] for p in snapshot['participants']], [42, 12])
        self.assertEqual(snapshot['participants'][1]['parent_id'], parent)
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
        self.assertEqual(self.room.snapshot(self.channel)['total_messages'], 3)

    def test_requires_auth_and_rejects_external_tasks_and_cross_channel_parent(self):
        error = ApiError('sign in', 401, key='managedAuthRequired')
        with patch.object(managed_cli, 'require_login', side_effect=error):
            with self.assertRaises(ApiError) as rejected:
                self.create('codex', 'Coordinator')
            self.assertEqual(rejected.exception.key, 'managedAuthRequired')
            self.assertEqual(self.room.participants(self.channel), [])
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


class ManagedProcessTest(unittest.TestCase):
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
