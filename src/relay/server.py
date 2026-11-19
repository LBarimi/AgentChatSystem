from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from configparser import Error as ConfigError
from importlib.resources import files
from hashlib import sha256
from ipaddress import ip_address, ip_network
from pathlib import Path
from socketserver import TCPServer
import json
import os
import re
import signal
import sqlite3
import sys
from threading import Condition, Lock, current_thread, main_thread
from time import monotonic, time_ns
from urllib.parse import parse_qs, quote, urlsplit
from uuid import UUID, uuid4

from relay.board import Board, prepare_post
from relay.chat_files import ChatFiles, prepare_chat_files
from relay.errors import ApiError
from relay.managed_agents import ManagedAgents
from relay.storage import ChannelStorage, StorageError, TEXT_BYTES
from relay.uploads import MAX_FILES, MAX_FILE_BYTES, MAX_UPLOAD_REQUEST, UploadError, UploadStore, file_data, prepare_files, upload_policy, validate_file
from relay.guidance import DEFAULT_WAIT_SECONDS, MAX_WAIT_SECONDS, LEGACY_MAX_WAIT_SECONDS, MCP_VERSION, LEGACY_PNG_PROMPT
from relay.settings import APP_NAME, LANGUAGES, LISTEN_HOST, LOOPBACK, Settings
from relay.session_details import ParticipantDetails
from relay.presence import ConnectedUsers
from relay.prompts import prompt_catalog

ROLES = ("codex", "claude")
DEFAULT_CHANNEL = "general"
MAX_PARTICIPANTS = 64
PARTICIPANT_ICONS = ('provider', 'bot', 'terminal', 'command', 'radio', 'message-square', 'hash', 'users')
ICON_IMAGE_TYPES = {'image/png', 'image/jpeg', 'image/gif'}
MAX_BODY = 48_000
TEXT_LIMIT = 10_000
WAIT_LIMIT = MAX_WAIT_SECONDS
LAN_NETWORKS = tuple(ip_network(network) for network in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
PENDING_MESSAGES = """
    FROM deliveries d JOIN messages m ON m.id=d.message_id
    WHERE NOT EXISTS(SELECT 1 FROM messages r WHERE r.reply_to=m.id AND r.sender=d.agent_id)
"""
MESSAGE_FIELDS = 'm.id, m.channel_id, m.sender, m.recipient, m.body, m.reply_to, m.created_at, m.user_ip, m.user_name'


class RequestError(ApiError):
    pass


def local_address(value):
    try:
        address = ip_address(value)
    except ValueError:
        return False
    return address.version == 4 and (address.is_loopback or any(address in network for network in LAN_NETWORKS))


def loopback(value):
    try:
        return isinstance(value, str) and ip_address(value).is_loopback
    except ValueError:
        return False


class Room:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage = ChannelStorage(db_path.parent)
        self.uploads = UploadStore(db_path.parent / 'uploads')
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
            CREATE TABLE IF NOT EXISTS workspace_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                message_id INTEGER NOT NULL REFERENCES messages(id),
                agent_id TEXT NOT NULL REFERENCES participants(id),
                PRIMARY KEY (message_id, agent_id)
            );
            CREATE INDEX IF NOT EXISTS delivery_inbox ON deliveries(agent_id, message_id);
            CREATE TABLE IF NOT EXISTS participant_icons (
                agent_id TEXT PRIMARY KEY REFERENCES participants(id),
                mime_type TEXT NOT NULL,
                data BLOB NOT NULL
            );
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
            for column in ('user_ip', 'user_name'):
                if column not in columns:
                    self.db.execute(f'ALTER TABLE messages ADD COLUMN {column} TEXT')
            participant_columns = {row['name'] for row in self.db.execute('PRAGMA table_info(participants)')}
            if 'removed_at' not in participant_columns:
                self.db.execute('ALTER TABLE participants ADD COLUMN removed_at TEXT')
            if 'icon' not in participant_columns:
                self.db.execute("ALTER TABLE participants ADD COLUMN icon TEXT NOT NULL DEFAULT 'provider'")
            if 'channel_id' not in participant_columns:
                self.db.execute("ALTER TABLE participants ADD COLUMN channel_id TEXT DEFAULT 'general'")
            self.db.execute('CREATE INDEX IF NOT EXISTS participants_channel ON participants(channel_id)')
            channel_columns = {row['name'] for row in self.db.execute('PRAGMA table_info(channels)')}
            if 'folder' not in channel_columns:
                self.db.execute('ALTER TABLE channels ADD COLUMN folder TEXT')
            if 'name_customized' not in channel_columns:
                self.db.execute('ALTER TABLE channels ADD COLUMN name_customized INTEGER NOT NULL DEFAULT 0')
            # Seed once, including on upgrade; an intentionally empty workspace stays empty.
            initialized = self.db.execute(
                "INSERT OR IGNORE INTO workspace_state(key,value) VALUES ('channels_initialized','1')"
            ).rowcount
            if initialized:
                self.db.execute("INSERT OR IGNORE INTO channels(id,name) VALUES ('general','작업실')")
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
        self.users = ConnectedUsers(self.changed, self._notify)
        self.waiters = {}
        self.legacy_notices = set()
        self.streams = 0
        self.dirty_logs = set()
        self.dirty_boards = set()
        self._migrate_channel_scope()
        self.participant_details = ParticipantDetails(self)
        self.managed = ManagedAgents(self, db_path.parent, PENDING_MESSAGES)
        for channel in self.channels():
            try:
                self.storage.folder(Path(channel['folder']), 'shared')
            except (OSError, StorageError) as error:
                print(f'Could not open shared folder for {channel["id"]}: {error}', file=sys.stderr, flush=True)
            self._write_log(channel['id'])
            self._write_board(channel['id'])

    def _migrate_channel_scope(self):
        with self.db:
            initialized = self.db.execute(
                "INSERT OR IGNORE INTO workspace_state(key,value) VALUES ('channel_isolation','1')"
            ).rowcount
            if not initialized:
                return
            row = self.db.execute("SELECT id FROM channels ORDER BY (id='general') DESC,rowid LIMIT 1").fetchone()
            target = row['id'] if row else None
            if target is None and self.db.execute('SELECT 1 FROM board_posts WHERE deleted_at IS NULL LIMIT 1').fetchone():
                target = 'ch-recovered-' + uuid4().hex[:8]
                self.db.execute('INSERT INTO channels(id,name) VALUES (?,?)', (target, 'Recovered workspace'))
            self.db.execute('UPDATE participants SET channel_id=?', (target,))
            self.db.execute('UPDATE board_posts SET channel_id=?', (target,))
            if target is None:
                self.db.execute("UPDATE participants SET removed_at=COALESCE(removed_at,strftime('%Y-%m-%dT%H:%M:%fZ','now'))")
                self.db.execute('DELETE FROM board_delivery')
                self.db.execute('DELETE FROM board_revocations')
            # Historical transcripts stay intact; pending cross-channel deliveries do not.
            self.db.execute('''DELETE FROM deliveries WHERE NOT EXISTS (
                SELECT 1 FROM messages m JOIN participants p ON p.id=deliveries.agent_id
                WHERE m.id=deliveries.message_id AND p.channel_id=m.channel_id
            )''')

    def channel_scope(self, channel_id=None, agent=None, *, include_removed=False):
        if agent is not None:
            participant = self._participant(agent, include_removed=include_removed)
            if participant is None:
                raise RequestError('등록된 에이전트를 찾을 수 없습니다.', 404, key='errorAgentMissing')
            if channel_id is not None and participant['channel_id'] != channel_id:
                raise RequestError('다른 채널의 참가자입니다.', 404, key='errorAgentChannel')
            channel_id = participant['channel_id']
        return self._channel(channel_id if channel_id is not None else DEFAULT_CHANNEL)['id']

    def _board_records(self, channel_id, post_id=None):
        rows = self.db.execute('SELECT id FROM board_posts WHERE channel_id=? AND (? IS NULL OR id=?) ORDER BY id',
                               (channel_id, post_id, post_id))
        for row in rows:
            yield (self.board.post(row['id'], receipts=True, include_deleted=True, channel_id=channel_id),
                   self.db.execute('SELECT id,name,data FROM board_images WHERE post_id=? ORDER BY id', (row['id'],)))

    def _write_board(self, channel_id, post_id=None, *, strict=False):
        try:
            root = Path(self._channel(channel_id)['folder'])
            if channel_id in self.dirty_boards:
                post_id = None
            self.storage.export_board(root, channel_id, self._board_records(channel_id, post_id))
            self.dirty_boards.discard(channel_id)
        except (OSError, StorageError) as error:
            self.dirty_boards.add(channel_id)
            if strict:
                raise
            print(f'Failed to export board for {channel_id}: {error}', file=sys.stderr, flush=True)

    def _notify(self):
        self.revision += 1
        self.changed.notify_all()

    def _notify_board(self):
        self.board_revision += 1
        self._notify()

    def publish_board_post(self, payload):
        with self.uploads.lock, self.changed:
            channel_id = self.channel_scope(payload.get('channel_id'))
            request_id = payload.get('request_id')
            existing = self.db.execute('SELECT id FROM board_posts WHERE request_id=?',
                                       (request_id if isinstance(request_id, str) else None,)).fetchone()
            if existing:
                post = self.board.post(existing['id'], channel_id=channel_id)
            else:
                post, published = self.board.create(*prepare_post(payload, self.uploads), channel_id=channel_id)
                if published:
                    self._notify_board()
            self._write_board(channel_id, post['id'])
            self.uploads.discard(payload.get('attachments', payload.get('images', [])))
            return post

    def list_board_posts(self, before=None, channel_id=None, agent=None):
        with self.changed:
            return self.board.list(before, self.channel_scope(channel_id, agent))

    def delete_board_post(self, post_id, channel_id=None):
        with self.changed:
            channel_id = self.channel_scope(channel_id)
            post, removed = self.board.delete(post_id, channel_id)
            if removed:
                self._notify_board()
            self._write_board(channel_id, post_id, strict=True)
            return post

    def read_board_post(self, post_id, agent=None, channel_id=None):
        with self.changed:
            channel_id = self.channel_scope(channel_id, agent)
            if agent is not None:
                post = self.board.post(post_id, include_deleted=True, channel_id=channel_id)
                if post.get('status') == 'deleted':
                    self.board.acknowledge_revocation(post_id, agent)
                elif self.board.mark_read(post_id, agent):
                    self._notify_board()
                    self._write_board(channel_id, post_id)
                return post
            return self.board.post(post_id, receipts=True, channel_id=channel_id)

    def board_image(self, image_id, channel_id=None, agent=None):
        with self.changed:
            return self.board.image(image_id, self.channel_scope(channel_id, agent))

    def chat_attachment(self, attachment_id, agent=None, channel_id=None):
        with self.changed:
            return self.chat_files.get(attachment_id, self.channel_scope(channel_id, agent))

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
            pending = dict(self.db.execute(f'''SELECT m.channel_id, COUNT(*) {PENDING_MESSAGES}
                AND EXISTS (SELECT 1 FROM participants p WHERE p.id=d.agent_id
                            AND p.channel_id=m.channel_id AND p.removed_at IS NULL)
                GROUP BY m.channel_id'''))
            return [self.storage.describe(row) | {'pending': pending.get(row['id'], 0)}
                    for row in self.db.execute('SELECT * FROM channels ORDER BY rowid')]

    def create_channel(self, payload):
        name = self._name(payload.get('name'), '채널 이름', 'errorChannelName')
        with self.changed:
            self._check_name(name, self.channels())
            channel_id = 'ch-' + uuid4().hex[:12]
            self.storage.folder(self.storage.root / channel_id, 'shared')
            with self.db:
                self.db.execute('INSERT INTO channels(id,name) VALUES (?,?)', (channel_id, name))
            self._write_log(channel_id)
            self._write_board(channel_id)
            self._notify()
            return self._channel(channel_id)

    def clear_channel(self, channel_id, *, allow_managed=True):
        with self.folder_lock:
            with self.changed:
                channel = self._channel(channel_id)
                self._check_managed_channel(channel_id, allow_managed)
                target = self.storage.log_path(Path(channel['folder']))
                with self.db:
                    count = self._delete_channel_messages(channel_id)
                    self.storage.log(target, (), replace=True)
                    self.managed.cancel_channel(channel_id)
                    self.managed.cancel_channel(channel_id)
                self._notify()
                return {'channel_id': channel_id, 'cleared': count}

    def _delete_channel_messages(self, channel_id):
        self.db.execute('DELETE FROM chat_attachments WHERE message_id IN (SELECT id FROM messages WHERE channel_id=?)', (channel_id,))
        self.db.execute('DELETE FROM deliveries WHERE message_id IN (SELECT id FROM messages WHERE channel_id=?)', (channel_id,))
        return self.db.execute('DELETE FROM messages WHERE channel_id=?', (channel_id,)).rowcount

    def rename_channel(self, payload):
        name = self._name(payload.get('name'), '채널 이름', 'errorChannelName')
        with self.changed:
            channel_id = self._channel(payload.get('channel_id'))['id']
            self._check_name(name, self.channels(), channel_id)
            with self.db:
                self.db.execute('UPDATE channels SET name=?,name_customized=1 WHERE id=?', (name, channel_id))
            self._notify()
            return self._channel(channel_id)

    def _check_managed_channel(self, channel_id, allow_managed):
        if not allow_managed and self.db.execute("""
            SELECT 1 FROM managed_agents a JOIN participants p ON p.id=a.id WHERE p.channel_id=? LIMIT 1
        """, (channel_id,)).fetchone():
            raise RequestError('Manage CLI agents on the server computer.', 403, key='managedLocalOnly')

    def delete_channel(self, channel_id, *, allow_managed=True):
        with self.folder_lock:
            with self.changed:
                channel = self._channel(channel_id)
                self._check_managed_channel(channel_id, allow_managed)
                target = self.storage.log_path(Path(channel['folder']))
                try:
                    with self.db:
                        self.managed.delete([row['id'] for row in self.db.execute(
                            'SELECT id FROM participants WHERE channel_id=?', (channel_id,),
                        )])
                        count = self._delete_channel_messages(channel_id)
                        self.db.execute("""UPDATE board_posts SET title='',body='',
                            deleted_at=COALESCE(deleted_at,strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE channel_id=?""", (channel_id,))
                        self.db.execute("UPDATE board_images SET data=X'' WHERE post_id IN (SELECT id FROM board_posts WHERE channel_id=?)", (channel_id,))
                        self.db.execute('DELETE FROM board_delivery WHERE post_id IN (SELECT id FROM board_posts WHERE channel_id=?)', (channel_id,))
                        self.db.execute('DELETE FROM board_revocations WHERE post_id IN (SELECT id FROM board_posts WHERE channel_id=?)', (channel_id,))
                        self.db.execute("""UPDATE participants SET
                            removed_at=COALESCE(removed_at,strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE channel_id=?""", (channel_id,))
                        self._write_board(channel_id, strict=True)
                        self.db.execute('DELETE FROM channels WHERE id=?', (channel_id,))
                        self.storage.log(target, (), replace=True)
                except Exception:
                    # A rolled-back database may need its filesystem exports rebuilt.
                    self.dirty_logs.add(channel_id)
                    self.dirty_boards.add(channel_id)
                    raise
                self.dirty_logs.discard(channel_id)
                self.dirty_boards.discard(channel_id)
                self._notify()
                return {'channel_id': channel_id, 'deleted': True, 'cleared': count}

    def _check_folder_scope(self, location, default):
        folders = tuple(location / kind for kind in ('shared', 'logs', 'board'))
        if location != default and location.is_relative_to(self.storage.root):
            raise RequestError('채널 기본 저장소 밖에 별도 폴더를 지정하세요.', 409, key='errorFolderScope')
        for channel in self.channels():
            root = Path(channel['folder'])
            for existing in (root / 'shared', root / 'logs', root / 'board'):
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
                self.storage.export_board(location, channel_id, self._board_records(channel_id))
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
                channel_id = self.channel_scope(payload.get('channel_id'), payload.get('agent'))
                root = Path(self._channel(channel_id)['folder'])
            if action == 'open':
                return self.storage.open_folder(root, payload.get('kind'))
            if action == 'list':
                return self.storage.list_shared(root, payload.get('path', ''))
            if action == 'read':
                return self.storage.read_shared(root, payload.get('path'))
            return self.storage.write_shared(root, payload.get('path'), payload.get('text'))

    def channel_files(self, channel_id, kind, relative, *, download=False, offset=0):
        with self.folder_lock:
            with self.changed:
                root = Path(self._channel(channel_id)['folder'])
                if kind == 'board' and channel_id in self.dirty_boards:
                    self._write_board(channel_id, strict=True)
            if download:
                return self.storage.open_file(root, kind, relative)
            return self.storage.list_files(root, kind, relative, offset)

    def _participant(self, agent_id, *, include_removed=False):
        if not isinstance(agent_id, str) or len(agent_id) > 32 or not agent_id.isascii():
            return None
        if include_removed:
            return self.db.execute('SELECT id,role,name,icon,channel_id,removed_at FROM participants WHERE id=?', (agent_id,)).fetchone()
        return self.db.execute('SELECT id,role,name,icon,channel_id FROM participants WHERE id=? AND removed_at IS NULL', (agent_id,)).fetchone()

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
        with self.changed:
            with self.db:
                agent = self._register(payload)
            self._notify_board()
            self._write_board(agent['channel_id'])
            return agent

    def _register(self, payload):
        role = payload.get('role')
        if role not in ROLES:
            raise RequestError('Codex 또는 Claude를 선택하세요.', key='errorRole')
        name = self._name(payload.get('name'), '닉네임', 'errorNickname')
        channel_id = self.channel_scope(payload.get('channel_id'))
        existing = self.db.execute('SELECT * FROM participants').fetchall()
        active = [row for row in existing if row['removed_at'] is None and row['channel_id'] == channel_id]
        self._check_name(name, active)
        if len(active) >= MAX_PARTICIPANTS:
            raise RequestError('에이전트는 64개까지 등록할 수 있습니다.', 409, key='errorAgentLimit')
        role_ids = {row['id'] for row in existing if row['role'] == role}
        number = 2
        while f'{role}-{number}' in role_ids:
            number += 1
        agent_id = f'{role}-{number}'
        self.db.execute('INSERT INTO participants(id,role,name,channel_id) VALUES (?,?,?,?)', (agent_id, role, name, channel_id))
        self.db.execute('INSERT INTO board_delivery(post_id,agent_id) SELECT id,? FROM board_posts WHERE channel_id=? AND deleted_at IS NULL', (agent_id, channel_id))
        return {'id': agent_id, 'role': role, 'name': name, 'icon': 'provider', 'channel_id': channel_id}

    def rename(self, payload):
        agent_id = payload.get('id')
        name = self._name(payload.get('name'), '닉네임', 'errorNickname')
        with self.changed:
            channel_id = self.channel_scope(payload.get('channel_id'), agent_id)
            participant = self._participant(agent_id)
            if participant is None:
                raise RequestError('등록된 에이전트를 찾을 수 없습니다.', 404, key='errorAgentMissing')
            self._check_name(name, self.db.execute('SELECT id,name FROM participants WHERE removed_at IS NULL AND channel_id=?', (channel_id,)), agent_id)
            with self.db:
                self.db.execute('UPDATE participants SET name=? WHERE id=?', (name, agent_id))
            self._notify_board()
            self._write_board(channel_id)
            return dict(participant) | {'name': name}

    def remove_participant(self, agent_id, channel_id=None, *, allow_managed=True):
        with self.changed:
            channel_id = self.channel_scope(channel_id, agent_id, include_removed=True)
            participant = self._participant(agent_id, include_removed=True)
            if participant is None:
                raise RequestError('등록된 에이전트를 찾을 수 없습니다.', 404, key='errorAgentMissing')
            if participant['removed_at'] is None:
                managed = self.managed.get(agent_id)
                if managed and not allow_managed:
                    raise RequestError('Manage CLI agents on the server computer.', 403, key='managedLocalOnly')
                ids = [agent_id]
                if managed:
                    ids.extend(row['id'] for row in self.db.execute(
                        'SELECT id FROM managed_agents WHERE parent_id=?', (agent_id,),
                    ))
                with self.db:
                    self.managed.delete(ids)
                    self.db.executemany("""
                        UPDATE participants SET removed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?
                    """, ((aid,) for aid in ids))
                    for aid in ids:
                        self.board.drop_pending(aid)
                self._notify_board()
                self._write_board(channel_id)
            return {'id': agent_id, 'removed': True}

    def set_participant_icon(self, payload):
        choice = payload.get('icon')
        if not isinstance(choice, str) or choice not in (*PARTICIPANT_ICONS, 'image'):
            raise RequestError('참가자 아이콘을 선택하세요.', key='errorParticipantIcon')
        with self.uploads.lock, self.changed:
            self.channel_scope(payload.get('channel_id'), payload.get('id'))
            participant = self._participant(payload.get('id'))
            if participant is None:
                raise RequestError('등록된 에이전트를 찾을 수 없습니다.', 404, key='errorAgentMissing')
            uploaded = []
            if choice == 'image':
                uploaded = [{'upload_id': payload.get('upload_id')}]
                _, mime, source = prepare_files(uploaded, self.uploads)[0]
                if mime not in ICON_IMAGE_TYPES:
                    raise RequestError('아이콘은 PNG, JPG, GIF 이미지로 설정하세요.', 415, key='iconImageTypes')
                data = file_data(source)
                choice = 'image:' + sha256(data).hexdigest()
            if choice != participant['icon']:
                with self.db:
                    if uploaded:
                        self.db.execute('INSERT OR REPLACE INTO participant_icons VALUES (?,?,?)',
                                        (participant['id'], mime, data))
                    else:
                        self.db.execute('DELETE FROM participant_icons WHERE agent_id=?', (participant['id'],))
                    self.db.execute('UPDATE participants SET icon=? WHERE id=?', (choice, participant['id']))
                self._notify()
            self.uploads.discard(uploaded)
            return dict(participant) | {'icon': choice}

    def participant_icon(self, agent_id, version='', channel_id=None):
        with self.changed:
            self.channel_scope(channel_id, agent_id, include_removed=True)
            participant = self._participant(agent_id, include_removed=True)
            if (participant is None or not participant['icon'].startswith('image:')
                    or (version and participant['icon'] != 'image:' + version)):
                raise RequestError('아이콘 이미지를 찾을 수 없습니다.', 404, key='errorUnavailable')
            image = self.db.execute('SELECT mime_type,data FROM participant_icons WHERE agent_id=?', (agent_id,)).fetchone()
            if image is None:
                raise RequestError('아이콘 이미지를 찾을 수 없습니다.', 404, key='errorUnavailable')
            return dict(image)

    def setup(self, agent_id, port, language='en', channel_id=None):
        from relay.cli import connection_setup
        with self.changed:
            channel_id = self.channel_scope(channel_id, agent_id)
            participant = self._participant(agent_id)
            if participant is None:
                raise RequestError('등록된 에이전트를 찾을 수 없습니다.', 404, key='errorAgentMissing')
            managed = bool(self.managed.get(agent_id))
            return {
                **dict(participant),
                **({} if managed else connection_setup(port, agent_id, participant['role'], language=language, channel_id=channel_id)),
                'managed': managed, 'icon_options': PARTICIPANT_ICONS,
                'icon_upload_extensions': ['.png', '.jpg', '.gif'],
            }

    def participants(self, channel_id=None, agent=None, *, local=False):
        with self.changed:
            channel_id = self.channel_scope(channel_id, agent)
            scope = 'AND m.channel_id=?'
            params = (channel_id,)
            pending = dict(self.db.execute(f'SELECT d.agent_id, COUNT(*) {PENDING_MESSAGES} {scope} GROUP BY d.agent_id', params))
            participants = self.db.execute('SELECT id,role,name,icon,channel_id FROM participants WHERE removed_at IS NULL AND channel_id=? ORDER BY rowid', (channel_id,)).fetchall()
            return [
                dict(p) | {
                    'waiting': p['id'] in self.waiters and (
                        not channel_id or self.waiters[p['id']] in (None, channel_id)
                    ),
                    'pending': pending.get(p['id'], 0),
                    **({'model_settings': self.participant_details.values.get(p['id'], {})} if p['role'] == 'codex' else {}),
                    **(self.managed.public(managed, local=local) if (managed := self.managed.get(p['id'])) else {}),
                }
                for p in participants
            ]

    def _archived_participants(self, messages):
        ids = {row[field] for row in messages for field in ('sender', 'recipient')} - {None, 'user', 'all'}
        if not ids:
            return []
        placeholders = ','.join('?' for _ in ids)
        return [dict(row) for row in self.db.execute(f'''
            SELECT id,role,name,icon,channel_id FROM participants WHERE id IN ({placeholders})
        ''', tuple(ids))]

    def snapshot(self, channel_id, *, viewer_address=None):
        with self.changed:
            channel = self._channel(channel_id) if channel_id is not None else None
            rows = self.db.execute(
                f'SELECT {MESSAGE_FIELDS} FROM messages m WHERE channel_id=? ORDER BY id DESC LIMIT 100', (channel_id,)
            ).fetchall()
            return {
                "revision": self.revision,
                "board_revision": str(self.board_revision),
                "channel": channel,
                "channels": self.channels(),
                "participants": self.participants(channel_id, local=loopback(viewer_address)) if channel else [],
                "managed_local": loopback(viewer_address),
                "users": self.users.snapshot(channel_id),
                "viewer": self.users.identity(viewer_address),
                "archived_participants": self._archived_participants(rows),
                "total_messages": self.db.execute('SELECT COUNT(*) FROM messages WHERE channel_id=?', (channel_id,)).fetchone()[0],
                "messages": self.chat_files.attach_metadata(reversed(rows)),
            }

    def _insert(self, channel_id, sender, recipient, body, reply_to=None, *, files=(), request_id=None, user=None,
                deliver_managed=False):
        user = user or {}
        cursor = self.db.execute(
            "INSERT INTO messages(channel_id,sender,recipient,body,reply_to,remaining,request_id,user_ip,user_name) VALUES(?,?,?,?,?,0,?,?,?)",
            (channel_id, sender, recipient, body, reply_to, request_id, user.get('ip'), user.get('name')),
        )
        message_id = cursor.lastrowid
        if recipient == 'all':
            self.db.execute("""
                INSERT INTO deliveries(message_id,agent_id)
                SELECT ?,p.id FROM participants p LEFT JOIN managed_agents a ON a.id=p.id
                WHERE p.removed_at IS NULL AND p.channel_id=? AND
                      (a.id IS NULL OR (? AND a.parent_id IS NULL AND a.session IS NOT NULL))
            """, (message_id, channel_id, deliver_managed))
        elif recipient is not None:
            self.db.execute('INSERT INTO deliveries(message_id,agent_id) VALUES (?,?)', (message_id, recipient))
        if files:
            self.chat_files.save(message_id, files)
        message = self.db.execute(f'SELECT {MESSAGE_FIELDS} FROM messages m WHERE id=?', (message_id,)).fetchone()
        return self.chat_files.attach_metadata((message,))[0]

    def announce_connection(self, payload):
        request_id = payload.get('request_id')
        try:
            if not isinstance(request_id, str) or len(request_id) != 36:
                raise ValueError
            request_id = str(UUID(request_id))
        except ValueError:
            raise RequestError('전송 요청 ID를 확인하세요.', key='errorRequestId') from None
        language = payload.get('language', 'en')
        if not isinstance(language, str) or language not in LANGUAGES:
            raise RequestError('Unsupported language.')
        agent = payload.get('agent') or ''
        with self.changed:
            channel_id = self.channel_scope(payload.get('channel_id'), agent)
            if self.managed.get(agent):
                raise RequestError('ACS connects managed agents.', 403, key='managedLocalOnly')
            existing = self.db.execute(f'SELECT {MESSAGE_FIELDS} FROM messages m WHERE request_id=?', (request_id,)).fetchone()
            if existing:
                if (existing['channel_id'] != channel_id or existing['sender'] != agent
                        or existing['recipient'] is not None or existing['reply_to'] is not None):
                    raise RequestError('전송 요청 ID가 이미 사용 중입니다.', 409, key='errorRequestUsed')
                return {'message': dict(existing), 'duplicate': True}
            with self.db:
                message = self._insert(channel_id, agent, None, prompt_catalog(language)['connected_notice'], request_id=request_id)
            self._write_log(channel_id, message)
            self._notify()
            return {'message': message, 'duplicate': False}

    def post(self, payload, *, user_address=None):
        with self.uploads.lock:
            result = self._post(payload, user_address=user_address)
            self.uploads.discard(payload.get('attachments', []))
            return result

    def _post(self, payload, *, user_address=None):
        sender = payload.get("sender")
        attachments = payload.get('attachments', [])
        body = payload.get("body", "")
        if not isinstance(body, str) or len(body.strip()) > TEXT_LIMIT or (not body.strip() and not attachments):
            raise RequestError("메시지는 1~10,000자로 입력하거나 파일을 첨부하세요.", key='errorMessageBody')
        try:
            body.encode('utf-8')
        except UnicodeError:
            raise RequestError('메시지를 UTF-8로 읽을 수 없습니다.', key='errorMessageEncoding') from None
        with self.changed:
            if sender == "user":
                channel_id = self._channel(payload.get('channel_id', DEFAULT_CHANNEL))['id']
                recipient = payload.get("recipient")
                is_local = loopback(user_address)
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
                        if (existing['channel_id'] != channel_id or existing['recipient'] != recipient
                                or existing['user_ip'] != user_address):
                            raise RequestError('전송 요청 ID가 이미 사용 중입니다.', 409, key='errorRequestUsed')
                        return {"message": self.chat_files.attach_metadata((existing,))[0], "duplicate": True}
                if recipient == 'all':
                    if not self.db.execute("""
                        SELECT 1 FROM participants p LEFT JOIN managed_agents a ON a.id=p.id
                        WHERE p.removed_at IS NULL AND p.channel_id=? AND
                              (a.id IS NULL OR (? AND a.parent_id IS NULL AND a.session IS NOT NULL)) LIMIT 1
                    """, (channel_id, is_local)).fetchone():
                        raise RequestError('메시지를 받을 에이전트를 먼저 추가하세요.', 409, key='errorAddAgentFirst')
                elif self._participant(recipient) is None or self._participant(recipient)['channel_id'] != channel_id:
                    raise RequestError('보낼 대상을 선택하세요.', key='errorChooseRecipient')
                elif managed := self.managed.get(recipient):
                    if not is_local:
                        raise RequestError('Send CLI tasks from the server computer.', 403, key='managedLocalOnly')
                    if not managed['session']:
                        raise RequestError('Wait until the CLI agent is ready.', 409, key='managedNotReady')
                files = prepare_chat_files(attachments, self.uploads)
                with self.db:
                    message = self._insert(channel_id, sender, recipient, body.strip(), files=files, request_id=request_id,
                                           user=self.users.identity(user_address), deliver_managed=is_local)
            elif self._participant(sender) is not None:
                if self.managed.get(sender):
                    raise RequestError('ACS sends replies for managed agents.', 403, key='managedLocalOnly')
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
                self.channel_scope(channel_id, sender)
                files = prepare_chat_files(attachments, self.uploads)
                body, shared_files = self._shared_png_reply(channel_id, body, len(files))
                files.extend(shared_files)
                recipient = payload.get('next_agent') if handoff else None
                if isinstance(recipient, str) and self.managed.get(recipient):
                    raise RequestError('Send CLI tasks from the server computer.', 403, key='managedLocalOnly')
                if recipient is not None and (recipient == sender or self._participant(recipient) is None
                                              or self._participant(recipient)['channel_id'] != channel_id):
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
            raise UploadError('파일은 최대 30개까지 첨부할 수 있습니다.', key='attachmentLimit')
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
                    data = handle.read(MAX_FILE_BYTES + 1)
            except OSError:
                raise UploadError(f'shared에서 PNG 파일을 열 수 없습니다: {path.name}', 404) from None
            attachments.append(validate_file(path.name, data))
        return '\n'.join(lines).strip(), attachments

    def wait_result(self, agent, seconds, channel_id=None, client_version=None):
        result = self.wait_message(agent, seconds, channel_id)
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

    def wait_message(self, agent, seconds, channel_id=None):
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
            if self.managed.get(agent):
                raise RequestError('ACS receives messages for managed agents.', 403, key='managedLocalOnly')
            channel_id = self.channel_scope(channel_id, agent)
            scope = 'AND m.channel_id=?'
            params = (agent, channel_id)
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
                    if channel_id:
                        self._channel(channel_id)
                    revoked = self.board.revoked(agent, channel_id)
                    if revoked:
                        return revoked
                    notice = self.board.unread(agent, channel_id)
                    if notice:
                        return {'state': 'board_post', 'post': notice}
                    message = self.db.execute(
                        f'SELECT {MESSAGE_FIELDS} {PENDING_MESSAGES} AND d.agent_id=? {scope} ORDER BY m.id LIMIT 1', params
                    ).fetchone()
                    if message:
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
        "/files": ("files.html", "text/html; charset=utf-8"),
        "/files.js": ("files.js", "text/javascript; charset=utf-8"),
        "/files.css": ("files.css", "text/css; charset=utf-8"),
        "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
        "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
        "/uploads.js": ("uploads.js", "text/javascript; charset=utf-8"),
        "/recipient.js": ("recipient.js", "text/javascript; charset=utf-8"),
        "/users.js": ("users.js", "text/javascript; charset=utf-8"),
        "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        "/board.js": ("board.js", "text/javascript; charset=utf-8"),
        "/style.css": ("style.css", "text/css; charset=utf-8"),
        "/vendor/icons.svg": ("vendor/icons.svg", "image/svg+xml"),
        "/vendor/openai.svg": ("vendor/openai.svg", "image/svg+xml"),
        "/vendor/anthropic.svg": ("vendor/anthropic.svg", "image/svg+xml"),
        "/vendor/PretendardVariable.woff2": ("vendor/PretendardVariable.woff2", "font/woff2"),
        **{f'/locales/{language}.json': (f'locales/{language}.json', 'application/json; charset=utf-8')
           for language in LANGUAGES},
    }

    def log_message(self, *args):
        pass

    @property
    def room(self):
        return self.server.room

    def _check_local(self, *, files_write=False):
        hosts = self.headers.get_all('Host', [])
        origins = self.headers.get_all('Origin', [])
        host = hosts[0] if len(hosts) == 1 else ''
        hostname, separator, port = host.partition(':')
        valid_port = port == str(self.server.server_port) if separator else self.server.server_port == 80
        # Reject arbitrary DNS names, public peers, and cross-origin requests.
        if (not local_address(self.client_address[0]) or not valid_port
                or not (hostname == 'localhost' or local_address(hostname))
                or (origins and origins != [f'http://{host}'])):
            raise RequestError("로컬 또는 사내망의 동일 출처 접속만 허용됩니다.", 403, key='errorNetworkAccess')
        if files_write and not ip_address(self.client_address[0]).is_loopback:
            raise RequestError('파일 저장과 서버 폴더 설정은 서버 PC에서만 변경할 수 있습니다.',
                               403, key='errorFilesLocalOnly')

    def _check_managed_local(self):
        if not loopback(self.client_address[0]):
            raise RequestError('Manage CLI agents on the server computer.', 403, key='managedLocalOnly')

    def _bytes(self, status, data, content_type, *, download_name=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if download_name is not None:
            self.send_header('Content-Disposition', f"attachment; filename=\"download\"; filename*=UTF-8''{quote(download_name, safe='')}")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' blob:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, data, status=200):
        self._bytes(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8")

    def _attachment(self, attachment, download=False):
        mime = attachment['mime_type']
        preview = mime in ('image/png', 'image/jpeg', 'image/gif', 'image/webp', 'video/mp4')
        name = attachment['name'] if download or not preview else None
        self._bytes(200, attachment['data'], mime, download_name=name)

    def _download(self, channel_id, kind, relative):
        handle, name = self.room.channel_files(channel_id, kind, relative, download=True)
        with handle:
            remaining = os.fstat(handle.fileno()).st_size
            self.connection.settimeout(30)
            self.close_connection = True
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Disposition', f"attachment; filename=\"download\"; filename*=UTF-8''{quote(name, safe='')}")
            self.send_header('Content-Length', str(remaining))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'none'; sandbox")
            self.end_headers()
            try:
                while remaining:
                    chunk = handle.read(min(remaining, 128 * 1024))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except OSError:
                # Headers are already sent; close an interrupted transfer cleanly.
                pass

    @staticmethod
    def _number(params, name):
        raw = params.get(name, [None])[0]
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            raise RequestError('유효한 게시글 또는 이미지 번호를 입력하세요.', key='errorPostId') from None

    def _events(self, channel_id=DEFAULT_CHANNEL, client_id=None):
        with self.room.changed:
            if channel_id is not None:
                self.room._channel(channel_id)
            if self.room.streams >= 12:
                raise RequestError("브라우저 연결이 너무 많습니다.", 429)
            self.room.streams += 1
            lease = self.room.users.open_session(self.client_address[0], channel_id, client_id or uuid4().hex)
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
                self.room.users.close_session(lease)

    def _saved_language(self):
        try:
            return self.server.settings.language()
        except (ConfigError, UnicodeError, OSError) as error:
            raise RequestError('settings.ini could not be read.', 503) from error

    def do_GET(self):
        try:
            self._check_local()
            url = urlsplit(self.path)
            params = parse_qs(url.query, keep_blank_values=url.path in ('/api/room', '/api/events'))
            channel_id = params.get('channel', [None])[0]
            agent = params.get('agent', [None])[0]
            if url.path == '/upload-policy.js':
                code = 'const uploadPolicy = Object.freeze(' + json.dumps(upload_policy()) + ');'
                self._bytes(200, code.encode('utf-8'), 'text/javascript; charset=utf-8')
            elif url.path in self.static:
                filename, content_type = self.static[url.path]
                self._bytes(200, files("relay.web").joinpath(filename).read_bytes(), content_type)
            elif url.path == "/api/health":
                self._json({"service": APP_NAME, "pid": os.getpid(), "version": 11,
                            "listen_host": self.server.server_address[0],
                            "mcp_version": MCP_VERSION, "receive_seconds": DEFAULT_WAIT_SECONDS,
                            "png_reply": True, "legacy_png_reply": True})
            elif url.path == "/api/room":
                selected = params.get('channel', [DEFAULT_CHANNEL])[0] or None
                self._json(self.room.snapshot(selected, viewer_address=self.client_address[0]))
            elif url.path == "/api/events":
                client_id = params.get('client', [None])[0]
                if client_id is not None and (not client_id or len(client_id) > 64):
                    raise RequestError('브라우저 연결 ID를 확인하세요.', key='errorRequestId')
                self._events(params.get('channel', [DEFAULT_CHANNEL])[0] or None, client_id)
            elif url.path == '/api/settings':
                self._json({'language': self._saved_language()})
            elif url.path == '/api/channels':
                with self.room.changed:
                    channels = ([self.room._channel(self.room.channel_scope(channel_id, agent))]
                                if agent else self.room.channels())
                self._json({'channels': channels})
            elif url.path in ('/api/files', '/api/files/download'):
                kind = params.get('kind', ['shared'])[0]
                relative = params.get('path', [''])[0]
                if url.path.endswith('/download'):
                    self._download(channel_id or DEFAULT_CHANNEL, kind, relative)
                else:
                    self._json(self.room.channel_files(channel_id or DEFAULT_CHANNEL, kind, relative,
                                                       offset=self._number(params, 'offset') or 0))
            elif url.path == '/api/setup':
                agent_id = params.get('agent', [''])[0]
                language = params.get('lang', [None])[0]
                if language not in LANGUAGES:
                    language = self._saved_language()
                self._json(self.room.setup(agent_id, self.server.server_port, language, channel_id))
            elif url.path == '/api/participants':
                self._json({'participants': self.room.participants(channel_id, agent)})
            elif url.path == '/api/participants/icon':
                image = self.room.participant_icon(params.get('agent', [''])[0], params.get('v', [''])[0], channel_id)
                self._bytes(200, image['data'], image['mime_type'])
            elif url.path == '/api/board':
                self._json(self.room.list_board_posts(self._number(params, 'before'), channel_id, agent))
            elif url.path == '/api/board/post':
                self._json(self.room.read_board_post(self._number(params, 'id'), channel_id=channel_id))
            elif url.path in ('/api/board/image', '/api/board/attachment'):
                image = self.room.board_image(self._number(params, 'id'), channel_id, agent)
                self._attachment(image, params.get('download') == ['1'])
            elif url.path == '/api/messages/attachment':
                agent = params.get('agent', [None])[0]
                attachment = self.room.chat_attachment(self._number(params, 'id'), agent, channel_id)
                self._attachment(attachment, params.get('download') == ['1'])
            elif url.path == "/api/wait":
                try:
                    seconds = int(params.get("seconds", [str(DEFAULT_WAIT_SECONDS)])[0])
                except ValueError:
                    raise RequestError("대기 시간은 초 단위 정수로 입력하세요.") from None
                self._json(self.room.wait_result(params.get("agent", [""])[0], seconds, channel_id,
                                                self.headers.get('X-Local-Relay-Client')))
            else:
                raise RequestError("페이지를 찾을 수 없습니다.", 404, key='errorUnavailable')
        except ApiError as exc:
            self._json(exc.response(), exc.status)
        except (ConnectionError, TimeoutError):
            pass

        except OSError:
            error = ApiError('채널 폴더에 접근할 수 없습니다. 파일 사용 상태와 권한을 확인하세요.', 503, key='errorFolderAccess')
            self._json(error.response(), error.status)

    def do_POST(self):
        try:
            self._check_local(files_write=self.path in ('/api/channels/folder', '/api/channels/open', '/api/shared/write'))
            self.connection.settimeout(8)
            self.close_connection = True
            url = urlsplit(self.path)
            if url.path == '/api/uploads':
                if self.headers.get_content_type() != 'application/octet-stream' or self.headers.get('X-Local-Relay') != '1':
                    raise RequestError('첨부 요청 헤더를 확인하세요.', 415, key='errorRequestHeaders')
                sizes = self.headers.get_all('Content-Length', [])
                if self.headers.get('Transfer-Encoding') or len(sizes) != 1 or len(sizes[0]) > 12 or not sizes[0].isascii() or not sizes[0].isdigit():
                    raise RequestError('첨부 파일 크기를 확인하세요.', key='errorUpload')
                self.connection.settimeout(60)
                name = parse_qs(url.query).get('name', [''])[0]
                self._json(self.room.uploads.stage(self.rfile, int(sizes[0]), name), 201)
                return
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
            if self.path == '/api/uploads/discard':
                items = payload.get('attachments')
                if not isinstance(items, list) or len(items) > MAX_FILES:
                    raise RequestError('첨부 목록을 확인하세요.', key='errorUpload')
                with self.room.uploads.lock:
                    self.room.uploads.discard(items)
                self._json({'discarded': True})
            elif self.path == "/api/messages":
                self._json(self.room.post(payload, user_address=self.client_address[0]))
            elif self.path == '/api/participants/connected':
                self._json(self.room.announce_connection(payload))
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
                self._json(self.room.delete_board_post(payload.get('post_id'), payload.get('channel_id')))
            elif self.path == '/api/board/read':
                self._json(self.room.read_board_post(payload.get('post_id'), payload.get('agent') or '', payload.get('channel_id')))
            elif self.path == '/api/participants':
                self._json(self.room.register(payload), 201)
            elif self.path == '/api/managed/agents':
                self._check_managed_local()
                language = payload.get('language')
                self._json(self.room.managed.create(payload, language if language in LANGUAGES else 'en'), 202)
            elif self.path == '/api/managed/auth':
                self._check_managed_local()
                self._json(self.room.managed.login(payload.get('id'), payload.get('channel_id')), 202)
            elif self.path == '/api/managed/retry':
                self._check_managed_local()
                self._json(self.room.managed.retry(payload.get('id'), payload.get('channel_id')), 202)
            elif self.path == '/api/participants/rename':
                self._json(self.room.rename(payload))
            elif self.path == '/api/participants/icon':
                self._json(self.room.set_participant_icon(payload))
            elif self.path == '/api/participants/delete':
                self._json(self.room.remove_participant(payload.get('id'), payload.get('channel_id'),
                                                        allow_managed=loopback(self.client_address[0])))
            elif self.path == '/api/channels':
                self._json(self.room.create_channel(payload), 201)
            elif self.path == '/api/channels/rename':
                self._json(self.room.rename_channel(payload))
            elif self.path == '/api/channels/delete':
                self._json(self.room.delete_channel(payload.get('channel_id'), allow_managed=loopback(self.client_address[0])))
            elif self.path == '/api/channels/open':
                self._json(self.room.storage_action('open', payload))
            elif self.path == '/api/channels/folder':
                self._json(self.room.set_folder(payload))
            elif self.path == '/api/channels/clear':
                self._json(self.room.clear_channel(payload.get('channel_id'), allow_managed=loopback(self.client_address[0])))
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


class LocalHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        # Keep the numeric bind address so startup avoids reverse DNS.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def serve(port, db_path, *, settings=None):
    with LocalHTTPServer((LISTEN_HOST, port), Handler) as server:
        server.settings = settings if settings is not None else Settings()
        server.room = Room(db_path)
        server.room.managed.start()
        server.room.participant_details.start()
        print(f"{APP_NAME}: http://{LOOPBACK}:{server.server_port}/", flush=True)
        print(f"LAN: http://<this computer's LAN IPv4 address>:{server.server_port}/", flush=True)
        def terminate(_signal, _frame):
            raise KeyboardInterrupt

        previous = signal.signal(signal.SIGTERM, terminate) if current_thread() is main_thread() else None
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)
            server.room.participant_details.stopped.set()
            server.room.managed.close()
