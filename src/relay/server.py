from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from configparser import Error as ConfigError
from importlib.resources import files
from ipaddress import ip_address
from pathlib import Path
import json
import os
import re
import sqlite3
import sys
from threading import Condition, Lock
from time import monotonic, time_ns
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from relay.automation import Automation
from relay.board import Board, prepare_post
from relay.chat_files import ChatFiles, prepare_chat_files
from relay.errors import ApiError
from relay.storage import ChannelStorage, StorageError, TEXT_BYTES
from relay.uploads import MAX_FILES, PNG_BYTES, MAX_UPLOAD_REQUEST, UploadError, is_png
from relay.guidance import DEFAULT_WAIT_SECONDS, MAX_WAIT_SECONDS, LEGACY_MAX_WAIT_SECONDS, MCP_VERSION, LEGACY_PNG_PROMPT
from relay.settings import APP_NAME, LANGUAGES, LOOPBACK, Settings

ROLES = ("codex", "claude")
DEFAULT_CHANNEL = "general"
MAX_PARTICIPANTS = 64
MAX_BODY = 48_000
TEXT_LIMIT = 10_000
WAIT_LIMIT = MAX_WAIT_SECONDS
PENDING_MESSAGES = """
    FROM deliveries d JOIN messages m ON m.id=d.message_id
    WHERE NOT EXISTS(SELECT 1 FROM messages r WHERE r.reply_to=m.id AND r.sender=d.agent_id)
"""
MESSAGE_FIELDS = 'm.id, m.channel_id, m.sender, m.recipient, m.body, m.reply_to, m.created_at'


class RequestError(ApiError):
    pass


class Room:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage = ChannelStorage(db_path.parent)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender TEXT NOT NULL,
                recipient TEXT,
                body TEXT NOT NULL,
                reply_to INTEGER REFERENCES messages(id),
                remaining INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            CREATE TABLE IF NOT EXISTS participants (
                id TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                name TEXT NOT NULL
            );
            INSERT OR IGNORE INTO participants(id,role,name) VALUES ('codex', 'codex', 'Codex');
            INSERT OR IGNORE INTO participants(id,role,name) VALUES ('claude', 'claude', 'Claude');
            CREATE TABLE IF NOT EXISTS channels (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );
            INSERT OR IGNORE INTO channels(id,name) VALUES ('general', '작업실');
            CREATE TABLE IF NOT EXISTS deliveries (
                message_id INTEGER NOT NULL REFERENCES messages(id),
                agent_id TEXT NOT NULL REFERENCES participants(id),
                PRIMARY KEY (message_id, agent_id)
            );
            CREATE INDEX IF NOT EXISTS delivery_inbox ON deliveries(agent_id, message_id);
        """)
        with self.db:
            self.db.execute('BEGIN')
            columns = {row['name'] for row in self.db.execute('PRAGMA table_info(messages)')}
            if 'channel_id' not in columns:
                self.db.execute("ALTER TABLE messages ADD COLUMN channel_id TEXT NOT NULL DEFAULT 'general'")
                self.db.execute('''
                    INSERT INTO deliveries(message_id,agent_id)
                    SELECT m.id,p.id FROM messages m JOIN participants p ON p.id=m.recipient
                ''')
            if 'request_id' not in columns:
                self.db.execute('ALTER TABLE messages ADD COLUMN request_id TEXT')
            participant_columns = {row['name'] for row in self.db.execute('PRAGMA table_info(participants)')}
            if 'removed_at' not in participant_columns:
                self.db.execute('ALTER TABLE participants ADD COLUMN removed_at TEXT')
            delivery_columns = {row['name'] for row in self.db.execute('PRAGMA table_info(deliveries)')}
            for column in ('received_at', 'received_by'):
                if column not in delivery_columns:
                    self.db.execute(f'ALTER TABLE deliveries ADD COLUMN {column} TEXT')
            channel_columns = {row['name'] for row in self.db.execute('PRAGMA table_info(channels)')}
            if 'folder' not in channel_columns:
                self.db.execute('ALTER TABLE channels ADD COLUMN folder TEXT')
            self.db.execute('DROP INDEX IF EXISTS one_reply')
            self.db.execute('DROP INDEX IF EXISTS inbox')
            self.db.execute('CREATE INDEX IF NOT EXISTS channel_messages ON messages(channel_id,id)')
            self.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS chat_request_ids ON messages(request_id) WHERE request_id IS NOT NULL')
            self.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS one_reply_per_agent ON messages(reply_to,sender) WHERE reply_to IS NOT NULL')
        self.changed = Condition()
        self.board = Board(self.db)
        self.chat_files = ChatFiles(self.db)
        self.board_revision = time_ns()
        self.folder_lock = Lock()
        self.revision = 0
        self.waiters = {}
        self.legacy_notices = set()
        self.streams = 0
        self.dirty_logs = set()
        self.automation = Automation(self, db_path.parent)
        for channel in self.channels():
            try:
                self.storage.folder(Path(channel['folder']), 'shared')
            except (OSError, StorageError) as error:
                print(f'Could not open shared folder for {channel["id"]}: {error}', file=sys.stderr, flush=True)
            self._write_log(channel['id'])

    def _notify(self):
        self.revision += 1
        self.changed.notify_all()

    def _notify_board(self):
        self.board_revision += 1
        self._notify()

    def publish_board_post(self, payload):
        post_data = prepare_post(payload)
        with self.changed:
            post, published = self.board.create(*post_data)
            if published:
                self._notify_board()
            return post

    def list_board_posts(self, before):
        with self.changed:
            return self.board.list(before)

    def delete_board_post(self, post_id):
        with self.changed:
            post, removed = self.board.delete(post_id)
            if removed:
                self._notify_board()
            return post

    def read_board_post(self, post_id, agent=None):
        with self.changed:
            if agent is not None:
                if self._participant(agent) is None:
                    raise RequestError('등록된 에이전트를 찾을 수 없습니다.', 404, key='errorAgentMissing')
                post = self.board.post(post_id, include_deleted=True)
                if post.get('status') == 'deleted':
                    self.board.acknowledge_revocation(post_id, agent)
                elif self.board.mark_read(post_id, agent):
                    self._notify_board()
                return post
            return self.board.post(post_id, receipts=True)

    def board_image(self, image_id):
        with self.changed:
            return self.board.image(image_id)

    def chat_attachment(self, attachment_id, agent):
        with self.changed:
            if agent is not None and self._participant(agent) is None:
                raise RequestError('등록된 에이전트를 찾을 수 없습니다.', 404, key='errorAgentMissing')
            return self.chat_files.get(attachment_id, agent)

    def _log_records(self, channel_id):
        cursor = self.db.execute(
            f'SELECT {MESSAGE_FIELDS} FROM messages m WHERE channel_id=? ORDER BY id', (channel_id,)
        )
        while rows := cursor.fetchmany(100):
            yield from self.chat_files.attach_metadata(rows)

    def _write_log(self, channel_id, message=None):
        try:
            target = self.storage.log_path(Path(self._channel(channel_id)['folder']))
            rebuild = message is None or channel_id in self.dirty_logs or not target.exists()
            records = self._log_records(channel_id) if rebuild else (message,)
            self.storage.log(target, records, replace=rebuild)
            self.dirty_logs.discard(channel_id)
        except (OSError, StorageError) as error:
            # SQLite still holds the message. Retry the full export on the next write or restart.
            self.dirty_logs.add(channel_id)
            print(f'Failed to export channel {channel_id}: {error}', file=sys.stderr, flush=True)

    def _channel(self, channel_id):
        if isinstance(channel_id, str) and len(channel_id) <= 32:
            row = self.db.execute('SELECT * FROM channels WHERE id=?', (channel_id,)).fetchone()
            if row:
                return self.storage.describe(row)
        raise RequestError('채널을 찾을 수 없습니다.', 404, key='errorChannelMissing')

    def channels(self):
        with self.changed:
            return [self.storage.describe(row) for row in self.db.execute('SELECT * FROM channels ORDER BY rowid')]

    def create_channel(self, payload):
        name = self._name(payload.get('name'), '채널 이름', 'errorChannelName')
        with self.changed:
            self._check_name(name, self.channels())
            channel_id = 'ch-' + uuid4().hex[:12]
            self.storage.folder(self.storage.root / channel_id, 'shared')
            with self.db:
                self.db.execute('INSERT INTO channels(id,name) VALUES (?,?)', (channel_id, name))
            self._write_log(channel_id)
            self._notify()
            return self._channel(channel_id)

    def clear_channel(self, channel_id):
        with self.folder_lock:
            with self.changed:
                channel = self._channel(channel_id)
                target = self.storage.log_path(Path(channel['folder']))
                with self.db:
                    self.db.execute('DELETE FROM chat_attachments WHERE message_id IN (SELECT id FROM messages WHERE channel_id=?)', (channel_id,))
                    self.db.execute('DELETE FROM deliveries WHERE message_id IN (SELECT id FROM messages WHERE channel_id=?)', (channel_id,))
                    count = self.db.execute('DELETE FROM messages WHERE channel_id=?', (channel_id,)).rowcount
                    self.storage.log(target, (), replace=True)
                self._notify()
                return {'channel_id': channel_id, 'cleared': count}

    def _check_folder_scope(self, location, default):
        folders = (location / 'shared', location / 'logs')
        if location != default and location.is_relative_to(self.storage.root):
            raise RequestError('채널 기본 저장소 밖에 별도 폴더를 지정하세요.', 409, key='errorFolderScope')
        for channel in self.channels():
            root = Path(channel['folder'])
            for existing in (root / 'shared', root / 'logs'):
                if location == root or location.is_relative_to(existing) or any(
                    existing.is_relative_to(folder) or folder.is_relative_to(existing) for folder in folders
                ):
                    raise RequestError('이미 사용 중인 채널 폴더와 겹칩니다.', 409, key='errorFolderOverlap')

    def set_folder(self, payload):
        copy_files = payload.get('copy_shared', True)
        if type(copy_files) is not bool:
            raise RequestError('기존 공유 파일을 복사할지 선택하세요.', key='errorFolderCopyChoice')
        with self.folder_lock:
            with self.changed:
                channel = self._channel(payload.get('channel_id'))
                channel_id = channel['id']
                previous = Path(channel['folder'])
                default = Path(channel['default_folder'])
                location = self.storage.location(payload.get('folder'), channel_id)
                if location == previous:
                    return channel
                self._check_folder_scope(location, default)
                target = self.storage.log_path(location)
                self.storage.check_existing_log(target, self._log_records(channel_id))
            self.storage.folder(location, 'shared')
            if copy_files:
                self.storage.copy_shared(previous, location)
            with self.changed:
                self.storage.check_existing_log(target, self._log_records(channel_id))
                self.storage.log(target, self._log_records(channel_id), replace=True)
                with self.db:
                    self.db.execute('UPDATE channels SET folder=? WHERE id=?', (
                        None if location == default else str(location), channel_id,
                    ))
                self.dirty_logs.discard(channel_id)
                self._notify()
                return self._channel(channel_id)

    def storage_action(self, action, payload):
        with self.folder_lock:
            with self.changed:
                root = Path(self._channel(payload.get('channel_id'))['folder'])
            if action == 'open':
                return self.storage.open_folder(root, payload.get('kind'))
            if action == 'list':
                return self.storage.list_shared(root, payload.get('path', ''))
            if action == 'read':
                return self.storage.read_shared(root, payload.get('path'))
            return self.storage.write_shared(root, payload.get('path'), payload.get('text'))

    def _participant(self, agent_id, *, include_removed=False):
        if not isinstance(agent_id, str) or len(agent_id) > 32 or not agent_id.isascii():
            return None
        if include_removed:
            return self.db.execute('SELECT id,role,name,removed_at FROM participants WHERE id=?', (agent_id,)).fetchone()
        return self.db.execute('SELECT id,role,name FROM participants WHERE id=? AND removed_at IS NULL', (agent_id,)).fetchone()

    @staticmethod
    def _name(value, label, key):
        name = ' '.join(value.split()) if isinstance(value, str) else ''
        if not 1 <= len(name) <= 32 or not name.isprintable():
            raise RequestError(f'{label}은 1~32자의 글자로 입력하세요.', key=key)
        return name

    @staticmethod
    def _check_name(name, rows, own_id=None):
        if any(row['id'] != own_id and row['name'].casefold() == name.casefold() for row in rows):
            raise RequestError('이미 사용 중인 이름입니다.', 409, key='errorNameUsed')

    def register(self, payload):
        role = payload.get('role')
        if role not in ROLES:
            raise RequestError('Codex 또는 Claude를 선택하세요.', key='errorRole')
        name = self._name(payload.get('name'), '닉네임', 'errorNickname')
        with self.changed:
            existing = self.db.execute('SELECT * FROM participants').fetchall()
            active = [row for row in existing if row['removed_at'] is None]
            self._check_name(name, active)
            if len(active) >= MAX_PARTICIPANTS:
                raise RequestError('에이전트는 64개까지 등록할 수 있습니다.', 409, key='errorAgentLimit')
            role_ids = {row['id'] for row in existing if row['role'] == role}
            number = 2
            while f'{role}-{number}' in role_ids:
                number += 1
            agent_id = f'{role}-{number}'
            with self.db:
                self.db.execute('INSERT INTO participants(id,role,name) VALUES (?,?,?)', (agent_id, role, name))
            self._notify()
            return {'id': agent_id, 'role': role, 'name': name}

    def rename(self, payload):
        agent_id = payload.get('id')
        name = self._name(payload.get('name'), '닉네임', 'errorNickname')
        with self.changed:
            participant = self._participant(agent_id)
            if participant is None:
                raise RequestError('등록된 에이전트를 찾을 수 없습니다.', 404, key='errorAgentMissing')
            self._check_name(name, self.db.execute('SELECT id,name FROM participants WHERE removed_at IS NULL'), agent_id)
            with self.db:
                self.db.execute('UPDATE participants SET name=? WHERE id=?', (name, agent_id))
            self._notify_board()
            return dict(participant) | {'name': name}

    def remove_participant(self, agent_id):
        with self.changed:
            participant = self._participant(agent_id, include_removed=True)
            if participant is None:
                raise RequestError('등록된 에이전트를 찾을 수 없습니다.', 404, key='errorAgentMissing')
            if participant['removed_at'] is None:
                with self.db:
                    self.db.execute("""
                        UPDATE participants SET removed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?
                    """, (agent_id,))
                    self.board.drop_pending(agent_id)
                    self.db.execute('UPDATE agent_automation SET enabled=0 WHERE agent_id=?', (agent_id,))
                self._notify_board()
            return {'id': agent_id, 'removed': True}

    def setup(self, agent_id, port, language='en'):
        from relay.cli import connection_setup
        with self.changed:
            participant = self._participant(agent_id)
            if participant is None:
                raise RequestError('등록된 에이전트를 찾을 수 없습니다.', 404, key='errorAgentMissing')
            return {
                **dict(participant), **connection_setup(port, agent_id, participant['role'], language=language),
                'automation': self.automation.describe(agent_id),
            }

    def participants(self, channel_id=None):
        with self.changed:
            scope = 'AND m.channel_id=?' if channel_id else ''
            params = (self._channel(channel_id)['id'],) if channel_id else ()
            pending = dict(self.db.execute(f'SELECT d.agent_id, COUNT(*) {PENDING_MESSAGES} {scope} GROUP BY d.agent_id', params))
            participants = self.db.execute('SELECT id,role,name FROM participants WHERE removed_at IS NULL ORDER BY rowid').fetchall()
            return [
                dict(p) | {
                    'waiting': p['id'] in self.waiters and (
                        not channel_id or self.waiters[p['id']] in (None, channel_id)
                    ),
                    'pending': pending.get(p['id'], 0),
                    'automation': self.automation.describe(p['id']),
                }
                for p in participants
            ]

    def _archived_participants(self, messages):
        ids = {row[field] for row in messages for field in ('sender', 'recipient')} - {None, 'user', 'all'}
        if not ids:
            return []
        placeholders = ','.join('?' for _ in ids)
        return [dict(row) for row in self.db.execute(f'''
            SELECT id,role,name FROM participants WHERE removed_at IS NOT NULL AND id IN ({placeholders})
        ''', tuple(ids))]

    def snapshot(self, channel_id):
        with self.changed:
            channel = self._channel(channel_id)
            rows = self.db.execute(
                f'SELECT {MESSAGE_FIELDS} FROM messages m WHERE channel_id=? ORDER BY id DESC LIMIT 100', (channel_id,)
            ).fetchall()
            return {
                "revision": self.revision,
                "board_revision": str(self.board_revision),
                "channel": channel,
                "channels": self.channels(),
                "participants": self.participants(channel_id),
                "archived_participants": self._archived_participants(rows),
                "total_messages": self.db.execute('SELECT COUNT(*) FROM messages WHERE channel_id=?', (channel_id,)).fetchone()[0],
                "messages": self.chat_files.attach_metadata(reversed(rows)),
            }

    def _insert(self, channel_id, sender, recipient, body, reply_to=None, *, files=(), request_id=None):
        cursor = self.db.execute(
            "INSERT INTO messages(channel_id,sender,recipient,body,reply_to,remaining,request_id) VALUES(?,?,?,?,?,0,?)",
            (channel_id, sender, recipient, body, reply_to, request_id),
        )
        message_id = cursor.lastrowid
        if recipient == 'all':
            self.db.execute('INSERT INTO deliveries(message_id,agent_id) SELECT ?, id FROM participants WHERE removed_at IS NULL', (message_id,))
        elif recipient is not None:
            self.db.execute('INSERT INTO deliveries(message_id,agent_id) VALUES (?,?)', (message_id, recipient))
        if files:
            self.chat_files.save(message_id, files)
        message = self.db.execute(f'SELECT {MESSAGE_FIELDS} FROM messages m WHERE id=?', (message_id,)).fetchone()
        return self.chat_files.attach_metadata((message,))[0]

    def post(self, payload):
        sender = payload.get("sender")
        files = prepare_chat_files(payload.get('attachments', []))
        body = payload.get("body", "")
        if not isinstance(body, str) or len(body.strip()) > TEXT_LIMIT or (not body.strip() and not files):
            raise RequestError("메시지는 1~10,000자로 입력하거나 파일을 첨부하세요.", key='errorMessageBody')
        try:
            body.encode('utf-8')
        except UnicodeError:
            raise RequestError('메시지를 UTF-8로 읽을 수 없습니다.', key='errorMessageEncoding') from None
        with self.changed:
            if sender == "user":
                channel_id = self._channel(payload.get('channel_id', DEFAULT_CHANNEL))['id']
                recipient = payload.get("recipient")
                request_id = payload.get('request_id')
                if request_id is not None:
                    try:
                        if not isinstance(request_id, str) or len(request_id) != 36:
                            raise ValueError
                        request_id = str(UUID(request_id))
                    except ValueError:
                        raise RequestError('전송 요청 ID를 확인하세요.', key='errorRequestId') from None
                    existing = self.db.execute(f'SELECT {MESSAGE_FIELDS} FROM messages m WHERE request_id=?', (request_id,)).fetchone()
                    if existing:
                        if existing['channel_id'] != channel_id or existing['recipient'] != recipient:
                            raise RequestError('전송 요청 ID가 이미 사용 중입니다.', 409, key='errorRequestUsed')
                        return {"message": self.chat_files.attach_metadata((existing,))[0], "duplicate": True}
                if recipient == 'all':
                    if self.db.execute('SELECT 1 FROM participants WHERE removed_at IS NULL LIMIT 1').fetchone() is None:
                        raise RequestError('메시지를 받을 에이전트를 먼저 추가하세요.', 409, key='errorAddAgentFirst')
                elif self._participant(recipient) is None:
                    raise RequestError('보낼 대상을 선택하세요.', key='errorChooseRecipient')
                with self.db:
                    message = self._insert(channel_id, sender, recipient, body.strip(), files=files, request_id=request_id)
            elif self._participant(sender) is not None:
                reply_to = payload.get("reply_to")
                handoff = payload.get("handoff", True)
                if type(reply_to) is not int or not 1 <= reply_to <= 2**63 - 1 or type(handoff) is not bool:
                    raise RequestError("받은 메시지 ID와 handoff 값을 확인하세요.")
                original = self.db.execute(
                    'SELECT m.channel_id FROM messages m JOIN deliveries d ON m.id=d.message_id WHERE m.id=? AND d.agent_id=?',
                    (reply_to, sender),
                ).fetchone()
                if original is None:
                    raise RequestError("해당 에이전트에게 온 메시지 ID가 아닙니다.", 409)
                existing = self.db.execute(
                    f'SELECT {MESSAGE_FIELDS} FROM messages m WHERE reply_to=? AND sender=?', (reply_to, sender)
                ).fetchone()
                if existing:
                    return {"message": self.chat_files.attach_metadata((existing,))[0], "duplicate": True}
                channel_id = original['channel_id']
                body, shared_files = self._shared_png_reply(channel_id, body, len(files))
                files.extend(shared_files)
                recipient = payload.get('next_agent') if handoff else None
                if recipient is not None and (recipient == sender or self._participant(recipient) is None):
                    raise RequestError('다른 등록 에이전트를 수신자로 선택하세요.', 409)
                with self.db:
                    message = self._insert(channel_id, sender, recipient, body.strip(), reply_to, files=files)
            else:
                raise RequestError("보낸 사람을 확인하세요.")
            self._write_log(channel_id, message)
            self._notify()
            return {"message": message, "duplicate": False}

    def _shared_png_reply(self, channel_id, body, file_count):
        # Only explicit trailing markers in an agent reply can read channel-shared PNGs.
        lines = body.rstrip().splitlines()
        paths = []
        while lines:
            match = re.fullmatch(r'\[\[attach_png:([^\r\n]+)\]\]', lines[-1].strip())
            if not match:
                break
            paths.append(match[1])
            lines.pop()
        if not paths:
            return body, []
        if file_count + len(paths) > MAX_FILES:
            raise UploadError('PNG 파일은 최대 4개까지 첨부할 수 있습니다.')
        root = Path(self._channel(channel_id)['folder'])
        attachments = []
        for relative in reversed(paths):
            _, path = self.storage.shared_path(root, relative)
            if path.suffix.lower() != '.png' or len(path.name) > 160:
                raise UploadError('shared 안의 PNG 파일 경로를 입력하세요.', 415)
            try:
                if not path.is_file():
                    raise OSError
                with path.open('rb') as handle:
                    data = handle.read(PNG_BYTES + 1)
            except OSError:
                raise UploadError(f'shared에서 PNG 파일을 열 수 없습니다: {path.name}', 404) from None
            if len(data) > PNG_BYTES:
                raise UploadError('PNG 파일은 파일당 4 MiB까지 첨부할 수 있습니다.', 413)
            if not is_png(data):
                raise UploadError(f'PNG 파일의 형식을 확인하세요: {path.name}', 415)
            attachments.append((path.name, 'image/png', data))
        return '\n'.join(lines).strip(), attachments

    def wait_result(self, agent, seconds, channel_id=None, runner=None, client_version=None):
        result = self.wait_message(agent, seconds, channel_id, runner)
        # Teach already-connected clients the body-only upload without changing their cached tool schema.
        with self.changed:
            if client_version != MCP_VERSION and agent not in self.legacy_notices and result['state'] != 'removed':
                self.legacy_notices.add(agent)
                result['relay_update'] = {
                    'version': MCP_VERSION,
                    'receive_seconds': DEFAULT_WAIT_SECONDS,
                    'png_reply': LEGACY_PNG_PROMPT,
                }
        return result

    def pending_event(self, agent):
        revoked = self.board.revoked(agent)
        if revoked:
            return {'kind': 'board_deleted', 'id': revoked['post_id']}
        notice = self.board.unread(agent)
        if notice:
            return {'kind': 'board_post', 'id': notice['id']}
        row = self.db.execute(f'SELECT m.id {PENDING_MESSAGES} AND d.agent_id=? ORDER BY m.id LIMIT 1', (agent,)).fetchone()
        return {'kind': 'message', 'id': row['id']} if row else None

    def configure_automation(self, payload):
        with self.changed:
            agent = self._participant(payload.get('id'))
            if agent is None:
                raise RequestError('에이전트를 찾을 수 없습니다.', 404, key='errorAgentMissing')
        try:
            return self.automation.configure(dict(agent), payload)
        except (ValueError, sqlite3.Error) as error:
            raise RequestError(str(error), 409) from None

    def wait_message(self, agent, seconds, channel_id=None, runner=None):
        if type(seconds) is not int or not 0 <= seconds <= LEGACY_MAX_WAIT_SECONDS:
            raise RequestError(f"에이전트와 대기 시간(0~{LEGACY_MAX_WAIT_SECONDS}초)을 확인하세요.")
        # Old clients can still submit 60, but idle waiting is now capped at 40 seconds.
        seconds = min(seconds, WAIT_LIMIT)
        with self.changed:
            participant = self._participant(agent, include_removed=True)
            if participant is None:
                raise RequestError('먼저 에이전트를 등록하세요.', 404)
            if participant['removed_at'] is not None:
                return {'state': 'removed'}
            scope = 'AND m.channel_id=?' if channel_id else ''
            params = (agent, self._channel(channel_id)['id']) if channel_id else (agent,)
            if seconds and agent in self.waiters:
                raise RequestError('이 ID는 이미 대기 중입니다. 각 대화에 다른 에이전트 ID를 사용하세요.', 409)
            if seconds:
                self.waiters[agent] = channel_id
                self._notify()
            deadline = monotonic() + seconds
            try:
                while True:
                    if self._participant(agent) is None:
                        return {'state': 'removed'}
                    if not runner and self.automation.describe(agent)['enabled']:
                        return {'state': 'managed', 'message': '자동 재개 실행기가 이 받은편지함을 처리합니다. 수동 수신을 끝내세요.'}
                    revoked = self.board.revoked(agent)
                    if revoked:
                        return revoked
                    notice = self.board.unread(agent)
                    if notice:
                        return {'state': 'board_post', 'post': notice}
                    message = self.db.execute(
                        f'SELECT {MESSAGE_FIELDS} {PENDING_MESSAGES} AND d.agent_id=? {scope} ORDER BY m.id LIMIT 1', params
                    ).fetchone()
                    if message:
                        with self.db:
                            self.db.execute("""
                                UPDATE deliveries SET received_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),received_by=?
                                WHERE message_id=? AND agent_id=?
                            """, (runner, message['id'], agent))
                        return {
                            "state": "message", "message": self.chat_files.attach_metadata((message,))[0],
                            "channel": self._channel(message['channel_id']),
                        }
                    left = deadline - monotonic()
                    if left <= 0:
                        return {"state": "timeout"}
                    self.changed.wait(left)
            finally:
                if seconds:
                    del self.waiters[agent]
                    self._notify()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    static = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
        "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        "/board.js": ("board.js", "text/javascript; charset=utf-8"),
        "/style.css": ("style.css", "text/css; charset=utf-8"),
        "/vendor/icons.svg": ("vendor/icons.svg", "image/svg+xml"),
        "/vendor/PretendardVariable.woff2": ("vendor/PretendardVariable.woff2", "font/woff2"),
        **{f'/locales/{language}.json': (f'locales/{language}.json', 'application/json; charset=utf-8')
           for language in LANGUAGES},
    }

    def log_message(self, *args):
        pass

    @property
    def room(self):
        return self.server.room

    def _check_local(self):
        allowed_hosts = {f'{LOOPBACK}:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        hosts = self.headers.get_all('Host', [])
        origins = self.headers.get_all('Origin', [])
        if (not ip_address(self.client_address[0]).is_loopback
                or len(hosts) != 1 or hosts[0] not in allowed_hosts
                or (origins and origins != [f'http://{hosts[0]}'])):
            raise RequestError("로컬 접속만 허용됩니다.", 403, key='errorLocalOnly')

    def _bytes(self, status, data, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, data, status=200):
        self._bytes(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8")

    @staticmethod
    def _number(params, name):
        raw = params.get(name, [None])[0]
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            raise RequestError('유효한 게시글 또는 이미지 번호를 입력하세요.', key='errorPostId') from None

    def _events(self):
        with self.room.changed:
            if self.room.streams >= 12:
                raise RequestError("브라우저 연결이 너무 많습니다.", 429)
            self.room.streams += 1
        try:
            self.close_connection = True
            self.connection.settimeout(8)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            seen = -1
            while True:
                with self.room.changed:
                    changed = self.room.changed.wait_for(lambda: self.room.revision != seen, 10)
                    if changed:
                        seen = self.room.revision
                if changed:
                    self.wfile.write(f"event: change\ndata: {seen}\n\n".encode("utf-8"))
                else:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        finally:
            with self.room.changed:
                self.room.streams -= 1

    def _saved_language(self):
        try:
            return self.server.settings.language()
        except (ConfigError, UnicodeError, OSError) as error:
            raise RequestError('settings.ini could not be read.', 503) from error

    def do_GET(self):
        try:
            self._check_local()
            url = urlsplit(self.path)
            params = parse_qs(url.query)
            channel_id = params.get('channel', [None])[0]
            if url.path in self.static:
                filename, content_type = self.static[url.path]
                self._bytes(200, files("relay.web").joinpath(filename).read_bytes(), content_type)
            elif url.path == "/api/health":
                self._json({"service": APP_NAME, "pid": os.getpid(), "version": 11,
                            "mcp_version": MCP_VERSION, "receive_seconds": DEFAULT_WAIT_SECONDS,
                            "png_reply": True, "legacy_png_reply": True})
            elif url.path == "/api/room":
                self._json(self.room.snapshot(channel_id or DEFAULT_CHANNEL))
            elif url.path == "/api/events":
                self._events()
            elif url.path == '/api/settings':
                self._json({'language': self._saved_language()})
            elif url.path == '/api/channels':
                self._json({'channels': self.room.channels()})
            elif url.path == '/api/setup':
                agent_id = params.get('agent', [''])[0]
                language = params.get('lang', [None])[0]
                if language not in LANGUAGES:
                    language = self._saved_language()
                self._json(self.room.setup(agent_id, self.server.server_port, language))
            elif url.path == '/api/participants':
                self._json({'participants': self.room.participants(channel_id)})
            elif url.path == '/api/board':
                self._json(self.room.list_board_posts(self._number(params, 'before')))
            elif url.path == '/api/board/post':
                self._json(self.room.read_board_post(self._number(params, 'id')))
            elif url.path == '/api/board/image':
                image = self.room.board_image(self._number(params, 'id'))
                self._bytes(200, image['data'], image['mime_type'])
            elif url.path == '/api/messages/attachment':
                agent = params.get('agent', [None])[0]
                attachment = self.room.chat_attachment(self._number(params, 'id'), agent)
                mime = attachment['mime_type']
                self._bytes(200, attachment['data'], 'text/plain; charset=utf-8' if mime == 'text/plain' else mime)
            elif url.path == "/api/wait":
                try:
                    seconds = int(params.get("seconds", [str(DEFAULT_WAIT_SECONDS)])[0])
                except ValueError:
                    raise RequestError("대기 시간은 초 단위 정수로 입력하세요.") from None
                self._json(self.room.wait_result(params.get("agent", [""])[0], seconds, channel_id,
                                                self.headers.get('X-Relay-Runner'), self.headers.get('X-Local-Relay-Client')))
            else:
                raise RequestError("페이지를 찾을 수 없습니다.", 404, key='errorUnavailable')
        except ApiError as exc:
            self._json(exc.response(), exc.status)
        except (ConnectionError, TimeoutError):
            pass

    def do_POST(self):
        try:
            self._check_local()
            self.connection.settimeout(8)
            self.close_connection = True
            if self.headers.get_content_type() != "application/json" or self.headers.get("X-Local-Relay") != "1":
                raise RequestError("채팅방 요청 헤더를 확인하세요.", 415, key='errorRequestHeaders')
            try:
                size = int(self.headers.get("Content-Length", "0"))
                max_body = {
                    '/api/shared/write': TEXT_BYTES * 6 + MAX_BODY,
                    '/api/board': MAX_UPLOAD_REQUEST,
                    '/api/messages': MAX_UPLOAD_REQUEST,
                }.get(self.path, MAX_BODY)
                if not 1 <= size <= max_body:
                    raise RequestError("요청 크기 제한을 확인하세요.", 413, key='errorRequestSize')
                payload = json.loads(self.rfile.read(size))
            except (ValueError, UnicodeError):
                raise RequestError("올바른 JSON 본문이 필요합니다.", key='errorRequestJson') from None
            if not isinstance(payload, dict):
                raise RequestError("JSON 객체가 필요합니다.", key='errorRequestObject')
            if self.path == "/api/messages":
                self._json(self.room.post(payload))
            elif self.path == '/api/settings/language':
                try:
                    language = self.server.settings.set_language(
                        payload.get('language'), initialize=payload.get('initialize') is True,
                    )
                except ValueError as error:
                    raise RequestError(str(error)) from None
                except (ConfigError, OSError) as error:
                    raise RequestError('settings.ini could not be saved.', 503) from error
                self._json({'language': language})
            elif self.path == '/api/board':
                self._json(self.room.publish_board_post(payload), 201)
            elif self.path == '/api/board/delete':
                self._json(self.room.delete_board_post(payload.get('post_id')))
            elif self.path == '/api/board/read':
                self._json(self.room.read_board_post(payload.get('post_id'), payload.get('agent') or ''))
            elif self.path == '/api/participants':
                self._json(self.room.register(payload), 201)
            elif self.path == '/api/participants/rename':
                self._json(self.room.rename(payload))
            elif self.path == '/api/participants/delete':
                self._json(self.room.remove_participant(payload.get('id')))
            elif self.path == '/api/participants/automation':
                self._json(self.room.configure_automation(payload))
            elif self.path == '/api/channels':
                self._json(self.room.create_channel(payload), 201)
            elif self.path == '/api/channels/open':
                self._json(self.room.storage_action('open', payload))
            elif self.path == '/api/channels/folder':
                self._json(self.room.set_folder(payload))
            elif self.path == '/api/channels/clear':
                self._json(self.room.clear_channel(payload.get('channel_id')))
            elif self.path in ('/api/shared/list', '/api/shared/read', '/api/shared/write'):
                self._json(self.room.storage_action(self.path.rsplit('/', 1)[1], payload))
            else:
                raise RequestError("페이지를 찾을 수 없습니다.", 404, key='errorUnavailable')
        except ApiError as exc:
            self._json(exc.response(), exc.status)
        except (ConnectionError, TimeoutError):
            pass
        except OSError:
            error = ApiError('채널 폴더에 접근할 수 없습니다. 파일 사용 상태와 권한을 확인하세요.', 503, key='errorFolderAccess')
            self._json(error.response(), error.status)


def serve(port, db_path, *, settings=None):
    with ThreadingHTTPServer((LOOPBACK, port), Handler) as server:
        server.settings = settings if settings is not None else Settings()
        server.room = Room(db_path)
        server.room.automation.start()
        print(f"{APP_NAME}: http://{LOOPBACK}:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.room.automation.stopped.set()
