from http.client import HTTPConnection
import json
from pathlib import Path
import sqlite3
from time import monotonic
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_network
from relay.cli import Client
from relay.server import RequestError, Room


class UserMessages(unittest.TestCase):
    setUp = test_network.NetworkAccess.setUp
    stop = test_network.NetworkAccess.stop

    def known_name(self, address, name):
        self.server.room.users.cache[address] = (monotonic() + 300, name)

    def test_http_peer_is_used_instead_of_forwarded_headers_or_payload(self):
        self.known_name('127.0.0.1', 'MY-PC')
        connection = HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            payload = {'sender': 'user', 'recipient': 'all', 'body': 'Hello',
                       'user_ip': '192.168.1.99', 'user_name': 'Spoofed', 'viewer': {'ip': '192.168.1.99'}}
            connection.request('POST', '/api/messages', json.dumps(payload), {
                'Content-Type': 'application/json', 'X-Local-Relay': '1', 'X-Forwarded-For': '192.168.1.99',
            })
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            message = json.load(response)['message']
            self.assertEqual((message['user_ip'], message['user_name']), ('127.0.0.1', 'MY-PC'))
        finally:
            connection.close()
        client = Client(self.port)
        snapshot = client.call('/api/room?viewer=192.168.1.99')
        self.assertEqual(snapshot['viewer'], {'ip': '127.0.0.1', 'name': 'MY-PC'})
        self.assertEqual(snapshot['users'], [])  # A normal HTTP read is not a browser presence connection.
        delivery = client.call('/api/wait?agent=codex&seconds=0')['message']
        self.assertEqual(delivery['user_name'], 'MY-PC')
        reply = client.call('/api/messages', {'sender': 'codex', 'reply_to': message['id'], 'body': 'Done',
                                              'user_ip': '127.0.0.1', 'user_name': 'Spoofed'})['message']
        self.assertIsNone(reply['user_ip'])
        self.assertIsNone(reply['user_name'])

    def test_two_users_have_per_viewer_identity_and_saved_names_across_restarts(self):
        room = self.server.room
        for address, name in [('192.168.1.20', 'ALICE-PC'), ('192.168.1.21', 'BOB-PC')]:
            self.known_name(address, name)
            room.post({'sender': 'user', 'recipient': 'all', 'body': name}, user_address=address)
            self.assertEqual(room.snapshot('general', viewer_address=address)['viewer'], {'ip': address, 'name': name})
        self.assertIsNone(room.snapshot('general')['viewer'])
        room.db.close()
        self.server.room = Room(Path(self.temporary.name) / 'room.sqlite3')
        messages = self.server.room.snapshot('general')['messages']
        self.assertEqual([(item['user_ip'], item['user_name']) for item in messages],
                         [('192.168.1.20', 'ALICE-PC'), ('192.168.1.21', 'BOB-PC')])
        log = Path(self.server.room.snapshot('general')['channel']['logs']) / 'chat.jsonl'
        entries = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
        self.assertEqual([item['user_name'] for item in entries], ['ALICE-PC', 'BOB-PC'])

    def test_unknown_or_expired_names_fall_back_to_ip_without_network_lookup(self):
        users = self.server.room.users
        users.cache['192.168.1.20'] = (0, 'STALE-PC')
        with patch('relay.presence.socket.gethostbyaddr', side_effect=AssertionError('Blocking DNS lookup')):
            self.assertEqual(users.identity('192.168.1.20'), {'ip': '192.168.1.20', 'name': '192.168.1.20'})
            self.assertEqual(users.identity('192.168.1.21'), {'ip': '192.168.1.21', 'name': '192.168.1.21'})
            with patch('relay.presence.socket.gethostname', return_value='LOCAL-PC'):
                self.assertEqual(users.identity('127.0.0.1')['name'], 'LOCAL-PC')

    def test_retry_keeps_original_author_and_rejects_another_peer_using_same_request_id(self):
        room = self.server.room
        self.known_name('192.168.1.20', 'ALICE-PC')
        payload = {'sender': 'user', 'recipient': 'all', 'body': 'Task', 'request_id': str(uuid4())}
        first = room.post(payload, user_address='192.168.1.20')
        self.known_name('192.168.1.20', 'RENAMED-PC')
        repeat = room.post(payload, user_address='192.168.1.20')
        self.assertTrue(repeat['duplicate'])
        self.assertEqual(repeat['message'], first['message'])
        with self.assertRaises(RequestError) as error:
            room.post(payload, user_address='192.168.1.21')
        self.assertEqual(error.exception.status, 409)

    def test_legacy_messages_are_preserved_without_inventing_an_author(self):
        path = Path(self.temporary.name) / 'legacy.sqlite3'
        db = sqlite3.connect(path)
        db.execute('''CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, sender TEXT NOT NULL,
                      recipient TEXT, body TEXT NOT NULL, reply_to INTEGER, remaining INTEGER NOT NULL,
                      created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00Z')''')
        db.execute("INSERT INTO messages(sender,recipient,body,remaining) VALUES('user','all','Old message',0)")
        db.commit()
        db.close()
        room = Room(path)
        self.addCleanup(room.db.close)
        message = room.snapshot('general', viewer_address='127.0.0.1')['messages'][0]
        self.assertEqual(message['body'], 'Old message')
        self.assertIsNone(message['user_ip'])
        self.assertIsNone(message['user_name'])
