from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from relay.server import RequestError, Room
from relay.settings import DEFAULT_SETTINGS, Settings


class ChannelManagement(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.room = Room(self.root / 'room.sqlite3')
        self.addCleanup(lambda: self.room.db.close())

    def post(self, channel_id, body='A task', **extra):
        participants = self.room.participants(channel_id)
        recipient = participants[0]['id'] if participants else self.room.register(
            {'role': 'codex', 'name': 'Worker', 'channel_id': channel_id})['id']
        return self.room.post(dict(sender='user', recipient=recipient, body=body,
                                   channel_id=channel_id, **extra))['message']

    def test_rename_preserves_identity_history_and_custom_default_name(self):
        channel = self.room.create_channel({'name': 'Original'})
        message = self.post(channel['id'])
        renamed = self.room.rename_channel({'channel_id': channel['id'], 'name': '  New   name  '})
        self.assertEqual(renamed['name'], 'New name')
        self.assertEqual(renamed['folder'], channel['folder'])
        self.assertEqual(self.room.snapshot(channel['id'])['messages'][0]['id'], message['id'])
        self.assertTrue(self.room.snapshot('general')['channel']['default_name'])
        self.room.rename_channel({'channel_id': 'general', 'name': 'My workspace'})
        self.room.db.close()
        self.room = Room(self.root / 'room.sqlite3')
        default = self.room.snapshot('general')['channel']
        self.assertEqual(default['name'], 'My workspace')
        self.assertFalse(default['default_name'])

    def test_activity_counts_unanswered_recipients_and_handoffs_across_channels(self):
        other = self.room.create_channel({'name': 'Other'})['id']
        own = self.room.post({'sender': 'user', 'recipient': 'all', 'body': 'Work', 'channel_id': 'general'})['message']
        theirs = self.post(other)
        def counts():
            return {channel['id']: channel['pending'] for channel in self.room.snapshot(None)['channels']}
        self.assertEqual(counts(), {'general': 2, other: 1})
        self.room.post({'sender': 'codex', 'reply_to': own['id'], 'body': 'Done'})
        self.assertEqual(counts()['general'], 1)
        handoff = self.room.post({'sender': 'claude', 'reply_to': own['id'], 'body': 'Please review', 'next_agent': 'codex'})['message']
        self.assertEqual(counts()['general'], 1)
        self.room.post({'sender': 'codex', 'reply_to': handoff['id'], 'body': 'Reviewed'})
        self.assertEqual(counts(), {'general': 0, other: 1})
        self.room.db.close()
        self.room = Room(self.root / 'room.sqlite3')
        self.assertEqual(counts(), {'general': 0, other: 1})
        self.room.post({'sender': theirs['recipient'], 'reply_to': theirs['id'], 'body': 'Finished'})
        self.assertEqual(counts(), {'general': 0, other: 0})

    def test_activity_ignores_notices_boards_and_removed_participants_and_clears_with_chat(self):
        self.room.announce_connection({'agent': 'codex', 'request_id': str(uuid4()), 'language': 'ko'})
        self.room.publish_board_post({'request_id': str(uuid4()), 'title': 'Instructions', 'body': 'For later'})
        self.assertEqual(self.room.channels()[0]['pending'], 0)
        self.post('general')
        self.room.remove_participant('codex')
        self.assertEqual(self.room.channels()[0]['pending'], 0)
        self.post('general')
        self.assertEqual(self.room.channels()[0]['pending'], 1)
        self.room.clear_channel('general')
        self.assertEqual(self.room.channels()[0]['pending'], 0)

    def test_invalid_or_duplicate_names_do_not_modify_channel(self):
        channel = self.room.create_channel({'name': 'Review'})
        for name in ('', 'x' * 33, 'reVIEW'):
            with self.subTest(name=name), self.assertRaises(RequestError):
                self.room.rename_channel({'channel_id': 'general', 'name': name})
        self.assertTrue(self.room.snapshot('general')['channel']['default_name'])
        self.assertEqual(self.room.rename_channel({'channel_id': channel['id'], 'name': 'Review'})['name'], 'Review')
        with self.assertRaises(RequestError) as error:
            self.room.rename_channel({'channel_id': 'missing', 'name': 'Valid'})
        self.assertEqual(error.exception.status, 404)

    def test_delete_clears_deliveries_and_attachments_but_preserves_other_data(self):
        channel = self.room.create_channel({'name': 'Disposable'})
        channel = self.room.set_folder({'channel_id': channel['id'], 'folder': str(self.root / 'custom files')})
        shared = Path(channel['shared']) / 'keep.md'
        shared.write_text('Keep this shared file.', encoding='utf-8')
        removed = self.post(channel['id'], attachments=[{'name': 'task.txt', 'data': b64encode(b'task').decode()}])
        kept = self.post('general', 'Keep this message')
        self.room.publish_board_post({'request_id': str(uuid4()), 'title': 'Keep instructions', 'body': 'Shared instructions'})
        self.assertEqual(self.room.delete_channel(channel['id'])['cleared'], 1)
        self.assertEqual(shared.read_text(encoding='utf-8'), 'Keep this shared file.')
        self.assertEqual((Path(channel['logs']) / 'chat.jsonl').read_bytes(), b'')
        self.assertEqual(self.room.db.execute('SELECT COUNT(*) FROM deliveries WHERE message_id=?', (removed['id'],)).fetchone()[0], 0)
        self.assertEqual(self.room.db.execute('SELECT COUNT(*) FROM chat_attachments').fetchone()[0], 0)
        self.assertEqual(self.room.snapshot('general')['messages'][0]['id'], kept['id'])
        self.assertEqual(self.room.wait_message('codex', 0)['state'], 'board_post')
        self.room.db.close()
        self.room = Room(self.root / 'room.sqlite3')
        with self.assertRaises(RequestError) as error:
            self.room.snapshot(channel['id'])
        self.assertEqual(error.exception.status, 404)

    def test_default_channel_deletion_survives_restart_and_removes_its_participants(self):
        self.assertEqual([channel['id'] for channel in self.room.channels()], ['general'])
        channel = self.room.snapshot('general')['channel']
        shared = Path(channel['shared']) / 'keep.txt'
        shared.write_text('Keep this file.', encoding='utf-8')
        participants = self.room.participants()
        self.post('general', attachments=[{'name': 'task.txt', 'data': b64encode(b'task').decode()}])
        self.assertEqual(self.room.delete_channel('general')['cleared'], 1)
        self.assertEqual(self.room.channels(), [])
        self.assertEqual(self.room.db.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0], 0)
        self.assertEqual(self.room.db.execute('SELECT COUNT(*) FROM chat_attachments').fetchone()[0], 0)
        self.assertEqual(shared.read_text(encoding='utf-8'), 'Keep this file.')
        self.assertEqual((Path(channel['logs']) / 'chat.jsonl').read_bytes(), b'')
        for _ in range(2):
            self.room.db.close()
            self.room = Room(self.root / 'room.sqlite3')
            self.assertEqual(self.room.channels(), [])
            self.assertEqual(self.room.db.execute('SELECT COUNT(*) FROM participants WHERE removed_at IS NULL').fetchone()[0], 0)
            for participant in participants:
                self.assertEqual(self.room.wait_message(participant['id'], 0)['state'], 'removed')
            snapshot = self.room.snapshot(None)
            self.assertIsNone(snapshot['channel'])
            for key in ('participants', 'archived_participants', 'messages'):
                self.assertEqual(snapshot[key], [])
            self.assertEqual(snapshot['total_messages'], 0)
            with self.assertRaises(RequestError) as error:
                self.post('general')
            self.assertEqual(error.exception.status, 404)
        replacement = self.room.create_channel({'name': 'New workspace'})
        self.assertEqual(self.room.snapshot(replacement['id'])['participants'], [])
        self.post(replacement['id'])
        self.assertEqual(self.room.snapshot(replacement['id'])['total_messages'], 1)
        self.assertEqual(len(self.room.snapshot(replacement['id'])['participants']), 1)

    def test_default_deletion_preserves_other_channels_and_board(self):
        other = self.room.create_channel({'name': 'Keep me'})
        message = self.post(other['id'])
        self.room.publish_board_post({'request_id': str(uuid4()), 'title': 'Instructions', 'body': 'Keep me', 'channel_id': other['id']})
        self.room.delete_channel('general')
        self.room.db.close()
        self.room = Room(self.root / 'room.sqlite3')
        empty = self.room.snapshot(None)
        self.assertIsNone(empty['channel'])
        self.assertEqual([channel['id'] for channel in empty['channels']], [other['id']])
        self.assertEqual(empty['messages'], [])
        self.assertEqual(self.room.snapshot(other['id'])['messages'][0]['id'], message['id'])
        agent = self.room.participants(other['id'])[0]['id']
        self.assertEqual(self.room.wait_message(agent, 0)['state'], 'board_post')

    def test_existing_database_gets_initialization_marker_without_resetting_channels(self):
        self.room.rename_channel({'channel_id': 'general', 'name': 'My workspace'})
        other = self.room.create_channel({'name': 'Existing channel'})
        with self.room.db:
            self.room.db.execute('DROP TABLE workspace_state')
        self.room.db.close()
        self.room = Room(self.root / 'room.sqlite3')
        self.assertEqual(self.room.snapshot('general')['channel']['name'], 'My workspace')
        self.assertEqual(len(self.room.channels()), 2)
        self.room.delete_channel('general')
        self.room.db.close()
        self.room = Room(self.root / 'room.sqlite3')
        self.assertEqual([channel['id'] for channel in self.room.channels()], [other['id']])

    def test_deletion_wakes_a_scoped_receiver(self):
        for channel_id in ('general', self.room.create_channel({'name': 'Waiting'})['id']):
            participants = self.room.participants(channel_id)
            agent = participants[0]['id'] if participants else self.room.register(
                {'role': 'codex', 'name': 'Waiting agent', 'channel_id': channel_id})['id']
            with self.subTest(channel=channel_id), ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self.room.wait_message, agent, 3, channel_id)
                deadline = monotonic() + 2
                while monotonic() < deadline:
                    with self.room.changed:
                        if agent in self.room.waiters:
                            break
                    sleep(0.01)
                self.assertIn(agent, self.room.waiters)
                self.room.delete_channel(channel_id)
                self.assertEqual(pending.result(timeout=1)['state'], 'removed')
            self.assertNotIn(agent, self.room.waiters)


class LocalSettings(unittest.TestCase):
    def test_first_launch_creates_defaults_and_keeps_user_changes(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'bin/settings.ini'
            settings = Settings(path)
            self.assertEqual(settings.port(), 9081)
            self.assertEqual(path.read_text(encoding='utf-8'), DEFAULT_SETTINGS)
            self.assertEqual((ROOT / 'bin/settings.example.ini').read_text(encoding='utf-8'), DEFAULT_SETTINGS)
            path.write_text('[server]\nport = 9182\n\n[ui]\nlanguage = ja\n', encoding='utf-8')
            self.assertEqual(Settings(path).port(), 9182)
            self.assertEqual(Settings(path).language(), 'ja')
            settings.set_language('ko')
            self.assertEqual(settings.port(), 9182)
            self.assertEqual(settings.language(), 'ko')
