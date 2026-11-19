from base64 import b64encode
from io import BytesIO
import json
import os
from pathlib import Path
from threading import Event, Thread
from time import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

import test_network
from relay.board import prepare_post
from relay.chat_files import prepare_chat_files
from relay.cli import Client, RelayError
from relay.mcp import attachment_content
from relay.uploads import MAX_FILES, MAX_FILE_BYTES, MIME_TYPES, UPLOAD_TTL, UploadError, prepare_files


class Attachments(unittest.TestCase):
    setUp = test_network.NetworkAccess.setUp
    stop = test_network.NetworkAccess.stop

    def client(self):
        return Client(self.port)

    def inline(self, name, data):
        return {'name': name, 'data': b64encode(data).decode('ascii')}

    def stage(self, name, data):
        return self.client().call('/api/uploads?' + urlencode({'name': name}), raw=BytesIO(data), size=len(data))

    def send(self, attachments):
        return self.client().call('/api/messages', {'sender': 'user', 'recipient': 'codex', 'body': '',
                                                  'request_id': str(uuid4()), 'attachments': attachments})['message']

    def test_chat_and_board_share_exact_extensions_and_case_handling(self):
        self.assertEqual(set(MIME_TYPES), {'.png', '.jpg', '.txt', '.csv', '.xlsx', '.xlsm', '.json', '.xml', '.pdf', '.ppt', '.pptx', '.mp4', '.gif'})
        self.assertEqual(prepare_files([self.inline('slides.ppt', b'')])[0][1], 'application/vnd.ms-powerpoint')
        samples = {'.png': b'\x89PNG\r\n\x1a\n\0\0\0\x0dIHDR', '.jpg': b'\xff\xd8\xff',
                   '.gif': b'GIF89a', '.mp4': b'\0\0\0\x18ftypisom'}
        items = [self.inline('report' + ext.upper(), samples.get(ext, b'data')) for ext in MIME_TYPES]
        chat = prepare_chat_files(items)
        board = prepare_post({'request_id': str(uuid4()), 'title': 'Files', 'body': '', 'attachments': items})[-1]
        self.assertEqual(chat, board)
        self.assertEqual([mime for _, mime, _ in chat], list(MIME_TYPES.values()))
        for name in ('old.md', 'old.webp', 'image.jpeg', 'script.exe', 'file.csv.exe', '../file.txt', r'C:\file.txt'):
            with self.subTest(name=name), self.assertRaises(UploadError):
                prepare_files([self.inline(name, b'data')])

    def test_size_and_count_boundaries_for_both_composers(self):
        file = self.inline('max.txt', b'x' * MAX_FILE_BYTES)
        self.assertEqual(len(prepare_files([file])[0][2]), MAX_FILE_BYTES)
        for method in (lambda files: prepare_chat_files(files), lambda files: prepare_post(
                {'request_id': str(uuid4()), 'title': 'Limit', 'body': '', 'attachments': files})):
            with self.assertRaises(UploadError) as error:
                method([self.inline('large.txt', b'x' * (MAX_FILE_BYTES + 1))])
            self.assertEqual(error.exception.status, 413)
            method([self.inline('small.txt', b'')] * MAX_FILES)
            with self.assertRaises(UploadError):
                method([self.inline('small.txt', b'x')] * (MAX_FILES + 1))

    def test_stage_rejects_invalid_content_and_cleans_failed_transfers(self):
        store = self.server.room.uploads
        for name, data in [('fake.png', b'not a PNG'), ('fake.jpg', b'<html>'), ('fake.gif', b'not GIF'), ('fake.mp4', b'not MP4')]:
            with self.subTest(name=name), self.assertRaises(RelayError) as error:
                self.stage(name, data)
            self.assertEqual(error.exception.status, 415)
        with self.assertRaises(UploadError):
            store.stage(BytesIO(b'short'), 100, 'short.txt')
        self.assertEqual(list(store.root.glob('*.upload')), [])
        with self.assertRaises(UploadError) as error:
            store.stage(BytesIO(), MAX_FILE_BYTES + 1, 'large.txt')
        self.assertEqual(error.exception.status, 413)

    def test_generic_downloads_and_mcp_reading_preserve_bytes(self):
        original = b'%PDF-1.7\n\xff\x00binary data'
        message = self.send([self.stage('보고서.pdf', original)])
        attachment_id = message['attachments'][0]['id']
        route = '/api/messages/attachment?' + urlencode({'id': attachment_id, 'agent': 'codex'})
        mime, data = self.client().call(route, binary=True)
        self.assertEqual((mime, data), ('application/pdf', original))
        request = test_network.HTTPConnection('127.0.0.1', self.port, timeout=2)
        try:
            request.request('GET', route)
            response = request.getresponse()
            self.assertTrue(response.getheader('Content-Disposition').startswith('attachment;'))
            self.assertEqual(response.read(), original)
        finally:
            request.close()
        with patch('relay.mcp.PROJECT', Path(self.temporary.name)):
            content = attachment_content(self.client(), route)
        info = json.loads(content['content'][0]['text'])
        self.assertEqual(Path(info['local_path']).read_bytes(), original)
        self.assertTrue(info['download_url'].endswith('&download=1'))
        self.assertEqual(self.server.room.uploads.records, {})

    def test_upload_tokens_commit_once_and_retries_work_after_cleanup(self):
        attachment = self.stage('data.json', b'{"ready": true}')
        payload = {'sender': 'user', 'recipient': 'codex', 'body': '', 'request_id': str(uuid4()), 'attachments': [attachment]}
        first = self.client().call('/api/messages', payload)
        again = self.client().call('/api/messages', payload)
        self.assertEqual(first['message']['id'], again['message']['id'])
        self.assertTrue(again['duplicate'])
        self.assertEqual(list(self.server.room.uploads.root.glob('*.upload')), [])
        board = {'request_id': str(uuid4()), 'title': 'Files', 'body': '', 'attachments': [self.stage('data.xml', b'<data/>')]}
        first = self.client().call('/api/board', board)
        again = self.client().call('/api/board', board)
        self.assertEqual(first['id'], again['id'])
        mime, body = self.client().call('/api/board/attachment?' + urlencode({'id': first['images'][0]['id']}), binary=True)
        self.assertEqual((mime, body), ('application/xml', b'<data/>'))

    def test_thirty_full_size_files_commit_without_one_large_json_request(self):
        source = Path(self.temporary.name) / 'max.txt'
        source.write_bytes(b'x' * MAX_FILE_BYTES)
        client = self.client()
        staged = client.upload_files([str(source)] * MAX_FILES)
        message = self.send(staged)
        self.assertEqual(len(message['attachments']), MAX_FILES)
        self.assertEqual(sum(file['size_bytes'] for file in message['attachments']), MAX_FILES * MAX_FILE_BYTES)
        self.assertEqual(list(self.server.room.uploads.root.glob('*.upload')), [])
        files = [self.stage(f'{index}.csv', b'a,b\n1,2') for index in range(MAX_FILES)]
        post = client.call('/api/board', {'request_id': str(uuid4()), 'title': 'Thirty', 'body': '', 'attachments': files})
        self.assertEqual(len(post['images']), MAX_FILES)

    def test_uploading_does_not_lock_chat_and_expired_files_are_removed(self):
        entered, finish = Event(), Event()
        class SlowStream(BytesIO):
            def read(self, size):
                entered.set()
                finish.wait(3)
                return super().read(size)
        store = self.server.room.uploads
        pending = []
        thread = Thread(target=lambda: pending.append(store.stage(SlowStream(b'x'), 1, 'slow.txt')))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.client().call('/api/messages', {'sender': 'user', 'recipient': 'codex', 'body': 'Responsive'}, timeout=1)
        finally:
            finish.set()
            thread.join(3)
        token = pending[0]['upload_id']
        path = store.records[token][2]
        os.utime(path, (time() - UPLOAD_TTL - 1,) * 2)
        with self.assertRaises(UploadError):
            store.resolve(token)
        store.last_cleanup = 0
        store.cleanup()
        self.assertFalse(path.exists())
        self.assertNotIn(token, store.records)

    def test_cli_replies_use_the_same_policy_and_legacy_data_stays_readable(self):
        client = self.client()
        initial = client.call('/api/messages', {'sender': 'user', 'recipient': 'codex', 'body': 'Send a file'})['message']
        source = Path(self.temporary.name) / 'result.xlsx'
        source.write_bytes(b'PK\x03\x04example')
        result = client.reply('codex', initial['id'], '', False, attachments=[str(source)])
        self.assertEqual(result['message']['attachments'][0]['mime_type'], MIME_TYPES['.xlsx'])
        with self.assertRaises(RelayError):
            client.upload_files([str(source)] * (MAX_FILES + 1))
        room = self.server.room
        with room.changed, room.db:
            room.chat_files.save(initial['id'], [('old.md', 'text/plain', b'# Existing attachment')])
        old = room.snapshot('general')['messages'][0]['attachments'][0]
        self.assertEqual(client.call('/api/messages/attachment?id=' + str(old['id']), binary=True)[1], b'# Existing attachment')
