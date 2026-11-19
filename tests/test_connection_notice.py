import json
from pathlib import Path
from uuid import uuid4
import unittest

import test_network
from relay.cli import Client, RelayError
from relay.mcp import dispatch
from relay.prompts import prompt_catalog
from relay.server import RequestError, Room
from relay.settings import LANGUAGES


class ConnectionNotice(unittest.TestCase):
    setUp = test_network.NetworkAccess.setUp
    stop = test_network.NetworkAccess.stop

    def announce(self, bound_agent='codex', request_id=None, language='ko', **arguments):
        result = dispatch(Client(self.port), bound_agent, {'method': 'tools/call', 'params': {
            'name': 'announce_connection', 'arguments': {
                'request_id': request_id or str(uuid4()), 'language': language, **arguments,
            },
        }})
        self.assertNotIn('isError', result)
        return json.loads(result['content'][0]['text'])

    def test_notice_uses_agent_name_and_channel_without_creating_inbox_work(self):
        room = self.server.room
        channel = room.create_channel({'name': 'Listening'})['id']
        agent = room.register({'role': 'claude', 'name': 'Listener', 'channel_id': channel})['id']
        result = self.announce(agent, channel_id='general', body='Ignored', agent='codex')
        message = result['message']
        self.assertEqual(message['sender'], agent)
        self.assertEqual(message['channel_id'], channel)
        self.assertEqual(message['body'], '접속되었습니다.')
        self.assertIsNone(message['recipient'])
        self.assertIsNone(message['reply_to'])
        own = room.snapshot(channel)
        self.assertEqual(own['participants'][0]['name'], 'Listener')
        self.assertEqual(own['participants'][0]['pending'], 0)
        self.assertEqual(own['messages'], [message])
        self.assertEqual(room.snapshot('general')['messages'], [])
        log = Path(own['channel']['logs']) / 'chat.jsonl'
        self.assertEqual(json.loads(log.read_text(encoding='utf-8'))['body'], message['body'])
        for _ in range(2):
            self.assertEqual(room.wait_message(agent, 0)['state'], 'timeout')
        self.assertEqual(room.snapshot(channel)['total_messages'], 1)

    def test_retries_survive_restart_and_new_listening_run_gets_a_new_notice(self):
        room = self.server.room
        token = str(uuid4())
        first = self.announce(request_id=token)
        self.assertFalse(first['duplicate'])
        repeat = self.announce(request_id=token)
        self.assertTrue(repeat['duplicate'])
        self.assertEqual(repeat['message']['id'], first['message']['id'])
        room.db.close()
        self.server.room = Room(Path(self.temporary.name) / 'room.sqlite3')
        self.assertTrue(self.announce(request_id=token)['duplicate'])
        second = self.announce()
        self.assertNotEqual(first['message']['id'], second['message']['id'])
        self.assertEqual(self.server.room.snapshot('general')['total_messages'], 2)

    def test_languages_and_invalid_requests(self):
        for language in LANGUAGES:
            with self.subTest(language=language):
                self.assertEqual(self.announce(language=language)['message']['body'], prompt_catalog(language)['connected_notice'])
        client = Client(self.port)
        for payload in (
            {'agent': 'codex', 'request_id': 'not-a-uuid'},
            {'agent': 'codex', 'request_id': str(uuid4()), 'language': 'unsupported'},
            {'request_id': str(uuid4())},
        ):
            with self.subTest(payload=payload), self.assertRaises(RelayError):
                client.call('/api/participants/connected', payload)

    def test_other_agent_or_channel_cannot_reuse_request_or_post_after_removal(self):
        room = self.server.room
        channel = room.create_channel({'name': 'Other'})['id']
        agent = room.register({'role': 'codex', 'name': 'Other', 'channel_id': channel})['id']
        token = str(uuid4())
        self.announce(request_id=token)
        for payload in (
            {'agent': 'claude', 'request_id': token},
            {'agent': agent, 'request_id': token},
            {'agent': 'codex', 'request_id': str(uuid4()), 'channel_id': channel},
        ):
            with self.subTest(payload=payload), self.assertRaises(RequestError):
                room.announce_connection(payload)
        room.remove_participant('codex')
        with self.assertRaises(RequestError):
            room.announce_connection({'agent': 'codex', 'request_id': str(uuid4())})
