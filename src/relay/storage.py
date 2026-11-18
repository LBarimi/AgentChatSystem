from contextlib import contextmanager
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import stat
import subprocess
import sys
from tempfile import NamedTemporaryFile
from threading import Thread

from relay.errors import ApiError

TEXT_BYTES = 256 * 1024


class StorageError(ApiError):
    pass


@contextmanager
def atomic_text(target):
    temporary = NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n', dir=target.parent, delete=False)
    try:
        with temporary:
            yield temporary
        os.replace(temporary.name, target)
    finally:
        if os.path.exists(temporary.name):
            os.unlink(temporary.name)


class ChannelStorage:
    def __init__(self, storage_dir):
        self.root = storage_dir.resolve() / 'channels'

    def _ordinary(self, path):
        # Don't traverse junctions or symbolic links created in a shared folder.
        try:
            resolved = path.resolve()
        except RuntimeError:
            raise StorageError('공유 경로를 확인할 수 없습니다.', 409, key='errorSharedPath') from None
        if path.is_symlink() or resolved != path:
            raise StorageError('공유 경로에는 바로가기나 심볼릭 링크를 사용할 수 없습니다.', 409, key='errorSharedLink')
        return path

    def location(self, value, channel_id):
        if not isinstance(value, str) or len(value) > 1024:
            raise StorageError('폴더의 절대 경로를 입력하세요.', key='errorAbsolutePath')
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        if not value:
            return self.root / channel_id
        path = Path(value)
        if os.name == 'nt' and (not path.is_absolute() or len(path.drive) != 2 or path.drive[1] != ':'):
            raise StorageError('드라이브 문자로 시작하는 로컬 폴더의 절대 경로를 입력하세요.', key='errorLocalFolder')
        if not path.is_absolute():
            raise StorageError('폴더의 절대 경로를 입력하세요.', key='errorAbsolutePath')
        if any(
            not part.isprintable() or (os.name == 'nt' and (
                any(char in part for char in '<>:"|?*')
                or (part != '..' and part.endswith(('.', ' '))) or PureWindowsPath(part).is_reserved()
            ))
            for part in path.parts[1:]
        ):
            raise StorageError('폴더 경로에 사용할 수 없는 이름이 있습니다.', key='errorFolderName')
        try:
            return path.resolve()
        except RuntimeError:
            raise StorageError('폴더 경로를 확인할 수 없습니다.', key='errorFolderCheck') from None

    def folder(self, root, kind):
        if kind not in ('shared', 'logs'):
            raise StorageError('shared 또는 logs 폴더를 선택하세요.', key='errorSharedOrLogs')
        path = root / kind
        self._ordinary(path).mkdir(parents=True, exist_ok=True)
        return self._ordinary(path)

    def describe(self, channel):
        default = self.root / channel['id']
        root = Path(channel['folder']) if channel['folder'] else default
        return {
            'id': channel['id'],
            'name': channel['name'],
            'default_name': channel['id'] == 'general' and not channel['name_customized'],
            'folder': str(root),
            'default_folder': str(default),
            'shared': str(root / 'shared'),
            'logs': str(root / 'logs'),
        }

    def copy_shared(self, source_root, target_root):
        source = self._ordinary(source_root / 'shared')
        if not source.is_dir():
            raise StorageError('기존 shared 폴더에 접근할 수 없습니다. 파일 복사를 끄고 위치를 바꿀 수 있습니다.', 409, key='errorCopyAccess')
        target = self.folder(target_root, 'shared')
        directories = []
        files = []
        for entry in source.rglob('*'):
            self._ordinary(entry)
            relative = entry.relative_to(source)
            destination = self._ordinary(target / relative)
            if entry.is_dir():
                if destination.exists() and not destination.is_dir():
                    raise StorageError(f'새 위치에서 이름이 겹칩니다. {relative}', 409,
                                       key='errorCopyName', path=str(relative))
                directories.append(destination)
            elif entry.is_file():
                if destination.exists():
                    if not destination.is_file() or not self._same_contents(entry, destination):
                        raise StorageError(f'새 위치에 내용이 다른 파일이 있습니다. {relative}', 409,
                                           key='errorCopyContents', path=str(relative))
                    continue
                files.append((entry, destination))
        for directory in directories:
            self._ordinary(directory).mkdir(parents=True, exist_ok=True)
        for entry, destination in files:
            with self._ordinary(entry).open('rb') as original:
                destination = self._ordinary(destination)
                with destination.open('xb') as copy:
                    try:
                        shutil.copyfileobj(original, copy)
                        copy.flush()
                    except OSError:
                        copy.close()
                        destination.unlink(missing_ok=True)
                        raise

    @staticmethod
    def _same_contents(first, second):
        if first.stat().st_size != second.stat().st_size:
            return False
        with first.open('rb') as left, second.open('rb') as right:
            while chunk := left.read(128 * 1024):
                if chunk != right.read(len(chunk)):
                    return False
            return not right.read(1)

    def open_folder(self, root, kind):
        path = self.folder(root, kind)
        try:
            if os.name == 'nt':
                os.startfile(path, 'explore')
            else:
                opener = 'open' if sys.platform == 'darwin' else 'xdg-open'
                process = subprocess.Popen([opener, str(path)], stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           start_new_session=True)
                try:
                    result = process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    Thread(target=process.wait, daemon=True).start()
                else:
                    if result:
                        raise OSError(f'{opener} exited with status {result}')
        except OSError:
            raise StorageError(f'파일 관리자를 열 수 없습니다. 이 경로를 복사하세요. {path}', 503,
                               key='errorOpenFolder', path=str(path)) from None
        return {'path': str(path)}

    def shared_path(self, root, relative, allow_root=False):
        return self.file_path(root, 'shared', relative, allow_root)

    def file_path(self, root, kind, relative, allow_root=False):
        base = self.folder(root, kind)
        if allow_root and relative == '':
            return base, base
        if not isinstance(relative, str) or not 1 <= len(relative) <= 512:
            raise StorageError('폴더 기준 상대 경로를 입력하세요.', key='errorFilePath')
        parts = relative.replace('\\', '/').split('/')
        if any(
            not part or part.endswith(('.', ' ')) or PureWindowsPath(part).is_reserved()
            or not part.isprintable() or any(c in part for c in '<>:"|?*')
            for part in parts
        ):
            raise StorageError('폴더 안의 일반 파일이나 폴더 경로를 입력하세요.', key='errorFilePath')
        return base, self._ordinary(base.joinpath(*parts))

    def list_shared(self, root, relative):
        return self.list_files(root, 'shared', relative)

    def list_files(self, root, kind, relative, offset=0):
        base, folder = self.file_path(root, kind, relative, allow_root=True)
        if type(offset) is not int or offset < 0:
            raise StorageError('올바른 목록 위치를 입력하세요.', key='errorFilePath')
        if not folder.is_dir():
            raise StorageError('폴더를 찾을 수 없습니다.', 404, key='errorFileMissing')
        entries = []
        for path in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
            try:
                self.file_path(root, kind, path.relative_to(base).as_posix())
                if path.is_dir():
                    entries.append({'path': path.relative_to(base).as_posix(), 'type': 'folder'})
                elif path.is_file():
                    entries.append({'path': path.relative_to(base).as_posix(), 'type': 'file', 'bytes': path.stat().st_size})
            except (OSError, StorageError):
                continue
        return {'directory': str(folder), 'path': folder.relative_to(base).as_posix() if folder != base else '',
                'entries': entries[offset:offset + 200], 'offset': offset, 'truncated': len(entries) > offset + 200}

    def open_file(self, root, kind, relative):
        _, path = self.file_path(root, kind, relative)
        if not path.is_file():
            raise StorageError('파일을 찾을 수 없습니다.', 404, key='errorFileMissing')
        handle = self._ordinary(path).open('rb')
        try:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise StorageError('일반 파일만 다운로드할 수 있습니다.', key='errorFilePath')
            self._ordinary(path)
        except BaseException:
            handle.close()
            raise
        return handle, path.name

    def read_shared(self, root, relative):
        base, path = self.shared_path(root, relative)
        if not path.is_file():
            raise StorageError('공유 파일을 찾을 수 없습니다.', 404)
        with path.open('rb') as handle:
            data = handle.read(TEXT_BYTES + 1)
        if len(data) > TEXT_BYTES:
            raise StorageError('256 KiB를 넘는 파일은 shared 폴더에서 직접 여세요.', 413)
        try:
            text = data.decode('utf-8-sig')
        except UnicodeError:
            raise StorageError('MCP로는 UTF-8 텍스트를 읽을 수 있습니다. 이 파일은 shared 폴더에서 직접 여세요.', 415) from None
        return {'path': path.relative_to(base).as_posix(), 'text': text}

    def write_shared(self, root, relative, text):
        base, path = self.shared_path(root, relative)
        if not isinstance(text, str):
            raise StorageError('저장할 텍스트를 입력하세요.')
        try:
            size = len(text.encode('utf-8'))
        except UnicodeError:
            raise StorageError('저장할 텍스트를 UTF-8로 읽을 수 없습니다.') from None
        if size > TEXT_BYTES:
            raise StorageError('256 KiB를 넘는 파일은 shared 폴더에 직접 넣으세요.', 413)
        if path.is_dir():
            raise StorageError('해당 경로에 이미 폴더가 있습니다.', 409)
        path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_text(self._ordinary(path)) as handle:
            handle.write(text)
        return {'path': path.relative_to(base).as_posix(), 'bytes': size}

    def log_path(self, root):
        return self._ordinary(self.folder(root, 'logs') / 'chat.jsonl')

    @staticmethod
    def check_existing_log(target, records):
        if not target.exists():
            return
        try:
            with target.open(encoding='utf-8') as handle:
                for line in handle:
                    expected = next(records, None)
                    if expected is None or json.loads(line) != expected:
                        raise ValueError
        except (ValueError, UnicodeError):
            raise StorageError('새 위치에 다른 대화 기록이 있습니다. 다른 폴더를 지정하세요.', 409, key='errorExistingLog') from None

    def log(self, target, messages, replace):
        output = atomic_text(target) if replace else target.open('a', encoding='utf-8', newline='\n')
        with output as handle:
            for message in messages:
                handle.write(json.dumps(message, ensure_ascii=False) + '\n')
