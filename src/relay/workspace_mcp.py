"""Read-only folder summaries for an ACS-managed CLI."""
from functools import partial
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat

from relay.mcp import RpcError, initialize, run_packets


SERVER = 'acs_workspace'
TOOL_NAME = 'inspect_folder'
ENTRY_LIMIT = 50
LINE_COUNT_BYTES = 512 * 1024
TOOL = {
    'name': TOOL_NAME,
    'description': (
        'List the names, types, and file sizes in one directory of the selected reference folder. '
        'Use this for file inventory and optional line counts without invoking a shell.'
    ),
    'inputSchema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'path': {'type': 'string', 'description': 'Folder path relative to the reference folder, or ".".'},
            'extension': {'type': 'string', 'description': 'Optional file extension such as ".gd".'},
            'count_lines': {'type': 'boolean', 'description': 'Count lines of readable text files up to 512 KiB.'},
            'offset': {'type': 'integer', 'minimum': 0, 'description': 'Use the returned next_offset for another page.'},
        },
    },
    'annotations': {'readOnlyHint': True, 'openWorldHint': False},
}


def is_reparse(info):
    return bool(getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def line_count(path, original):
    if original.st_size > LINE_COUNT_BYTES:
        return None
    try:
        with path.open('rb') as handle:
            if not os.path.samestat(original, os.fstat(handle.fileno())):
                return None
            data = handle.read(LINE_COUNT_BYTES + 1)
        if len(data) > LINE_COUNT_BYTES:
            return None
        if data.startswith((b'\xff\xfe', b'\xfe\xff')):
            return len(data.decode('utf-16').splitlines())
        if b'\0' not in data:
            return len(data.splitlines())
    except (OSError, UnicodeError):
        pass
    return None


def inspect_folder(root, args):
    relative = args.get('path', '.')
    extension = args.get('extension')
    count = args.get('count_lines', False)
    offset = args.get('offset', 0)
    if (not isinstance(relative, str) or not 1 <= len(relative) <= 512 or type(count) is not bool
            or type(offset) is not int or offset < 0):
        raise ValueError('Use a relative folder path, a boolean count_lines, and a nonnegative offset.')
    if extension is not None and (not isinstance(extension, str) or not re.fullmatch(r'\.[A-Za-z0-9_-]{1,24}', extension)):
        raise ValueError('Use a file extension such as .gd.')
    parts = [] if relative == '.' else relative.replace('\\', '/').split('/')
    if any(not part or not part.isprintable() or part.endswith(('.', ' ')) or PureWindowsPath(part).is_reserved()
           or any(char in part for char in '<>:"|?*') for part in parts):
        raise ValueError('Use an ordinary folder path relative to the reference folder.')
    folder = root
    for part in parts:
        folder /= part
        info = folder.lstat()
        if not stat.S_ISDIR(info.st_mode) or is_reparse(info):
            raise ValueError('Choose an ordinary directory inside the reference folder.')
    with os.scandir(folder) as directory:
        entries = sorted(directory, key=lambda entry: entry.name.casefold())
    page = []
    skipped = 0
    for entry in entries:
        path = Path(entry.path)
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if is_reparse(info):
            continue
        if stat.S_ISDIR(info.st_mode):
            if extension:
                continue
            item = {'name': entry.name, 'type': 'directory'}
        elif stat.S_ISREG(info.st_mode):
            if extension and path.suffix.casefold() != extension.casefold():
                continue
            item = {'name': entry.name, 'type': 'file', 'size_bytes': info.st_size}
        else:
            continue
        if skipped < offset:
            skipped += 1
            continue
        if len(page) == ENTRY_LIMIT:
            return {'path': relative, 'entries': page, 'next_offset': offset + ENTRY_LIMIT}
        if count and item['type'] == 'file':
            item['lines'] = line_count(path, info)
        page.append(item)
    return {'path': relative, 'entries': page, 'next_offset': None}


def dispatch(root, packet):
    method = packet['method']
    params = packet.get('params', {})
    if not isinstance(params, dict):
        raise RpcError(-32602, 'Invalid params')
    if method == 'initialize':
        return initialize(params, SERVER, 'Use inspect_folder to list and count lines in the selected reference folder.')
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': [TOOL]}
    if method != 'tools/call':
        raise RpcError(-32601, 'Method not found')
    args = params.get('arguments', {})
    if params.get('name') != TOOL_NAME or not isinstance(args, dict):
        raise RpcError(-32602, 'Invalid workspace tool or arguments')
    try:
        data = json.dumps(inspect_folder(root, args), ensure_ascii=True)
        return {'content': [{'type': 'text', 'text': data}]}
    except (RuntimeError, OSError):
        error = 'The reference folder entry is missing or cannot be read.'
    except ValueError as exc:
        error = str(exc)
    return {'isError': True, 'content': [{'type': 'text', 'text': error}]}


def run(root):
    root = root.absolute()
    if root.resolve(strict=True) != root or not root.is_dir():
        raise ValueError('Choose an ordinary reference directory.')
    return run_packets(partial(dispatch, root))
