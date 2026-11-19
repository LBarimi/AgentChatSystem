from email.message import Message
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace
import unittest
from urllib.parse import urlencode, unquote
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from relay.cli import Client
from relay.server import Handler, LocalHTTPServer, RequestError, Room
from relay.settings import LISTEN_HOST, Settings


class NetworkAccess(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.server = LocalHTTPServer((LISTEN_HOST, 0), Handler)
        self.server.room = Room(root / 'room.sqlite3')
        self.server.settings = Settings(root / 'settings.ini')
        self.port = self.server.server_port
        self.thread = Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.server.room.db.close()

    def call(self, route, *, host=None, origin=None, payload=None, headers=()):
        connection = HTTPConnection('127.0.0.1', self.port, timeout=2)
        try:
            connection.putrequest('POST' if payload is not None else 'GET', route, skip_host=True)
            if host is not None:
                connection.putheader('Host', host)
            if origin is not None:
                connection.putheader('Origin', origin)
            for key, value in headers:
                connection.putheader(key, value)
            body = None
            if payload is not None:
                body = json.dumps(payload).encode()
                connection.putheader('Content-Type', 'application/json')
                connection.putheader('Content-Length', str(len(body)))
                connection.putheader('X-Local-Relay', '1')
            connection.endheaders(body)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_bind_and_local_cli_connection(self):
        health = Client(self.port).call('/api/health')
        self.assertEqual(health['listen_host'], '0.0.0.0')
        self.assertEqual(health['service'], 'AgentChatSystem')
        self.assertEqual(self.server.server_address[0], '0.0.0.0')

    def test_default_channel_delete_and_empty_workspace_api(self):
        host = f'127.0.0.1:{self.port}'
        status, initial = self.call('/api/room', host=host)
        self.assertEqual(status, 200)
        self.assertEqual(initial['channel']['id'], 'general')
        status, deleted = self.call('/api/channels/delete', host=host, payload={'channel_id': 'general'})
        self.assertEqual(status, 200)
        self.assertTrue(deleted['deleted'])
        for route in ('/api/room', '/api/room?channel=general', '/api/room?channel=missing'):
            self.assertEqual(self.call(route, host=host)[0], 404)
        status, empty = self.call('/api/room?channel=', host=host)
        self.assertEqual(status, 200)
        self.assertIsNone(empty['channel'])
        for key in ('channels', 'participants', 'archived_participants', 'messages'):
            self.assertEqual(empty[key], [])
        status, channel = self.call('/api/channels', host=host, payload={'name': 'New channel'})
        self.assertEqual(status, 201)
        status, empty = self.call('/api/room?channel=', host=host)
        self.assertEqual(status, 200)
        self.assertIsNone(empty['channel'])
        self.assertEqual(empty['channels'][0]['id'], channel['id'])
        status, selected = self.call('/api/room?channel=' + channel['id'], host=host)
        self.assertEqual(status, 200)
        self.assertEqual(selected['participants'], [])

    def test_legacy_resume_settings_do_not_block_manual_mcp_delivery(self):
        root = Path(self.temporary.name)
        with self.server.room.db as db:
            db.execute('CREATE TABLE agent_automation (agent_id TEXT, session_id TEXT, cwd TEXT, transcript TEXT, enabled INTEGER)')
            db.execute('INSERT INTO agent_automation VALUES (?,?,?,?,1)', ('codex', 'old-session', str(root), 'old.jsonl'))
            for column in ('received_at', 'received_by'):
                db.execute(f'ALTER TABLE deliveries ADD COLUMN {column} TEXT')
        self.server.room.db.close()
        self.server.room = Room(root / 'room.sqlite3')
        client = Client(self.port)
        status, _ = self.call('/api/participants/automation', host=f'127.0.0.1:{self.port}',
                              payload={'id': 'codex', 'enabled': True})
        self.assertEqual(status, 404)
        self.assertTrue(all('automation' not in item for item in client.call('/api/participants')['participants']))
        for language in ('en', 'ko', 'ja', 'zh-CN', 'zh-TW', 'ru', 'de'):
            setup = client.call('/api/setup?agent=codex&lang=' + language)
            self.assertNotIn('automation', setup)
            self.assertNotIn('managed', setup['prompt'])
            self.assertIn('receive(seconds=40)', setup['prompt'])
        message = client.call('/api/messages', {'sender': 'user', 'recipient': 'codex', 'body': 'Manual task'})['message']
        event = client.call('/api/wait?agent=codex&seconds=0')
        self.assertEqual(event['state'], 'message')
        self.assertEqual(event['message']['id'], message['id'])
        client.call('/api/messages', {'sender': 'codex', 'reply_to': message['id'], 'body': 'Completed manually'})
        self.assertEqual(client.call('/api/wait?agent=codex&seconds=0')['state'], 'timeout')
        post = self.server.room.publish_board_post({'request_id': str(uuid4()), 'title': 'Instructions', 'body': 'Read manually'})
        event = client.call('/api/wait?agent=codex&seconds=0')
        self.assertEqual(event['state'], 'board_post')
        self.assertEqual(event['post']['id'], post['id'])
        self.server.room.read_board_post(post['id'], 'codex')
        self.server.room.delete_board_post(post['id'])
        self.assertEqual(client.call('/api/wait?agent=codex&seconds=0')['state'], 'board_deleted')
        self.server.room.read_board_post(post['id'], 'codex')
        self.assertEqual(client.call('/api/wait?agent=codex&seconds=0')['state'], 'timeout')
        self.server.room.remove_participant('codex')
        self.assertEqual(client.call('/api/wait?agent=codex&seconds=0')['state'], 'removed')

    def test_numeric_lan_hosts_can_read_and_write_with_matching_origin(self):
        for address in ('127.0.0.1', 'localhost', '192.168.1.20', '172.31.250.20', '10.20.30.40'):
            with self.subTest(address=address):
                host = f'{address}:{self.port}'
                status, _ = self.call('/api/health', host=host)
                self.assertEqual(status, 200)
                status, result = self.call('/api/channels', host=host, origin=f'http://{host}',
                                           payload={'name': address})
                self.assertEqual(status, 201)
                self.assertEqual(result['name'], address)

    def test_invalid_hosts_and_cross_origin_requests_are_rejected(self):
        valid_host = f'192.168.1.20:{self.port}'
        cases = [
            {'host': None},
            {'host': '192.168.1.20'},
            {'host': f'192.168.1.20:{self.port + 1}'},
            {'host': f'example.com:{self.port}'},
            {'host': f'8.8.8.8:{self.port}'},
            {'host': f'0.0.0.0:{self.port}'},
            {'host': valid_host, 'origin': 'null'},
            {'host': valid_host, 'origin': 'https://example.com'},
            {'host': valid_host, 'origin': f'http://10.0.0.1:{self.port}'},
            {'host': valid_host, 'origin': f'http://192.168.1.20:{self.port + 1}'},
            {'host': valid_host, 'headers': [('Host', valid_host)]},
            {'host': valid_host, 'origin': f'http://{valid_host}',
             'headers': [('Origin', f'http://{valid_host}')]},
        ]
        for case in cases:
            with self.subTest(case=case):
                status, error = self.call('/api/channels', payload={'name': 'Blocked'}, **case)
                self.assertEqual(status, 403)
                self.assertEqual(error['error_key'], 'errorNetworkAccess')
        self.assertEqual(len(self.server.room.channels()), 1)

    def test_public_peers_cannot_spoof_a_private_host_or_forwarding_header(self):
        request = Handler.__new__(Handler)
        request.server = SimpleNamespace(server_port=self.port)
        request.headers = Message()
        request.headers['Host'] = f'192.168.1.20:{self.port}'
        request.headers['X-Forwarded-For'] = '192.168.1.21'
        for peer in ('8.8.8.8', '172.15.0.1', '172.32.0.1', '169.254.1.1', '100.64.1.1', '0.0.0.0', '::1'):
            with self.subTest(peer=peer):
                request.client_address = (peer, 12345)
                with self.assertRaises(RequestError) as rejected:
                    request._check_local()
                self.assertEqual(rejected.exception.status, 403)
        for peer in ('127.0.0.1', '10.0.0.1', '172.16.0.1', '172.31.255.254', '192.168.255.254'):
            with self.subTest(peer=peer):
                request.client_address = (peer, 12345)
                request._check_local()
                if peer == '127.0.0.1':
                    request._check_local(files_write=True)
                else:
                    with self.assertRaises(RequestError) as rejected:
                        request._check_local(files_write=True)
                    self.assertEqual(rejected.exception.key, 'errorFilesLocalOnly')

    def test_default_http_port_allows_browser_host_without_port(self):
        request = Handler.__new__(Handler)
        request.server = SimpleNamespace(server_port=80)
        request.client_address = ('192.168.1.21', 12345)
        request.headers = Message()
        request.headers['Host'] = '192.168.1.20'
        request.headers['Origin'] = 'http://192.168.1.20'
        request._check_local()

    def download(self, **params):
        connection = HTTPConnection('127.0.0.1', self.port, timeout=2)
        try:
            connection.request('GET', '/api/files/download?' + urlencode(params),
                               headers={'Host': f'192.168.1.20:{self.port}'})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_downloads_binary_unicode_names_and_logs_without_text_size_limit(self):
        root = Path(self.server.room.channels()[0]['folder'])
        nested = root / 'shared' / 'reports'
        nested.mkdir()
        data = bytes(range(256)) * 4096
        (nested / '결과 #1.bin').write_bytes(data)
        params = {'channel': 'general', 'kind': 'shared', 'path': 'reports/결과 #1.bin'}
        status, headers, actual = self.download(**params)
        self.assertEqual(status, 200)
        self.assertEqual(actual, data)
        self.assertEqual(int(headers['Content-Length']), len(data))
        self.assertEqual(headers['Content-Type'], 'application/octet-stream')
        self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
        self.assertIn('결과 #1.bin', unquote(headers['Content-Disposition']))
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        status, _, actual = self.download(channel='general', kind='logs', path='chat.jsonl')
        self.assertEqual(status, 200)
        self.assertEqual(actual, (root / 'logs' / 'chat.jsonl').read_bytes())

    def test_file_browser_pagination_and_nested_directories(self):
        root = Path(self.server.room.channels()[0]['folder']) / 'shared'
        for number in range(205):
            (root / f'{number:03}.txt').write_text(str(number), encoding='utf-8')
        (root / 'nested').mkdir()
        (root / 'nested' / 'one.txt').write_text('one', encoding='utf-8')
        host = f'192.168.1.20:{self.port}'
        status, first = self.call('/api/files?channel=general&kind=shared', host=host)
        self.assertEqual(status, 200)
        self.assertEqual(len(first['entries']), 200)
        self.assertTrue(first['truncated'])
        self.assertEqual(first['entries'][0], {'path': 'nested', 'type': 'folder'})
        status, last = self.call('/api/files?channel=general&offset=200', host=host)
        self.assertEqual(status, 200)
        self.assertEqual(len(last['entries']), 6)
        self.assertFalse(last['truncated'])
        combined = first['entries'] + last['entries']
        self.assertEqual(len({entry['path'] for entry in combined}), 206)
        self.assertEqual([entry['path'] for entry in combined[1:]], [f'{number:03}.txt' for number in range(205)])
        status, nested = self.call('/api/files?channel=general&path=nested', host=host)
        self.assertEqual(status, 200)
        self.assertEqual(nested['entries'], [{'path': 'nested/one.txt', 'type': 'file', 'bytes': 3}])

    def test_downloads_cannot_escape_channel_folders(self):
        for path in ('../room.sqlite3', '../../settings.ini', r'..\..\settings.ini',
                     '/etc/passwd', 'C:/Windows/win.ini', r'\\server\share\file',
                     'nested/../../room.sqlite3', 'file:stream', 'con', '.', ''):
            with self.subTest(path=path):
                status, _, _ = self.download(channel='general', kind='shared', path=path)
                self.assertEqual(status, 400)
        for params, expected in [
            ({'kind': '..', 'path': 'room.sqlite3'}, 400),
            ({'channel': 'missing', 'path': 'file.txt'}, 404),
            ({'kind': 'shared', 'path': 'missing.txt'}, 404),
        ]:
            with self.subTest(params=params):
                status, _, _ = self.download(**params)
                self.assertEqual(status, expected)

    def test_symlinks_are_neither_listed_nor_downloaded(self):
        root = Path(self.server.room.channels()[0]['folder']) / 'shared'
        secret = Path(self.temporary.name) / 'outside.txt'
        secret.write_text('outside channel', encoding='utf-8')
        link = root / 'link.txt'
        try:
            link.symlink_to(secret)
        except OSError:
            self.skipTest('Creating symlinks requires additional Windows privileges')
        status, listing = self.call('/api/files', host=f'127.0.0.1:{self.port}')
        self.assertEqual(status, 200)
        self.assertEqual(listing['entries'], [])
        status, _, _ = self.download(path='link.txt')
        self.assertEqual(status, 409)
