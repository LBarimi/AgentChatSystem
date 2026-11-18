"""One attachment policy for chat, board, browser uploads, and CLI/MCP replies."""
from base64 import b64decode
from binascii import Error as Base64Error
from pathlib import Path, PureWindowsPath
import re
from threading import RLock
from time import time
from uuid import uuid4

from relay.errors import ApiError

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES = 30
MAX_UPLOAD_REQUEST = MAX_FILES * (((MAX_FILE_BYTES + 2) // 3) * 4) + 128_000
UPLOAD_TTL = 3600
MAX_PENDING_BYTES = 2 * 1024 * 1024 * 1024
MIME_TYPES = {
    '.png': 'image/png', '.jpg': 'image/jpeg', '.txt': 'text/plain',
    '.csv': 'text/csv', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.xlsm': 'application/vnd.ms-excel.sheet.macroEnabled.12', '.json': 'application/json',
    '.xml': 'application/xml', '.pdf': 'application/pdf',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.mp4': 'video/mp4', '.gif': 'image/gif',
}


class UploadError(ApiError):
    pass


def upload_policy():
    return {'extensions': list(MIME_TYPES), 'max_files': MAX_FILES, 'max_file_bytes': MAX_FILE_BYTES}


def upload_name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 160 or not value.isprintable():
        raise UploadError('파일 이름은 1~160자로 입력하세요.', key='errorFilenameLength')
    name = value.strip()
    if any(char in name for char in '/\\<>:"|?*') or PureWindowsPath(name).is_reserved():
        raise UploadError('첨부 파일 이름을 확인하세요.', key='errorAttachmentName')
    suffix = PureWindowsPath(name).suffix.lower()
    if suffix not in MIME_TYPES:
        raise UploadError('허용된 확장자의 파일을 첨부하세요.', 415, key='attachmentTypes')
    return name, MIME_TYPES[suffix]


def upload_size(size):
    if size > MAX_FILE_BYTES:
        raise UploadError('파일은 개당 10 MB까지 첨부할 수 있습니다.', 413, key='attachmentSizeLimit')


def validate_file(name, data):
    name, mime = upload_name(name)
    upload_size(len(data))
    # Documents are downloaded, never executed or interpreted as HTML.
    valid = {
        'image/png': lambda: is_png(data),
        'image/jpeg': lambda: data.startswith(b'\xff\xd8\xff'),
        'image/gif': lambda: data[:6] in (b'GIF87a', b'GIF89a'),
        'video/mp4': lambda: len(data) >= 12 and data[4:8] == b'ftyp',
    }
    if mime in valid and not valid[mime]():
        raise UploadError('파일 내용과 확장자가 일치하는지 확인하세요.', 415, key='attachmentFormat')
    return name, mime, data


def decode_upload(attachment):
    if not isinstance(attachment, dict):
        raise UploadError('첨부 파일을 확인하세요.', key='errorUpload')
    name, _ = upload_name(attachment.get('name'))
    data = attachment.get('data')
    if not isinstance(data, str):
        raise UploadError('첨부 파일을 확인하세요.', key='errorUpload')
    if len(data) > ((MAX_FILE_BYTES + 2) // 3) * 4:
        upload_size(MAX_FILE_BYTES + 1)
    try:
        decoded = b64decode(data, validate=True)
    except (Base64Error, ValueError):
        raise UploadError('첨부 파일을 읽을 수 없습니다.', key='uploadReadFailed') from None
    return validate_file(name, decoded)


def is_png(data):
    return data.startswith(b'\x89PNG\r\n\x1a\n') and data[12:16] == b'IHDR'


def prepare_files(items, uploads=None):
    if not isinstance(items, list) or len(items) > MAX_FILES:
        raise UploadError('파일은 한 번에 최대 30개까지 첨부할 수 있습니다.', key='attachmentLimit')
    prepared = []
    for item in items:
        if isinstance(item, dict) and 'upload_id' in item:
            if uploads is None:
                raise UploadError('첨부 파일을 다시 올려주세요.', 409, key='uploadExpired')
            prepared.append(uploads.resolve(item['upload_id']))
        else:
            prepared.append(decode_upload(item))
    return prepared


def file_data(value):
    # A path here comes only from UploadStore, never from an API payload.
    return value.read_bytes() if isinstance(value, Path) else value


class UploadStore:
    """Temporary opaque references keep large attachment batches out of JSON."""
    def __init__(self, root):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.records = {}
        self.active = {}
        self.last_cleanup = 0
        self.cleanup()

    def cleanup(self):
        with self.lock:
            now = time()
            if now - self.last_cleanup < 60:
                return
            self.last_cleanup = now
            for path in self.root.glob('*.upload'):
                if path.stem not in self.active and re.fullmatch(r'[0-9a-f]{32}\.upload', path.name):
                    try:
                        if now - path.stat().st_mtime >= UPLOAD_TTL:
                            path.unlink(missing_ok=True)
                            self.records.pop(path.stem, None)
                    except OSError:
                        pass

    def stage(self, stream, size, name):
        name, mime = upload_name(name)
        if type(size) is not int or size < 0:
            raise UploadError('첨부 파일 크기를 확인하세요.', key='errorUpload')
        upload_size(size)
        with self.lock:
            self.cleanup()
            stored = sum(path.stat().st_size for path in self.root.glob('*.upload') if path.stem not in self.active)
            if stored + sum(self.active.values()) + size > MAX_PENDING_BYTES:
                raise UploadError('임시 첨부 공간이 가득 찼습니다. 잠시 후 다시 시도하세요.', 429, key='uploadStorageFull')
            token = uuid4().hex
            path = self.root / (token + '.upload')
            self.active[token] = size
        try:
            with path.open('xb') as handle:
                remaining = size
                while remaining:
                    block = stream.read(min(remaining, 128 * 1024))
                    if not block:
                        raise UploadError('첨부 파일 전송이 중단되었습니다.', key='uploadReadFailed')
                    handle.write(block)
                    remaining -= len(block)
            validate_file(name, path.read_bytes())
            with self.lock:
                self.records[token] = (name, mime, path)
            return {'upload_id': token}
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            with self.lock:
                self.active.pop(token, None)


    def resolve(self, token):
        if not isinstance(token, str) or token not in self.records:
            raise UploadError('첨부 파일을 다시 올려주세요.', 409, key='uploadExpired')
        result = self.records[token]
        if not result[2].is_file() or time() - result[2].stat().st_mtime >= UPLOAD_TTL:
            raise UploadError('첨부 파일을 다시 올려주세요.', 409, key='uploadExpired')
        # The caller holds lock until its transaction has read each file.
        return result

    def discard(self, items):
        if not isinstance(items, list):
            return
        for item in items:
            token = item.get('upload_id') if isinstance(item, dict) else None
            if isinstance(token, str) and token in self.records:
                _, _, path = self.records[token]
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue
                self.records.pop(token, None)
