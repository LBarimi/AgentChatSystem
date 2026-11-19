from base64 import b64encode
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_network
from relay.errors import ApiError
from relay.presence import ConnectedUsers
from relay.server import Room
from threading import Condition


class ChannelIsolation(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.room = Room(self.root / 'room.sqlite3')
        self.addCleanup(lambda: self.room.db.close())
        self.other = self.room.create_channel({'name': 'Other'})['id']
        self.agent = self.room.register({'role': 'codex', 'name': 'Codex', 'channel_id': self.other})['id']

    def post(self, channel, **extra):
        return self.room.post(dict(sender='user', recipient='all', body='Task', channel_id=channel, **extra))['message']

    def board(self, channel):
        return self.room.publish_board_post({'channel_id': channel, 'request_id': str(uuid4()), 'title': channel,
            'body': 'Private instructions', 'attachments': [{'name': 'guide.txt', 'data': b64encode(channel.encode()).decode()}]})

    def test_participants_broadcast_receiving_and_handoffs_stay_in_channel(self):
        self.assertEqual([p['id'] for p in self.room.participants(self.other)], [self.agent])
        first = self.post('general')
        second = self.post(self.other)
        self.assertEqual(self.room.wait_message('codex', 0)['message']['id'], first['id'])
        self.assertEqual(self.room.wait_message(self.agent, 0)['message']['id'], second['id'])
        for operation in (
            lambda: self.room.wait_message('codex', 0, self.other),
            lambda: self.room.participants(self.other, 'codex'),
            lambda: self.room.post({'sender': 'user', 'recipient': 'codex', 'body': 'Wrong target', 'channel_id': self.other}),
            lambda: self.room.post({'sender': self.agent, 'reply_to': first['id'], 'body': 'Wrong channel'}),
            lambda: self.room.post({'sender': 'codex', 'reply_to': first['id'], 'body': 'Wrong handoff', 'next_agent': self.agent}),
            lambda: self.room.rename({'id': 'codex', 'name': 'Wrong edit', 'channel_id': self.other}),
            lambda: self.room.remove_participant('codex', self.other),
            lambda: self.room.set_participant_icon({'id': 'codex', 'icon': 'bot', 'channel_id': self.other}),
            lambda: self.room.storage_action('list', {'channel_id': self.other, 'agent': 'codex'}),
        ):
            with self.subTest(operation=operation), self.assertRaises(ApiError):
                operation()
        reply = self.room.post({'sender': 'codex', 'reply_to': first['id'], 'body': 'Same channel', 'next_agent': 'claude'})
        self.assertEqual(reply['message']['channel_id'], 'general')
        self.assertEqual(self.room.snapshot(self.other)['total_messages'], 1)

    def test_boards_receipts_revocations_and_attachments_are_scoped(self):
        first, second = self.board('general'), self.board(self.other)
        self.assertEqual([p['id'] for p in self.room.list_board_posts(channel_id=self.other)['posts']], [second['id']])
        self.assertEqual(self.room.wait_message('codex', 0)['post']['id'], first['id'])
        self.assertEqual(self.room.wait_message(self.agent, 0)['post']['id'], second['id'])
        for operation in (
            lambda: self.room.read_board_post(first['id'], self.agent),
            lambda: self.room.read_board_post(first['id'], channel_id=self.other),
            lambda: self.room.delete_board_post(first['id'], self.other),
            lambda: self.room.board_image(first['images'][0]['id'], agent=self.agent),
            lambda: self.room.board_image(first['images'][0]['id'], channel_id=self.other),
        ):
            with self.assertRaises(ApiError):
                operation()
        self.room.read_board_post(second['id'], self.agent)
        self.assertEqual([r['id'] for r in self.room.read_board_post(second['id'], channel_id=self.other)['recipients']], [self.agent])
        self.room.delete_board_post(second['id'], self.other)
        self.assertEqual(self.room.wait_message(self.agent, 0)['state'], 'board_deleted')
        self.assertEqual(self.room.wait_message('codex', 0)['state'], 'board_post')
        self.room.read_board_post(second['id'], self.agent)
        self.assertEqual(self.room.wait_message(self.agent, 0)['state'], 'timeout')

    def test_chat_attachments_and_request_deduplication_do_not_cross_channels(self):
        message = self.room.post({'sender': 'user', 'recipient': 'codex', 'body': 'A only', 'channel_id': 'general',
                                 'attachments': [{'name': 'task.txt', 'data': b64encode(b'channel A').decode()}]})['message']
        attachment = message['attachments'][0]['id']
        self.assertEqual(self.room.chat_attachment(attachment, 'codex')['data'], b'channel A')
        self.assertEqual(self.room.chat_attachment(attachment, 'claude')['data'], b'channel A')
        for kwargs in ({'agent': self.agent}, {'channel_id': self.other}):
            with self.assertRaises(ApiError):
                self.room.chat_attachment(attachment, **kwargs)
        request = str(uuid4())
        post = {'request_id': request, 'title': 'A', 'body': 'Only A', 'channel_id': 'general'}
        self.room.publish_board_post(post)
        with self.assertRaises(ApiError):
            self.room.publish_board_post(post | {'channel_id': self.other})
        self.assertEqual(self.room.list_board_posts(channel_id=self.other)['posts'], [])

    def test_new_participant_receives_existing_instructions_only_from_its_channel(self):
        self.board('general')
        own = self.board(self.other)
        late = self.room.register({'role': 'claude', 'name': 'Late reader', 'channel_id': self.other})['id']
        self.assertEqual(self.room.wait_message(late, 0)['post']['id'], own['id'])
        self.room.read_board_post(own['id'], late)
        self.assertEqual(self.room.wait_message(late, 0)['state'], 'timeout')

    def test_folder_layout_exports_board_files_moves_together_and_cleans_deleted_content(self):
        post = self.board(self.other)
        channel = self.room.snapshot(self.other)['channel']
        for kind in ('shared', 'logs', 'board'):
            self.assertEqual(Path(channel[kind]).parent, Path(channel['folder']))
            self.assertTrue(Path(channel[kind]).is_dir())
        folder = Path(channel['board']) / str(post['id'])
        attachment = folder / f"{post['images'][0]['id']}-guide.txt"
        self.assertEqual(attachment.read_text(), self.other)
        self.assertEqual(json.loads((folder / 'post.json').read_text())['channel_id'], self.other)
        with self.assertRaises(ApiError):
            self.room.set_folder({'channel_id': 'general', 'folder': str(folder)})
        moved = self.room.set_folder({'channel_id': self.other, 'folder': str(self.root / 'custom channel'), 'copy_shared': False})
        copied = Path(moved['board']) / str(post['id']) / attachment.name
        self.assertEqual(copied.read_bytes(), attachment.read_bytes())
        self.room.delete_board_post(post['id'], self.other)
        self.assertFalse(copied.exists())
        self.assertEqual(json.loads((copied.parent / 'post.json').read_text())['status'], 'deleted')
        self.assertEqual(self.room.channel_files(self.other, 'board', '')['entries'][0]['type'], 'folder')

    def test_legacy_global_data_migrates_once_and_cancels_cross_channel_deliveries(self):
        post = self.board('general')
        message = self.post(self.other)
        with self.room.db:
            self.room.db.execute("DELETE FROM workspace_state WHERE key='channel_isolation'")
            self.room.db.execute('DROP INDEX participants_channel')
            self.room.db.execute('ALTER TABLE participants DROP COLUMN channel_id')
            self.room.db.execute('DROP INDEX board_by_channel')
            self.room.db.execute('ALTER TABLE board_posts DROP COLUMN channel_id')
        self.room.db.close()
        self.room = Room(self.root / 'room.sqlite3')
        self.assertEqual(self.room.participants(self.other), [])
        self.assertEqual(len(self.room.participants('general')), 3)
        self.assertEqual(self.room.snapshot(self.other)['messages'][0]['id'], message['id'])
        self.assertEqual(self.room.db.execute('SELECT COUNT(*) FROM deliveries WHERE message_id=?', (message['id'],)).fetchone()[0], 0)
        self.assertEqual(self.room.read_board_post(post['id'])['channel_id'], 'general')
        new = self.room.register({'role': 'claude', 'name': 'Independent', 'channel_id': self.other})
        self.room.db.close()
        self.room = Room(self.root / 'room.sqlite3')
        self.assertEqual([p['id'] for p in self.room.participants(self.other)], [new['id']])

    def test_migration_preserves_old_board_when_all_channels_were_deleted(self):
        post = self.board('general')
        with self.room.db:
            self.room.db.execute('DELETE FROM channels')
            self.room.db.execute("DELETE FROM workspace_state WHERE key='channel_isolation'")
        self.room.db.close()
        self.room = Room(self.root / 'room.sqlite3')
        recovered, = self.room.channels()
        self.assertNotEqual(recovered['id'], 'general')
        self.assertEqual(self.room.read_board_post(post['id'], channel_id=recovered['id'])['body'], 'Private instructions')

    def test_failed_board_cleanup_cannot_serve_stale_exported_attachments(self):
        post = self.board(self.other)
        relative = f"{post['id']}/{post['images'][0]['id']}-guide.txt"
        with patch.object(self.room.storage, 'export_board', side_effect=OSError('File in use')):
            with self.assertRaises(OSError):
                self.room.delete_board_post(post['id'], self.other)
            with self.assertRaises(OSError):
                self.room.channel_files(self.other, 'board', relative, download=True)
        self.room.delete_board_post(post['id'], self.other)
        with self.assertRaises(ApiError):
            self.room.channel_files(self.other, 'board', relative, download=True)

    def test_channel_delete_rollback_rebuilds_board_exports_from_preserved_database(self):
        post = self.board(self.other)
        with patch.object(self.room.storage, 'log', side_effect=OSError('Log file in use')):
            with self.assertRaises(OSError):
                self.room.delete_channel(self.other)
        self.assertEqual(self.room.participants(self.other)[0]['id'], self.agent)
        self.assertEqual(self.room.read_board_post(post['id'], channel_id=self.other)['body'], 'Private instructions')
        relative = f"{post['id']}/{post['images'][0]['id']}-guide.txt"
        handle, _ = self.room.channel_files(self.other, 'board', relative, download=True)
        with handle:
            self.assertEqual(handle.read(), self.other.encode())


class IsolationHTTP(unittest.TestCase):
    setUp = test_network.NetworkAccess.setUp
    stop = test_network.NetworkAccess.stop
    call = test_network.NetworkAccess.call

    def test_api_and_mcp_scope_cannot_read_other_channel_ids(self):
        room = self.server.room
        channel = room.create_channel({'name': 'B'})['id']
        agent = room.register({'role': 'codex', 'name': 'B agent', 'channel_id': channel})['id']
        post = room.publish_board_post({'request_id': str(uuid4()), 'title': 'A only', 'body': 'A',
                                       'attachments': [{'name': 'A.txt', 'data': b64encode(b'A').decode()}]})
        host = f'127.0.0.1:{self.port}'
        status, result = self.call('/api/channels?agent=' + agent, host=host)
        self.assertEqual(status, 200)
        self.assertEqual([c['id'] for c in result['channels']], [channel])
        status, result = self.call('/api/participants?agent=' + agent, host=host)
        self.assertEqual([p['id'] for p in result['participants']], [agent])
        self.assertEqual(self.call('/api/board?agent=' + agent, host=host)[1]['posts'], [])
        for route in (f"/api/board/post?id={post['id']}&channel={channel}",
                      f"/api/board/attachment?id={post['images'][0]['id']}&agent={agent}",
                      f'/api/participants?agent={agent}&channel=general'):
            self.assertEqual(self.call(route, host=host)[0], 404)
        from relay.mcp import dispatch
        from relay.cli import Client
        result = dispatch(Client(self.port), agent, {'method': 'tools/call', 'params': {'name': 'channels', 'arguments': {}}})
        self.assertEqual([c['id'] for c in json.loads(result['content'][0]['text'])['channels']], [channel])


class PresenceIsolation(unittest.TestCase):
    def test_tab_channel_switch_and_stale_disconnect_do_not_leak_presence(self):
        changed = Condition()
        users = ConnectedUsers(changed, lambda: changed.notify_all())
        with patch('relay.presence.computer_name', return_value='TEST-PC'):
            old = users.open_session('127.0.0.1', 'A', 'tab-one')
            other_tab = users.open_session('127.0.0.1', 'A', 'tab-two')
            moved = users.open_session('127.0.0.1', 'B', 'tab-one')
            self.assertEqual(len(users.snapshot('A')), 1)
            self.assertEqual(len(users.snapshot('B')), 1)
            users.close_session(old)
            self.assertEqual(len(users.snapshot('B')), 1)
            users.close_session(other_tab)
            self.assertEqual(users.snapshot('A'), [])
            users.close_session(moved)
            self.assertEqual(users.snapshot('B'), [])
