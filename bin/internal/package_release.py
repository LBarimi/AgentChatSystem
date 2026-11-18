"""Build a portable AgentChatSystem ZIP from an explicit file list."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile
import zipapp


ROOT = Path(__file__).resolve().parents[2]
APP_NAME = 'AgentChatSystem'
RUNTIME = f'bin/internal/{APP_NAME}.pyz'
SOURCE_FILES = (
    'src/__main__.py',
    'src/relay/__init__.py',
    'src/relay/board.py',
    'src/relay/chat_files.py',
    'src/relay/cli.py',
    'src/relay/errors.py',
    'src/relay/guidance.py',
    'src/relay/mcp.py',
    'src/relay/prompt_locales.json',
    'src/relay/prompts.py',
    'src/relay/presence.py',
    'src/relay/server.py',
    'src/relay/session_details.py',
    'src/relay/settings.py',
    'src/relay/storage.py',
    'src/relay/uploads.py',
    'src/relay/web/__init__.py',
    'src/relay/web/app.js',
    'src/relay/web/board.js',
    'src/relay/web/i18n.js',
    'src/relay/web/uploads.js',
    'src/relay/web/recipient.js',
    'src/relay/web/users.js',
    'src/relay/web/index.html',
    'src/relay/web/style.css',
    'src/relay/web/locales/de.json',
    'src/relay/web/locales/en.json',
    'src/relay/web/locales/ja.json',
    'src/relay/web/locales/ko.json',
    'src/relay/web/locales/ru.json',
    'src/relay/web/locales/zh-CN.json',
    'src/relay/web/locales/zh-TW.json',
    'src/relay/web/vendor/Feather-LICENSE.txt',
    'src/relay/web/vendor/Lucide-LICENSE.txt',
    'src/relay/web/vendor/Pretendard-LICENSE.txt',
    'src/relay/web/vendor/PretendardVariable.woff2',
    'src/relay/web/vendor/icons.svg',
    'src/relay/web/vendor/openai.svg',
    'src/relay/web/vendor/anthropic.svg',
    'src/relay/web/vendor/SimpleIcons-LICENSE.txt',
    'src/relay/web/vendor/Brand-NOTICE.txt',
)
OTHER_FILES = (
    '.gitattributes',
    '.gitignore',
    'LICENSE',
    'README.md',
    'RELEASE_NOTES.md',
    'release-notes/de.md',
    'release-notes/ja.md',
    'release-notes/ko.md',
    'release-notes/ru.md',
    'release-notes/zh-CN.md',
    'release-notes/zh-TW.md',
    'bin/internal/build.cmd',
    'bin/internal/build.py',
    'bin/internal/launch.ps1',
    'bin/internal/package_release.py',
    'bin/internal/relay.cmd',
    'bin/internal/relay.sh',
    'bin/start.cmd',
    'bin/start.sh',
    'bin/settings.example.ini',
    'bin/internal/stop.cmd',
    'media/readme/chat.png',
    'media/readme/board.png',
    'media/readme/board-read-receipts.png',
)


def audit_name(name):
    parts = Path(name).parts
    if any(part in ('.git', '__pycache__', 'var', '.codex', '.claude', 'docs', 'Docs') for part in parts):
        raise ValueError(f'Private or runtime path in release: {name}')
    if Path(name).suffix.lower() in ('.pyc', '.pyo', '.sqlite', '.sqlite3', '.log', '.jsonl'):
        raise ValueError(f'Runtime artifact in release: {name}')


def package():
    metadata_path = ROOT / 'release.json'
    if not metadata_path.is_file() or metadata_path.is_symlink():
        raise ValueError('Missing ordinary release source: release.json')
    metadata = metadata_path.read_bytes()
    version = json.loads(metadata).get('version')
    if not isinstance(version, str) or re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version) is None:
        raise ValueError('release.json must contain a numeric major.minor.patch version.')
    output = ROOT / 'release'
    output.mkdir(exist_ok=True)
    folder_name = f'{APP_NAME}-v{version}'
    archive_path = output / f'{folder_name}.zip'
    with tempfile.TemporaryDirectory(prefix='agent-chat-system-release-') as temporary:
        stage = Path(temporary) / folder_name
        stage.mkdir()
        for relative in SOURCE_FILES + OTHER_FILES:
            audit_name(relative)
            source = ROOT / relative
            if not source.is_file() or source.is_symlink():
                raise ValueError(f'Missing ordinary release source: {relative}')
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())

        # A release starts with default settings, regardless of this installation's preferences.
        (stage / 'bin/settings.ini').write_bytes((stage / 'bin/settings.example.ini').read_bytes())
        zipapp.create_archive(stage / 'src', stage / RUNTIME, compressed=True)
        with ZipFile(stage / RUNTIME) as runtime:
            for name in runtime.namelist():
                audit_name(name)
                if name.endswith('.pyc'):
                    raise ValueError('Bytecode must not be shipped.')

        (stage / 'release.json').write_bytes(metadata)
        manifest = []
        for path in sorted(stage.rglob('*')):
            if path.is_file():
                relative = path.relative_to(stage).as_posix()
                audit_name(relative)
                manifest.append(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + relative)
        (stage / 'MANIFEST.sha256').write_text('\n'.join(manifest) + '\n', encoding='utf-8')

        with ZipFile(archive_path, 'w', compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(stage.rglob('*')):
                if path.is_file():
                    archive.write(path, folder_name + '/' + path.relative_to(stage).as_posix())

    checksum = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    archive_path.with_suffix(archive_path.suffix + '.sha256').write_text(checksum + '  ' + archive_path.name + '\n', encoding='ascii')
    return archive_path, checksum


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    result, digest = package()
    print(json.dumps({'archive': str(result), 'bytes': result.stat().st_size, 'sha256': digest}, ensure_ascii=False))
