"""ACS-owned conversations and their delivery workers."""
from datetime import datetime, timezone
import json
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, time

from relay.errors import ApiError
from relay import managed_cli
from relay.managed_options import ROLES, ModelOptions, claude_model_label, codex_quota, folder_path
from relay.settings import PROJECT


PARENT_SCHEMA = {
    'type': 'object',
    'properties': {
        'message': {'type': 'string'},
        'child_id': {'type': ['string', 'null']},
    },
    'required': ['message', 'child_id'],
    'additionalProperties': False,
}
MAX_HANDOFFS = 2
TASK_TIMEOUT_SECONDS = 2 * 60 * 60
TASK_IDLE_SECONDS = 30 * 60
STARTUP_TIMEOUT_SECONDS = 120


@lru_cache(maxsize=7)
def catalog(language):
    return json.loads(files('relay.web').joinpath('locales', language + '.json').read_text(encoding='utf-8'))


class ManagedAgents:
    def __init__(self, room, data_dir, pending_sql):
        self.room = room
        self.db = room.db
        self.data_dir = data_dir
        self.options = ModelOptions(PROJECT)
        self.pending_sql = pending_sql
        self.processes = {}
        self.logins = {}
        self.login_states = {}
        self.threads = {}
        self.stopping = set()
        self.closed = False
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS managed_agents (
                id TEXT PRIMARY KEY REFERENCES participants(id),
                parent_id TEXT REFERENCES managed_agents(id),
                cwd TEXT NOT NULL,
                model TEXT NOT NULL,
                model_label TEXT NOT NULL DEFAULT '',
                effort TEXT NOT NULL,
                language TEXT NOT NULL,
                session TEXT,
                usage TEXT,
                active_message INTEGER,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS managed_parent ON managed_agents(parent_id);
        """)
        if 'model_label' not in {row['name'] for row in self.db.execute('PRAGMA table_info(managed_agents)')}:
            self.db.execute("ALTER TABLE managed_agents ADD COLUMN model_label TEXT NOT NULL DEFAULT ''")
        columns = {row['name'] for row in self.db.execute('PRAGMA table_info(participants)')}
        if 'is_manager' not in columns:
            with self.db:
                self.db.execute('BEGIN')
                self.db.execute('ALTER TABLE participants ADD COLUMN is_manager INTEGER NOT NULL DEFAULT 0')
                self.db.execute("""
                    UPDATE participants SET is_manager=1
                    WHERE id IN (SELECT id FROM managed_agents WHERE parent_id IS NULL)
                """)

    def get(self, agent_id):
        return self.db.execute("""
            SELECT a.*,p.role,p.name,p.channel_id FROM managed_agents a
            JOIN participants p ON a.id=p.id WHERE a.id=? AND p.removed_at IS NULL
        """, (agent_id,)).fetchone()

    def _startup_agent(self, agent_id, channel_id):
        agent = self.get(agent_id) if isinstance(agent_id, str) else None
        if not agent or agent['channel_id'] != channel_id or agent['session'] or not agent['error']:
            raise ApiError('This agent is not waiting for startup recovery.', 409, key='managedNotReady')
        return agent

    def retry(self, agent_id, channel_id):
        with self.room.changed:
            if self.closed:
                raise ApiError('ACS is stopping.', 503, key='errorUnavailable')
            agent = self._startup_agent(agent_id, channel_id)
            if agent['role'] in self.logins:
                raise ApiError('Wait for CLI login to finish.', 409, key='managedAuthPending')
            with self.db:
                self.db.execute('UPDATE managed_agents SET error=NULL WHERE id=?', (agent['id'],))
            self.room._notify()
            return {'state': 'starting'}

    def login(self, agent_id, channel_id):
        with self.room.changed:
            if self.closed:
                raise ApiError('ACS is stopping.', 503, key='errorUnavailable')
            agent = self.get(agent_id) if isinstance(agent_id, str) else None
            if not agent or agent['channel_id'] != channel_id or agent['error'] != 'managedAuthRequired':
                raise ApiError('This agent has no login request.', 409, key='managedNotReady')
            role = agent['role']
            if role in self.logins:
                return {'state': 'pending'}
            args = managed_cli.login_command(role)
            try:
                turn = managed_cli.Turn(args, None)
            except OSError as error:
                raise ApiError('Could not start CLI login.', 503, key='managedAuthFailed') from error
            self.logins[role] = turn
            self.login_states[role] = 'pending'
            self.threads['login-' + role] = Thread(
                target=self._login, args=(role, turn), name='acs-login-' + role, daemon=True,
            )
            self.threads['login-' + role].start()
            self.room._notify()
            return {'state': 'pending'}

    def _login(self, role, turn):
        state = 'failed'
        try:
            turn.read('', timeout=300)
            state = 'ready'
        except (OSError, subprocess.TimeoutExpired, managed_cli.CliError):
            pass
        finally:
            with self.room.changed:
                turn.stop()
                self.logins.pop(role, None)
                self.login_states[role] = state
                self.threads.pop('login-' + role, None)
                if not self.closed and state == 'ready':
                    with self.db:
                        self.db.execute("""
                            UPDATE managed_agents SET error=NULL
                            WHERE error='managedAuthRequired'
                                AND id IN (SELECT id FROM participants WHERE role=? AND removed_at IS NULL)
                        """, (role,))
                self.room._notify()
            turn.reap()

    def create(self, payload, language):
        parent_id = payload.get('parent_id')
        role = payload.get('role')
        if role not in ROLES or (parent_id is None and role != 'codex'):
            raise ApiError('Choose a supported agent type.', key='errorRole')
        if parent_id is not None and (not isinstance(parent_id, str) or len(parent_id) > 32):
            raise ApiError('Choose a Codex parent in this channel.', key='managedParentMissing')
        cwd = folder_path(payload.get('cwd'))
        model = payload.get('model', '')
        effort = payload.get('effort', '')
        managed_cli.require_executable(role)
        choice = self.options.validate(role, model, effort)
        if role == 'claude' and not choice:
            choice = next((item for item in self.options.get(role)['models'] if item['default_model']), None)
        model_label = choice['name'] if role == 'claude' and choice else ''
        with self.room.changed:
            if self.closed:
                raise ApiError('ACS is stopping.', 503, key='errorUnavailable')
            channel_id = self.room.channel_scope(payload.get('channel_id'))
            if parent_id is not None:
                parent = self.get(parent_id)
                if not parent or parent['parent_id'] is not None or parent['channel_id'] != channel_id or not parent['session']:
                    raise ApiError('Choose a ready Codex parent in this channel.', 409, key='managedParentMissing')
            with self.db:
                agent = self.room._register(payload)
                self.db.execute("""
                    INSERT INTO managed_agents(id,parent_id,cwd,model,model_label,effort,language) VALUES (?,?,?,?,?,?,?)
                """, (agent['id'], parent_id, str(cwd), model, model_label, effort, language))
                if parent_id is None:
                    self.db.execute('UPDATE participants SET is_manager=1 WHERE id=?', (agent['id'],))
            self.room._notify_board()
            self.room._write_board(channel_id)
            self._start(agent['id'])
            return agent

    def start(self):
        with self.room.changed:
            for row in self.db.execute('SELECT id FROM managed_agents').fetchall():
                self._start(row['id'])

    def _start(self, agent_id):
        worker = Thread(target=self._work, args=(agent_id,), name='acs-' + agent_id, daemon=True)
        self.threads[agent_id] = worker
        worker.start()

    def public(self, row, *, local=False):
        status = 'ready'
        if row['error'] == 'managedAuthRequired':
            status = 'auth'
        elif row['error']:
            status = 'failed'
        elif not row['session']:
            status = 'starting'
        elif row['active_message'] is not None:
            status = 'busy'
        quota_row = self.db.execute('SELECT value FROM workspace_state WHERE key=?',
                                    ('cli_quota_' + row['role'],)).fetchone()
        quota = json.loads(quota_row['value']) if quota_row else None
        if quota and quota['resets_at'] and quota['resets_at'] <= time():
            quota = None
        result = {
            'managed': True, 'parent_id': row['parent_id'], 'state': status,
            'resumable': bool(row['session']),
            'error': row['error'], 'usage': quota,
            'model_settings': {'model': row['model_label'] or row['model'], 'reasoning_effort': row['effort']},
            'waiting': status == 'ready',
        }
        if local:
            result['cwd'] = row['cwd']
            result['active_message'] = row['active_message']
            result['stopping'] = (row['id'], row['active_message']) in self.stopping
            if status == 'auth':
                result['auth_state'] = self.login_states.get(row['role'], 'idle')
        return result

    def stop_task(self, agent_id, channel_id, message_id):
        with self.room.changed:
            agent = self.get(agent_id) if isinstance(agent_id, str) else None
            if (not agent or agent['channel_id'] != channel_id or
                type(message_id) is not int or agent['active_message'] != message_id):
                raise ApiError('The selected CLI task is no longer running.', 409, key='managedTaskChanged')
            self.stopping.add((agent_id, message_id))
            if turn := self.processes.get(agent_id):
                turn.stop()
            self.room._notify()
            return {'state': 'stopping'}

    def delete(self, ids):
        for agent_id in ids:
            turn = self.processes.get(agent_id)
            if turn:
                turn.stop()
        self.db.executemany('DELETE FROM managed_agents WHERE id=?', ((agent_id,) for agent_id in ids))

    def cancel_channel(self, channel_id):
        for row in self.db.execute("""
            SELECT a.id FROM managed_agents a JOIN participants p ON p.id=a.id WHERE p.channel_id=?
        """, (channel_id,)):
            turn = self.processes.get(row['id'])
            if turn and self.get(row['id'])['active_message'] is not None:
                turn.stop()

    def cancel_message(self, message_id):
        for row in self.db.execute('SELECT id FROM managed_agents WHERE active_message=?', (message_id,)):
            turn = self.processes.get(row['id'])
            if turn:
                turn.stop()

    def close(self):
        with self.room.changed:
            self.closed = True
            for turn in (*self.processes.values(), *self.logins.values()):
                turn.stop()
            workers = list(self.threads.values())
            self.room.changed.notify_all()
        for worker in workers:
            worker.join(timeout=2)

    def _work(self, agent_id):
        try:
            with self.room.changed:
                agent = self.get(agent_id)
                if agent and agent['active_message'] is not None:
                    self._finish(agent, agent['active_message'], failure='managedInterrupted')
            while True:
                with self.room.changed:
                    agent = self.get(agent_id)
                    if self.closed or not agent:
                        return
                    if agent['error'] and (not agent['session'] or agent['error'] == 'managedAuthRequired'):
                        self.room.changed.wait()
                        continue
                    message = None
                    if agent['session']:
                        message = self.db.execute(f"""
                            SELECT m.* {self.pending_sql} AND d.agent_id=? ORDER BY m.id LIMIT 1
                        """, (agent_id,)).fetchone()
                        if not message:
                            self.room.changed.wait()
                            continue
                        with self.db:
                            self.db.execute('UPDATE managed_agents SET active_message=?,error=NULL WHERE id=?',
                                            (message['id'], agent_id))
                        self.room._notify()
                    message_id = message['id'] if message else None
                self._turn(agent, message, message_id)
        finally:
            with self.room.changed:
                self.threads.pop(agent_id, None)

    def _prompt(self, agent, message):
        if not message:
            return 'Initialize this AgentChatSystem conversation. Do not use tools. Reply only: Ready.', set()
        children = set()
        instructions = (
            'Respond to this AgentChatSystem message in the sender’s language. This run is non-interactive. '
            'Use the tools allowed in the reference folder. If approval or input is required, complete any '
            'remaining work you can do and report the blocker in your final reply. Do not wait for an answer '
            'or repeat a denied action. Do not retry a task a user has stopped.'
        )
        if agent['role'] == 'claude':
            instructions += (
                ' For folder inspection, list and search the reference folder with Glob and Grep, '
                'then read relevant current files with Read. Use Git only when the task asks for changes '
                'relative to Git, history, branches, or another repository-specific operation.'
            )
        if agent['parent_id'] is None:
            cursor = message
            depth = 0
            while cursor and cursor['reply_to'] and depth < MAX_HANDOFFS * 2:
                cursor = self.db.execute('SELECT reply_to FROM messages WHERE id=?', (cursor['reply_to'],)).fetchone()
                depth += 1
            if depth < MAX_HANDOFFS * 2:
                rows = self.db.execute("""
                    SELECT a.id,p.name,p.role FROM managed_agents a JOIN participants p ON a.id=p.id
                    WHERE a.parent_id=? AND a.session IS NOT NULL AND p.removed_at IS NULL
                        AND COALESCE(a.error, '') != 'managedAuthRequired'
                """, (agent['id'],))
                roster = [dict(row) for row in rows]
                children = {child['id'] for child in roster}
                instructions += (
                    '\nYou are the parent. Only use child_id to delegate work to an existing child. '
                    'When delegating, put the complete task for that child in message. Its response returns here. '
                    'For your final reply set child_id to null. Available children: ' + json.dumps(roster, ensure_ascii=False)
                )
            else:
                instructions += '\nGive the final answer now and set child_id to null.'
        return instructions + '\n\nIncoming message:\n' + message['body'], children

    def _attachments(self, message, target):
        source = message
        while source['sender'] != 'user' and source['reply_to']:
            source = self.db.execute('SELECT * FROM messages WHERE id=?', (source['reply_to'],)).fetchone()
            if source is None:
                return ''
        attachments = []
        for row in self.db.execute('SELECT id,name,data FROM chat_attachments WHERE message_id=?', (source['id'],)):
            path = target / (str(row['id']) + Path(row['name']).suffix)
            path.write_bytes(row['data'])
            attachments.append({'name': row['name'], 'path': str(path)})
        if not attachments:
            return ''
        return '\n\nFiles attached by the user for this task (available during this turn):\n' + json.dumps(attachments, ensure_ascii=False)

    def _turn(self, agent, message, message_id):
        agent_id = agent['id']
        started = monotonic()
        turn = None
        scratch = None
        response = None
        failure = 'managedRunFailed'
        try:
            with self.room.changed:
                if self.closed or not self.get(agent_id) or (agent_id, message_id) in self.stopping:
                    return
                if message and not self.db.execute('SELECT 1 FROM messages WHERE id=?', (message_id,)).fetchone():
                    return
                prompt, children = self._prompt(agent, message)
                program = managed_cli.executable(agent['role'])
                if not program:
                    raise managed_cli.CliError('CLI missing')
                schema = None
                attachments = None
                if message:
                    scratch = TemporaryDirectory(prefix='acs-turn-', dir=self.data_dir)
                    if not agent['parent_id']:
                        schema = Path(scratch.name) / 'response.json'
                        schema.write_text(json.dumps(PARENT_SCHEMA), encoding='utf-8')
                    attachment_prompt = self._attachments(message, Path(scratch.name))
                    if attachment_prompt:
                        attachments = Path(scratch.name)
                    prompt += attachment_prompt
                turn = managed_cli.Turn(managed_cli.command(program, agent, schema, attachments=attachments), agent['cwd'])
                self.processes[agent_id] = turn
            timeout = TASK_TIMEOUT_SECONDS if message else STARTUP_TIMEOUT_SECONDS
            session, body, model, quota = turn.run(
                agent['role'], prompt, timeout, idle_timeout=TASK_IDLE_SECONDS if message else None,
            )
            if agent['session'] and session != agent['session']:
                raise managed_cli.CliError('CLI resumed a different session', key='managedResponseInvalid')
            target = agent['parent_id'] if message and message['sender'] == agent['parent_id'] else None
            if schema:
                try:
                    reply = json.loads(body)
                    body, child = reply['message'], reply['child_id']
                    if not isinstance(body, str) or not body.strip() or (
                        child is not None and (not isinstance(child, str) or child not in children)
                    ):
                        raise ValueError
                except (ValueError, TypeError, KeyError) as error:
                    raise managed_cli.CliError('Invalid delegation', key='managedResponseInvalid') from error
                target = child
            if agent['role'] == 'codex':
                quota = codex_quota(program, PROJECT)
            model_label = claude_model_label(model) if model else ''
            response = (session, body.strip()[:10000].encode('utf-8', 'replace').decode(), model_label, quota, target)
        except managed_cli.CliError as error:
            failure = managed_cli.failure_key(agent['role'], error)
        except subprocess.TimeoutExpired:
            failure = 'managedTimedOut' if message else 'managedStartupTimedOut'
        except PermissionError:
            failure = 'managedPermissionDenied'
        except (OSError, ValueError, TypeError, KeyError):
            pass
        finally:
            with self.room.changed:
                if (agent_id, message_id) in self.stopping:
                    self.stopping.discard((agent_id, message_id))
                    response, failure = None, 'managedTaskStopped'
                if turn:
                    turn.stop()
                    self.processes.pop(agent_id, None)
                if not self.closed and self.get(agent_id):
                    self._finish(agent, message_id, response, failure=None if response else failure)
                    if not response and failure != 'managedTaskStopped' and (
                        message is None or self.db.execute('SELECT 1 FROM messages WHERE id=?', (message_id,)).fetchone()
                    ):
                        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
                        print(f'{now} CLI {agent_id} message={message_id} {failure} after {monotonic() - started:.1f}s',
                              file=sys.stderr, flush=True)
            try:
                if turn:
                    turn.reap()
            finally:
                if scratch:
                    scratch.cleanup()

    def _finish(self, agent, message_id, response=None, *, failure=None):
        labels = catalog(agent['language'])
        aid, channel_id = agent['id'], agent['channel_id']
        original = self.db.execute('SELECT sender FROM messages WHERE id=?', (message_id,)).fetchone()
        published = None
        with self.db:
            if response:
                session, body, model_label, quota, target = response
                if quota is not None:
                    self.db.execute('INSERT OR REPLACE INTO workspace_state(key,value) VALUES (?,?)',
                                    ('cli_quota_' + agent['role'], json.dumps(quota)))
                self.db.execute("UPDATE managed_agents SET session=?,model_label=COALESCE(NULLIF(?,''),model_label) WHERE id=?",
                                (session, model_label, aid))
            else:
                target = agent['parent_id'] if original and original['sender'] == agent['parent_id'] else None
                body = labels[failure] if failure else ''
            if target and self.get(target) is None:
                target = None
                body += '\n\n' + labels['managedChildUnavailable']
            if message_id is None and response:
                published = self.room._insert(channel_id, aid, None, labels['managedCreated'])
            elif original:
                published = self.room._insert(channel_id, aid, target, body, message_id)
            self.db.execute('UPDATE managed_agents SET active_message=NULL,error=? WHERE id=?',
                            (failure if (message_id is None or original) and failure != 'managedTaskStopped' else None, aid))
        if published:
            self.room._write_log(channel_id, published)
        self.room._notify()
