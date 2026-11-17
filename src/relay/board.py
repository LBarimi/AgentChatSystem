from uuid import UUID
from relay.uploads import MAX_FILES, decode_upload, is_png

from relay.errors import ApiError

PAGE_SIZE = 20


class BoardError(ApiError):
    pass


def record_id(value):
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise BoardError('유효한 게시글 또는 이미지 번호를 입력하세요.', key='errorPostId')
    return value


def _image_upload(attachment):
    name, decoded = decode_upload(attachment)
    if is_png(decoded):
        mime = 'image/png'
    elif decoded.startswith(b'\xff\xd8\xff') and decoded.endswith(b'\xff\xd9'):
        mime = 'image/jpeg'
    elif decoded.startswith(b'RIFF') and decoded[8:12] == b'WEBP':
        mime = 'image/webp'
    else:
        raise BoardError('PNG, JPG 또는 WebP 이미지를 올리세요.', 415, key='errorImageType')
    return name, mime, decoded


def prepare_post(payload):
    request_id, title, body = (payload.get(key) for key in ('request_id', 'title', 'body'))
    if not isinstance(request_id, str) or len(request_id) != 36:
        raise BoardError('글쓰기 요청을 확인하세요.', key='errorPostRequest')
    try:
        request_id = str(UUID(request_id))
    except ValueError:
        raise BoardError('글쓰기 요청을 확인하세요.', key='errorPostRequest') from None
    title = ' '.join(title.split()) if isinstance(title, str) else ''
    body = body.strip() if isinstance(body, str) else None
    if not title or len(title) > 100 or not title.isprintable():
        raise BoardError('제목은 1~100자로 입력하세요.', key='errorPostTitle')
    if body is None or len(body) > 10000:
        raise BoardError('본문은 10,000자까지 입력할 수 있습니다.', key='errorPostLength')
    try:
        body.encode('utf-8')
    except UnicodeError:
        raise BoardError('본문을 UTF-8로 읽을 수 없습니다.', key='errorPostEncoding') from None
    attachments = payload.get('images', [])
    if not isinstance(attachments, list) or len(attachments) > MAX_FILES:
        raise BoardError('이미지는 최대 4장까지 올릴 수 있습니다.', key='errorBoardImageLimit')
    if not body and not attachments:
        raise BoardError('본문을 쓰거나 이미지를 첨부하세요.', key='errorPostBody')
    return request_id, title, body, [_image_upload(image) for image in attachments]


class Board:
    def __init__(self, db):
        self.db = db
        db.executescript("""
            CREATE TABLE IF NOT EXISTS board_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            CREATE TABLE IF NOT EXISTS board_images (
                id INTEGER PRIMARY KEY,
                post_id INTEGER NOT NULL REFERENCES board_posts(id),
                name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                data BLOB NOT NULL
            );
            CREATE INDEX IF NOT EXISTS board_images_by_post ON board_images(post_id,id);
            CREATE TABLE IF NOT EXISTS board_delivery (
                post_id INTEGER NOT NULL REFERENCES board_posts(id),
                agent_id TEXT NOT NULL REFERENCES participants(id),
                read_at TEXT,
                PRIMARY KEY (post_id,agent_id)
            );
            CREATE INDEX IF NOT EXISTS board_unread ON board_delivery(agent_id,post_id) WHERE read_at IS NULL;
            CREATE TABLE IF NOT EXISTS board_revocations (
                post_id INTEGER NOT NULL REFERENCES board_posts(id),
                agent_id TEXT NOT NULL REFERENCES participants(id),
                PRIMARY KEY (agent_id,post_id)
            );
        """)
        columns = {row['name'] for row in db.execute('PRAGMA table_info(board_posts)')}
        if 'deleted_at' not in columns:
            with db:
                db.execute('ALTER TABLE board_posts ADD COLUMN deleted_at TEXT')

    def create(self, request_id, title, body, images):
        existing = self.db.execute('SELECT id FROM board_posts WHERE request_id=?', (request_id,)).fetchone()
        if existing:
            return self.post(existing['id']), False
        with self.db:
            post_id = self.db.execute(
                'INSERT INTO board_posts(request_id,title,body) VALUES (?,?,?)', (request_id, title, body)
            ).lastrowid
            self.db.executemany(
                'INSERT INTO board_images(post_id,name,mime_type,data) VALUES (?,?,?,?)',
                ((post_id, name, mime, data) for name, mime, data in images),
            )
            self.db.execute('INSERT INTO board_delivery(post_id,agent_id) SELECT ?,id FROM participants WHERE removed_at IS NULL', (post_id,))
        return self.post(post_id), True

    def list(self, before=None):
        scope = 'AND b.id < ?' if before is not None else ''
        params = (record_id(before),) if before is not None else ()
        rows = self.db.execute(f'''
            SELECT b.id,b.title,substr(b.body,1,160) AS excerpt,b.created_at,
                (SELECT COUNT(*) FROM board_images i WHERE i.post_id=b.id) AS image_count,
                (SELECT COUNT(*) FROM board_delivery d WHERE d.post_id=b.id AND d.read_at IS NOT NULL) AS read_count,
                (SELECT COUNT(*) FROM board_delivery d WHERE d.post_id=b.id) AS recipient_count
            FROM board_posts b WHERE b.deleted_at IS NULL {scope} ORDER BY b.id DESC LIMIT ?
        ''', (*params, PAGE_SIZE + 1)).fetchall()
        return {
            'posts': [dict(row) for row in rows[:PAGE_SIZE]],
            'next_before': rows[PAGE_SIZE - 1]['id'] if len(rows) > PAGE_SIZE else None,
        }

    def post(self, post_id, receipts=False, include_deleted=False):
        row = self.db.execute(
            'SELECT id,title,body,created_at,deleted_at FROM board_posts WHERE id=?', (record_id(post_id),)
        ).fetchone()
        if row is None:
            raise BoardError('게시글을 찾을 수 없습니다.', 404, key='errorPostMissing')
        if row['deleted_at']:
            if include_deleted:
                return {'id': post_id, 'status': 'deleted', 'deleted_at': row['deleted_at']}
            raise BoardError('삭제된 게시글입니다.', 410, key='errorPostDeleted')
        images = self.db.execute(
            'SELECT id,name,mime_type,length(data) AS size_bytes FROM board_images WHERE post_id=? ORDER BY id', (post_id,)
        )
        result = dict(row) | {'images': [dict(image) for image in images]}
        del result['deleted_at']
        if receipts:
            result['recipients'] = [dict(recipient) for recipient in self.db.execute('''
                SELECT p.id,p.name,d.read_at FROM board_delivery d
                JOIN participants p ON p.id=d.agent_id WHERE d.post_id=? ORDER BY p.rowid
            ''', (post_id,))]
        return result

    def unread(self, agent):
        row = self.db.execute('''
            SELECT d.post_id FROM board_delivery d JOIN board_posts b ON b.id=d.post_id
            WHERE d.agent_id=? AND d.read_at IS NULL AND b.deleted_at IS NULL ORDER BY d.post_id LIMIT 1
        ''', (agent,)).fetchone()
        return self.post(row['post_id']) if row else None

    def delete(self, post_id):
        with self.db:
            changed = self.db.execute("""
                UPDATE board_posts SET title='',body='',deleted_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id=? AND deleted_at IS NULL
            """, (record_id(post_id),)).rowcount > 0
            if changed:
                # Keep image IDs reserved so an old link can never open a different attachment.
                self.db.execute("UPDATE board_images SET name='',data=X'' WHERE post_id=?", (post_id,))
                self.db.execute('INSERT INTO board_revocations(post_id,agent_id) SELECT ?,id FROM participants WHERE removed_at IS NULL', (post_id,))
        return self.post(post_id, include_deleted=True), changed

    def revoked(self, agent):
        row = self.db.execute('''
            SELECT b.id,b.deleted_at FROM board_revocations r JOIN board_posts b ON b.id=r.post_id
            WHERE r.agent_id=? ORDER BY b.deleted_at,b.id LIMIT 1
        ''', (agent,)).fetchone()
        return {'state': 'board_deleted', 'post_id': row['id'], 'deleted_at': row['deleted_at']} if row else None

    def acknowledge_revocation(self, post_id, agent):
        with self.db:
            self.db.execute('DELETE FROM board_revocations WHERE post_id=? AND agent_id=?', (post_id, agent))

    def drop_pending(self, agent):
        self.db.execute('DELETE FROM board_delivery WHERE agent_id=? AND read_at IS NULL', (agent,))
        self.db.execute('DELETE FROM board_revocations WHERE agent_id=?', (agent,))

    def mark_read(self, post_id, agent):
        with self.db:
            return self.db.execute("""
                UPDATE board_delivery SET read_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE post_id=? AND agent_id=? AND read_at IS NULL
            """, (post_id, agent)).rowcount > 0

    def image(self, image_id):
        row = self.db.execute(
            '''SELECT i.name,i.mime_type,i.data FROM board_images i JOIN board_posts b ON b.id=i.post_id
               WHERE i.id=? AND b.deleted_at IS NULL''', (record_id(image_id),)
        ).fetchone()
        if row is None:
            raise BoardError('첨부 이미지를 찾을 수 없습니다.', 404, key='errorImageMissing')
        return row
