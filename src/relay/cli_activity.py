"""Turn activity suitable for the local viewer."""
from collections import OrderedDict
import json
import re


PERMISSION_ERROR = re.compile(
    r'\b(?:requires approval|approval required|no approval surface|permission.denied|'
    r'access is denied|EACCES|EPERM|permission has not been granted)\b', re.I,
)


def short_text(value, maximum=320):
    if not isinstance(value, str):
        return ''
    return ' '.join(value.split())[:maximum].encode('utf-8', 'replace').decode('utf-8')


class CliActivity:
    def __init__(self, role, publish):
        self.role = role
        self.publish = publish
        self.tools = OrderedDict()

    def __call__(self, line):
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            return
        if isinstance(event, dict):
            if self.role == 'claude':
                self.claude(event)
            else:
                self.codex(event)

    def claude(self, event):
        kind = event.get('type')
        if kind == 'system' and event.get('subtype') == 'permission_denied':
            self.publish('cliActivityDenied', short_text(event.get('tool_name')))
        if kind not in ('assistant', 'user'):
            return
        message = event.get('message')
        blocks = message.get('content') if isinstance(message, dict) else None
        if not isinstance(blocks, list):
            return
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if kind == 'assistant' and block.get('type') == 'tool_use':
                args = block.get('input')
                args = args if isinstance(args, dict) else {}
                name = short_text(block.get('name'), 64)
                if not name:
                    continue
                detail = short_text(args.get('description') or args.get('file_path') or args.get('path'))
                label = ' · '.join(filter(None, (name, detail)))
                tool_id = block.get('id')
                if isinstance(tool_id, str):
                    self.tools[tool_id] = label
                    if len(self.tools) > 256:
                        self.tools.popitem(last=False)
                self.publish('cliActivityCall', label)
            elif kind == 'user' and block.get('type') == 'tool_result':
                tool_id = block.get('tool_use_id')
                label = self.tools.pop(tool_id, '') if isinstance(tool_id, str) else ''
                key = 'cliActivityResult'
                if block.get('is_error'):
                    result = json.dumps(block.get('content'), ensure_ascii=False)
                    key = 'cliActivityDenied' if PERMISSION_ERROR.search(result) else 'cliActivityToolFailed'
                self.publish(key, label)

    def codex(self, event):
        item = event.get('item')
        event_type = event.get('type')
        if event_type not in ('item.started', 'item.completed') or not isinstance(item, dict):
            return
        kind = item.get('type')
        if kind == 'command_execution':
            detail = short_text(item.get('command'))
        elif kind == 'mcp_tool_call':
            detail = ' / '.join(filter(None, (short_text(item.get('server'), 64), short_text(item.get('tool'), 64))))
        elif kind == 'file_change':
            changes = item.get('changes')
            detail = ', '.join(short_text(change.get('path'), 100) for change in changes[:3]
                               if isinstance(change, dict)) if isinstance(changes, list) else ''
        else:
            return
        key = 'cliActivityCall' if event_type == 'item.started' else 'cliActivityResult'
        if event_type == 'item.completed' and (item.get('status') == 'failed' or item.get('exit_code') not in (None, 0)):
            output = item.get('aggregated_output')
            denied = isinstance(output, str) and PERMISSION_ERROR.search(output)
            key = 'cliActivityDenied' if denied else 'cliActivityToolFailed'
        self.publish(key, detail)
