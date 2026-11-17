from base64 import b64decode
from binascii import Error as Base64Error

from relay.errors import ApiError

PNG_BYTES = 4 * 1024 * 1024
MAX_FILES = 4
MAX_UPLOAD_REQUEST = MAX_FILES * (((PNG_BYTES + 2) // 3) * 4) + 80_000


class UploadError(ApiError):
    pass


def decode_upload(attachment, byte_limit=PNG_BYTES):
    if not isinstance(attachment, dict):
        raise UploadError('첨부 파일을 확인하세요.', key='errorUpload')
    name, data = attachment.get('name'), attachment.get('data')
    if not isinstance(name, str) or not name.strip() or len(name) > 160 or not name.isprintable():
        raise UploadError('파일 이름은 1~160자로 입력하세요.', key='errorFilenameLength')
    if not isinstance(data, str):
        raise UploadError('첨부 파일을 확인하세요.', key='errorUpload')
    if len(data) > ((byte_limit + 2) // 3) * 4:
        raise UploadError(f'첨부 파일은 {byte_limit // 1024} KiB까지 올릴 수 있습니다.', 413,
                          key='errorUploadLimit', limit=byte_limit // 1024)
    try:
        decoded = b64decode(data, validate=True)
    except (Base64Error, ValueError):
        raise UploadError('첨부 파일을 읽을 수 없습니다.', key='uploadReadFailed') from None
    if len(decoded) > byte_limit:
        raise UploadError(f'첨부 파일은 {byte_limit // 1024} KiB까지 올릴 수 있습니다.', 413,
                          key='errorUploadLimit', limit=byte_limit // 1024)
    return name.strip(), decoded


def is_png(data):
    return data.startswith(b'\x89PNG\r\n\x1a\n') and data[12:16] == b'IHDR'
