from relay.uploads import UploadError, file_data, prepare_files


def prepare_chat_files(attachments, uploads=None):
    return prepare_files(attachments, uploads)


class ChatFiles:
    def __init__(self, db):
        self.db = db
        db.executescript("""
            CREATE TABLE IF NOT EXISTS chat_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL REFERENCES messages(id),
                name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                data BLOB NOT NULL
            );
            CREATE INDEX IF NOT EXISTS chat_attachments_message ON chat_attachments(message_id,id);
        """)
        schema = db.execute("SELECT sql FROM sqlite_master WHERE name='chat_attachments'").fetchone()[0]
        if 'AUTOINCREMENT' not in schema.upper():
            with db:
                db.execute('ALTER TABLE chat_attachments RENAME TO chat_attachments_previous')
                db.execute('DROP INDEX chat_attachments_message')
                db.execute('''
                    CREATE TABLE chat_attachments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        message_id INTEGER NOT NULL REFERENCES messages(id),
                        name TEXT NOT NULL, mime_type TEXT NOT NULL, data BLOB NOT NULL
                    )
                ''')
                db.execute('INSERT INTO chat_attachments SELECT * FROM chat_attachments_previous')
                db.execute('DROP TABLE chat_attachments_previous')
                db.execute('CREATE INDEX chat_attachments_message ON chat_attachments(message_id,id)')

    def save(self, message_id, files):
        self.db.executemany(
            'INSERT INTO chat_attachments(message_id,name,mime_type,data) VALUES (?,?,?,?)',
            ((message_id, name, mime, file_data(data)) for name, mime, data in files),
        )

    def attach_metadata(self, rows):
        messages = {row['id']: dict(row) for row in rows}
        if messages:
            placeholders = ','.join('?' for _ in messages)
            attachments = self.db.execute(f'''
                SELECT id,message_id,name,mime_type,length(data) AS size_bytes
                FROM chat_attachments WHERE message_id IN ({placeholders}) ORDER BY id
            ''', tuple(messages))
            for row in attachments:
                metadata = dict(row)
                messages[metadata.pop('message_id')].setdefault('attachments', []).append(metadata)
        return list(messages.values())

    def get(self, attachment_id, agent=None):
        if type(attachment_id) is not int or not 1 <= attachment_id <= 2**63 - 1:
            raise UploadError('유효한 첨부 파일 번호를 입력하세요.', key='errorAttachmentId')
        row = self.db.execute('''
            SELECT a.name,a.mime_type,a.data FROM chat_attachments a JOIN messages m ON m.id=a.message_id
            WHERE a.id=? AND (? IS NULL OR m.sender=? OR EXISTS(
                SELECT 1 FROM deliveries d WHERE d.message_id=m.id AND d.agent_id=?
            ))
        ''', (attachment_id, agent, agent, agent)).fetchone()
        if row is None:
            raise UploadError('첨부 파일을 찾을 수 없습니다.', 404, key='errorAttachmentMissing')
        return row
