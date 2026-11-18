"""Read model settings only from Codex sessions linked to a participant."""

from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import Event, Thread


def text_value(value):
    return value.strip() if isinstance(value, str) and 0 < len(value.strip()) <= 120 and value.isprintable() else None


def agent_marker(content):
    if not isinstance(content, str):
        return None
    first = content.lstrip().partition('\n')[0].rstrip()
    match = re.fullmatch(r'\[AgentChatSystem:([a-zA-Z0-9_-]{1,32})\]', first)
    if not match:
        match = re.match(r'이 대화는 (?:AgentChatSystem|Local Relay)의 ([a-zA-Z0-9_-]{1,32}) 세션이야\.', first)
    return match.group(1) if match else None


class CodexSessions:
    def __init__(self, home=None):
        self.home = Path(home) if home is not None else Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
        self.cache = {}

    def transcript(self, filename):
        if not isinstance(filename, str) or not filename:
            return {'agents': set(), 'model': None, 'reasoning_effort': None}
        path = Path(filename)
        previous = self.cache.get(filename, {})
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if previous.get('signature') == signature:
                return previous
            # A listening marker is normally near the start; turn settings are
            # near the end. Bound each read even for very large conversations.
            with path.open('rb') as handle:
                head = handle.read(512 * 1024)
                offset = max(len(head), stat.st_size - 2 * 1024 * 1024)
                handle.seek(offset)
                tail = handle.read(2 * 1024 * 1024)
            lines = head.splitlines() + tail.splitlines()
        except (OSError, ValueError):
            return {'agents': set(), 'model': None, 'reasoning_effort': None}
        if previous.get('signature', (0, 0))[1] > stat.st_size:
            previous = {}
        result = {'signature': signature, 'agents': set(previous.get('agents', ())),
                  'model': previous.get('model'), 'reasoning_effort': previous.get('reasoning_effort')}
        for line in lines:
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(record, dict) or not isinstance(record.get('payload'), dict):
                continue
            payload = record['payload']
            if record.get('type') == 'turn_context':
                result['model'] = text_value(payload.get('model'))
                result['reasoning_effort'] = text_value(payload.get('effort') or payload.get('reasoning_effort'))
            elif record.get('type') == 'response_item' and payload.get('type') == 'message' and payload.get('role') == 'user':
                content = payload.get('content')
                if isinstance(content, list):
                    content = '\n'.join(block.get('text', '') for block in content
                                        if isinstance(block, dict) and isinstance(block.get('text'), str))
                agent = agent_marker(content)
                if agent:
                    result['agents'].add(agent)
        self.cache[filename] = result
        return result

    def read(self, agent_ids):
        if not agent_ids:
            return {}
        result = {}
        databases = sorted(self.home.glob('state_*.sqlite'),
                           key=lambda path: int(path.stem[6:]) if path.stem[6:].isdigit() else -1, reverse=True)
        for database in databases:
            try:
                with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.2)) as db:
                    db.row_factory = sqlite3.Row
                    columns = {row['name'] for row in db.execute('PRAGMA table_info(threads)')}
                    if not {'id', 'rollout_path', 'updated_at', 'archived'} <= columns:
                        continue
                    optional = [name if name in columns else f'NULL AS {name}'
                                for name in ('model', 'reasoning_effort', 'first_user_message')]
                    rows = db.execute('SELECT id,rollout_path,' + ','.join(optional)
                                      + ' FROM threads WHERE archived=0 ORDER BY updated_at DESC LIMIT 100').fetchall()
            except (OSError, sqlite3.Error):
                continue
            used = set()
            for row in rows:
                settings = self.transcript(row['rollout_path'])
                used.add(row['rollout_path'])
                matched = settings['agents'] | {agent_marker(row['first_user_message'])}
                for agent in agent_ids:
                    if agent in matched and agent not in result:
                        result[agent] = {
                            'model': text_value(row['model']) or settings['model'],
                            'reasoning_effort': text_value(row['reasoning_effort']) or settings['reasoning_effort'],
                        }
            self.cache = {key: value for key, value in self.cache.items() if key in used}
            break
        return result


class ParticipantDetails:
    def __init__(self, room, home=None):
        self.room = room
        self.reader = CodexSessions(home)
        self.values = {}
        self.stopped = Event()

    def refresh(self):
        with self.room.changed:
            agents = {row[0] for row in self.room.db.execute(
                "SELECT id FROM participants WHERE role='codex' AND removed_at IS NULL")}
        values = self.reader.read(agents)
        with self.room.changed:
            if values != self.values:
                self.values = values
                self.room._notify()

    def start(self):
        def watch():
            while not self.stopped.is_set():
                try:
                    self.refresh()
                except (OSError, sqlite3.Error):
                    # A temporarily unavailable local session store must not
                    # stop chat or prevent later metadata refreshes.
                    pass
                self.stopped.wait(5)
        Thread(target=watch, name='codex-participant-details', daemon=True).start()
