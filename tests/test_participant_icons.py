from base64 import b64decode
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sqlite3
import unittest
from urllib.parse import urlencode

import test_network
from relay.cli import Client, RelayError
from relay.server import PARTICIPANT_ICONS, Room
from relay.uploads import MAX_FILE_BYTES, UploadError


PNG = b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=')


class ParticipantIcons(unittest.TestCase):
    setUp = test_network.NetworkAccess.setUp
    stop = test_network.NetworkAccess.stop

    def call(self, route, payload=None, **kwargs):
        return Client(self.port).call(route, payload, **kwargs)

    def stage(self, name, data):
        return self.call('/api/uploads?' + urlencode({'name': name}), raw=BytesIO(data), size=len(data))

    def save(self, icon, *, agent='codex', upload=None):
        return self.call('/api/participants/icon', {'id': agent, 'icon': icon, **(upload or {})})

    def download(self, agent='codex', version=''):
        return self.call('/api/participants/icon?' + urlencode({'agent': agent, 'v': version}), binary=True)

    def test_default_presets_and_idempotent_updates(self):
        setup = self.call('/api/setup?agent=codex')
        self.assertEqual(setup['icon'], 'provider')
        self.assertEqual(setup['icon_options'], list(PARTICIPANT_ICONS))
        self.assertEqual(setup['icon_upload_extensions'], ['.png', '.jpg', '.gif'])
        created = self.call('/api/participants', {'role': 'claude', 'name': 'Designer'})
        self.assertEqual(created['icon'], 'provider')
        for icon in PARTICIPANT_ICONS:
            self.assertEqual(self.save(icon)['icon'], icon)
            revision = self.server.room.revision
            self.save(icon)
            self.assertEqual(self.server.room.revision, revision)
            roster = self.call('/api/room')['participants']
            self.assertEqual(next(agent for agent in roster if agent['id'] == 'codex')['icon'], icon)
        self.assertEqual(self.call('/api/setup?agent=claude')['icon'], 'provider')

    def test_images_download_replace_and_reset(self):
        samples = [('image.PNG', 'image/png', PNG), ('image.jpg', 'image/jpeg', b'\xff\xd8\xff\xe0'),
                   ('image.gif', 'image/gif', b'GIF89a')]
        previous = None
        for name, mime, data in samples:
            staged = self.stage(name, data)
            result = self.save('image', upload=staged)
            digest = sha256(data).hexdigest()
            self.assertEqual(result['icon'], 'image:' + digest)
            self.assertEqual(self.download(version=digest), (mime, data))
            self.assertEqual(list(self.server.room.uploads.root.glob('*.upload')), [])
            revision = self.server.room.revision
            self.save('image', upload=self.stage(name, data))
            self.assertEqual(self.server.room.revision, revision)
            if previous:
                with self.assertRaises(RelayError) as error:
                    self.download(version=previous)
                self.assertEqual(error.exception.status, 404)
            previous = digest
        self.assertEqual(self.server.room.db.execute('SELECT COUNT(*) FROM participant_icons').fetchone()[0], 1)
        self.save('provider')
        self.assertEqual(self.server.room.db.execute('SELECT COUNT(*) FROM participant_icons').fetchone()[0], 0)
        with self.assertRaises(RelayError) as error:
            self.download()
        self.assertEqual(error.exception.status, 404)

    def test_invalid_updates_leave_previous_icon_unchanged(self):
        self.save('bot')
        for icon in (None, [], {}, True, 'unknown', '../../secret', 'https://example.com/avatar.png', 'image:123'):
            with self.subTest(icon=icon), self.assertRaises(RelayError) as error:
                self.save(icon)
            self.assertEqual(error.exception.status, 400)
        for agent in ('missing', [], None):
            with self.subTest(agent=agent), self.assertRaises(RelayError) as error:
                self.save('terminal', agent=agent)
            self.assertEqual(error.exception.status, 404)
        with self.assertRaises(RelayError) as error:
            self.save('image')
        self.assertEqual(error.exception.status, 409)
        for name, data in [('image.txt', b'hello'), ('image.pdf', b'%PDF-1.7'), ('image.mp4', b'\0\0\0\x18ftypisom')]:
            staged = self.stage(name, data)
            with self.subTest(name=name), self.assertRaises(RelayError) as error:
                self.save('image', upload=staged)
            self.assertEqual(error.exception.status, 415)
            self.call('/api/uploads/discard', {'attachments': [staged]})
        self.assertEqual(self.call('/api/setup?agent=codex')['icon'], 'bot')
        self.assertEqual(self.server.room.db.execute('SELECT COUNT(*) FROM participant_icons').fetchone()[0], 0)

    def test_shared_upload_size_limit_and_content_validation(self):
        data = PNG + b'\0' * (MAX_FILE_BYTES - len(PNG))
        self.save('image', upload=self.stage('limit.png', data))
        self.assertEqual(len(self.download()[1]), MAX_FILE_BYTES)
        for name, contents in [('fake.png', b'<html>not an image'), ('avatar.svg', b'<svg/>')]:
            with self.subTest(name=name), self.assertRaises(RelayError) as error:
                self.stage(name, contents)
            self.assertEqual(error.exception.status, 415)
        with self.assertRaises(UploadError) as error:
            self.server.room.uploads.stage(BytesIO(), MAX_FILE_BYTES + 1, 'large.png')
        self.assertEqual(error.exception.status, 413)

    def test_persistence_and_removed_participant_history(self):
        self.save('image', upload=self.stage('avatar.png', PNG))
        original = self.call('/api/messages', {'sender': 'user', 'recipient': 'codex', 'body': 'Review'})['message']
        self.call('/api/messages', {'sender': 'codex', 'reply_to': original['id'], 'body': 'Reviewed', 'handoff': False})
        self.call('/api/participants/delete', {'id': 'codex'})
        self.server.room.db.close()
        self.server.room = Room(Path(self.temporary.name) / 'room.sqlite3')
        room = self.call('/api/room')
        self.assertNotIn('codex', [agent['id'] for agent in room['participants']])
        archived = next(agent for agent in room['archived_participants'] if agent['id'] == 'codex')
        self.assertEqual(archived['icon'], 'image:' + sha256(PNG).hexdigest())
        self.assertEqual(self.download(), ('image/png', PNG))
        self.assertEqual(len(room['messages']), 2)
        with self.assertRaises(RelayError) as error:
            self.save('bot')
        self.assertEqual(error.exception.status, 404)

    def test_existing_database_migrates_without_changing_participants(self):
        path = Path(self.temporary.name) / 'legacy.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE participants (id TEXT PRIMARY KEY, role TEXT NOT NULL, name TEXT NOT NULL)')
            db.execute('INSERT INTO participants VALUES (?,?,?)', ('codex-2', 'codex', '기존 참가자'))
        db.close()
        room = Room(path)
        self.addCleanup(room.db.close)
        participant = next(agent for agent in room.participants() if agent['id'] == 'codex-2')
        self.assertEqual((participant['name'], participant['icon']), ('기존 참가자', 'provider'))
        room.set_participant_icon({'id': 'codex-2', 'icon': 'terminal'})
        room.db.close()
        reopened = Room(path)
        self.addCleanup(reopened.db.close)
        self.assertEqual(reopened.setup('codex-2', self.port)['icon'], 'terminal')


if __name__ == '__main__':
    unittest.main()
