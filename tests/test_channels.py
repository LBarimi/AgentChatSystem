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
        return self.room.post(dict(sender='user', recipient='codex', body=body,
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
        removed = self.post(channel['id'], attachments=[{'name': 'task.md', 'data': b64encode(b'task').decode()}])
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

    def test_default_channel_cannot_be_deleted(self):
        self.post('general')
        with self.assertRaises(RequestError) as error:
            self.room.delete_channel('general')
        self.assertEqual(error.exception.key, 'errorDefaultChannelDelete')
        self.assertEqual(self.room.snapshot('general')['total_messages'], 1)

    def test_deletion_wakes_a_scoped_receiver(self):
        channel = self.room.create_channel({'name': 'Waiting'})
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.room.wait_message, 'codex', 3, channel['id'])
            deadline = monotonic() + 2
            while monotonic() < deadline:
                with self.room.changed:
                    if 'codex' in self.room.waiters:
                        break
                sleep(0.01)
            self.assertIn('codex', self.room.waiters)
            self.room.delete_channel(channel['id'])
            with self.assertRaises(RequestError) as error:
                pending.result(timeout=1)
            self.assertEqual(error.exception.status, 404)
        self.assertNotIn('codex', self.room.waiters)


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
