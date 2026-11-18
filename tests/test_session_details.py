import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from relay.session_details import CodexSessions, ParticipantDetails
from relay.server import Room


def user_message(text, role='user'):
    return {'type': 'response_item', 'payload': {'type': 'message', 'role': role,
            'content': [{'type': 'input_text', 'text': text}]}}


class SessionDetails(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.db = sqlite3.connect(self.home / 'state_5.sqlite')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE threads (id TEXT, rollout_path TEXT, updated_at INTEGER, archived INTEGER, model TEXT, reasoning_effort TEXT, first_user_message TEXT)')
        self.reader = CodexSessions(self.home)

    def session(self, session_id, agent=None, model=None, effort=None, updated=1, events=(), first=None):
        path = self.home / (session_id + '.jsonl')
        records = ([user_message(f'[AgentChatSystem:{agent}]\nListen for tasks.')] if agent else []) + list(events)
        path.write_text(''.join(json.dumps(record) + '\n' for record in records), encoding='utf-8')
        with self.db:
            self.db.execute('INSERT INTO threads VALUES (?,?,?,0,?,?,?)',
                            (session_id, str(path), updated, model, effort, first))
        return path

    def test_matches_each_agent_and_uses_latest_linked_session(self):
        self.session('old', 'codex', 'old-model', 'low')
        self.session('latest', 'codex', 'selected-model', 'xhigh', updated=2)
        self.session('second', 'codex-2', 'second-model', 'medium')
        self.session('unrelated', model='unrelated-model', effort='high', updated=10)
        found = self.reader.read({'codex', 'codex-2', 'codex-3'})
        self.assertEqual(found['codex'], {'model': 'selected-model', 'reasoning_effort': 'xhigh'})
        self.assertEqual(found['codex-2']['model'], 'second-model')
        self.assertNotIn('codex-3', found)

    def test_assistant_quotes_do_not_link_an_unrelated_session(self):
        self.session('quoted', model='wrong-model', events=[user_message('[AgentChatSystem:codex]', 'assistant')])
        self.assertEqual(self.reader.read({'codex'}), {})

    def test_old_schema_falls_back_to_turn_context_and_tracks_changes(self):
        for column in ('model', 'reasoning_effort', 'first_user_message'):
            self.db.execute('ALTER TABLE threads DROP COLUMN ' + column)
        path = self.home / 'legacy.jsonl'
        path.write_text(json.dumps(user_message('[AgentChatSystem:codex]')) + '\n' + json.dumps({
            'type': 'turn_context', 'payload': {'model': 'legacy-model', 'effort': 'medium'},
        }) + '\n', encoding='utf-8')
        with self.db:
            self.db.execute('INSERT INTO threads VALUES (?,?,1,0)', ('legacy', str(path)))
        self.assertEqual(self.reader.read({'codex'})['codex']['reasoning_effort'], 'medium')
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'type': 'turn_context', 'payload': {'model': 'new-model', 'effort': 'high'}}) + '\n')
        self.assertEqual(self.reader.read({'codex'})['codex'], {'model': 'new-model', 'reasoning_effort': 'high'})

    def test_unavailable_or_malformed_metadata_is_not_guessed(self):
        path = self.session('first', first='[AgentChatSystem:codex]\nListen.')
        path.write_text('broken JSON\n[]\n{"payload": []}\n', encoding='utf-8')
        self.assertEqual(self.reader.read({'codex'})['codex'], {'model': None, 'reasoning_effort': None})
        path.unlink()
        self.assertEqual(self.reader.read({'codex'})['codex'], {'model': None, 'reasoning_effort': None})
        self.assertEqual(CodexSessions(self.home / 'missing').read({'codex'}), {})

    def test_truncated_transcript_does_not_keep_an_old_identity(self):
        path = self.session('replaced', 'codex', model='known-model')
        self.assertIn('codex', self.reader.read({'codex'}))
        path.write_text('{}\n', encoding='utf-8')
        self.assertEqual(self.reader.read({'codex'}), {})

    def test_legacy_resume_settings_do_not_override_current_session(self):
        self.session('bound', 'codex', 'old-model', 'low')
        self.session('manual', 'codex', 'manual-model', 'high', updated=2)
        room = Room(self.home / 'room.sqlite3')
        self.addCleanup(room.db.close)
        room.participant_details = ParticipantDetails(room, self.home)
        with room.db:
            room.db.execute('CREATE TABLE agent_automation (agent_id TEXT, session_id TEXT, cwd TEXT, transcript TEXT, enabled INTEGER)')
            room.db.execute('INSERT INTO agent_automation VALUES (?,?,?,?,0)',
                            ('codex', 'bound', str(self.home), str(self.home / 'bound.jsonl')))
        room.participant_details.refresh()
        self.assertEqual(room.participant_details.values['codex']['model'], 'manual-model')
        with room.db:
            room.db.execute('UPDATE agent_automation SET enabled=1')
        room.participant_details.refresh()
        self.assertEqual(room.participant_details.values['codex']['model'], 'manual-model')

    def test_room_updates_only_when_settings_change(self):
        self.session('linked', 'codex', 'known-model', 'high')
        room = Room(self.home / 'room.sqlite3')
        self.addCleanup(room.db.close)
        room.participant_details = ParticipantDetails(room, self.home)
        revision = room.revision
        room.participant_details.refresh()
        self.assertGreater(room.revision, revision)
        participants = room.participants()
        self.assertEqual(participants[0]['model_settings']['model'], 'known-model')
        self.assertNotIn('model_settings', participants[1])
        revision = room.revision
        room.participant_details.refresh()
        self.assertEqual(room.revision, revision)
