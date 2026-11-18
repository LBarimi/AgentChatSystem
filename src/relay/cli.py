import argparse
from configparser import Error as ConfigError
from http.client import HTTPException
import json
from math import ceil
import os
from pathlib import Path
from shlex import join as shell_join
import sys
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from relay.guidance import DEFAULT_WAIT_SECONDS, MAX_WAIT_SECONDS, MCP_TOOL_TIMEOUT_SECONDS, MCP_VERSION
from relay.prompts import render_connection_prompts
from relay.settings import APP_NAME, ARCHIVE, LISTEN_HOST, LOOPBACK, PROJECT, Settings
from relay.uploads import MAX_FILES, UploadError, upload_name, upload_size


class RelayError(Exception):
    def __init__(self, message, *, retryable=False, status=None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class Client:
    def __init__(self, port):
        self.url = f"http://{LOOPBACK}:{port}"
        self.opener = build_opener(ProxyHandler({}))

    def call(self, route, payload=None, timeout=8, *, binary=False, raw=None, size=None):
        data = raw if raw is not None else (None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        request = Request(self.url + route, data=data, headers={
            "Content-Type": "application/octet-stream" if raw is not None else "application/json", "X-Local-Relay": "1",
            **({"Content-Length": str(size)} if raw is not None else {}),
            "X-Local-Relay-Client": MCP_VERSION,
            **({'X-Relay-Runner': os.environ['LOCAL_RELAY_RUN_ID']} if os.environ.get('LOCAL_RELAY_RUN_ID') else {}),
        })
        try:
            with self.opener.open(request, timeout=timeout) as response:
                if binary:
                    return response.headers.get_content_type(), response.read()
                return json.load(response)
        except HTTPError as exc:
            try:
                message = json.loads(exc.read())["error"]
            except (ValueError, KeyError):
                message = f"HTTP {exc.code}"
            finally:
                exc.close()
            raise RelayError(message, retryable=500 <= exc.code <= 599, status=exc.code) from exc
        except (URLError, OSError, HTTPException, json.JSONDecodeError) as exc:
            raise RelayError(
                f"채팅방에 재연결하는 중입니다. {self.url}", retryable=True,
            ) from exc

    def wait(self, agent, seconds, channel=None):
        if type(seconds) is not int or not 0 <= seconds <= MAX_WAIT_SECONDS:
            raise RelayError(f"seconds는 0~{MAX_WAIT_SECONDS}의 정수여야 합니다.")
        deadline = monotonic() + seconds
        interrupted = False
        while True:
            remaining = min(seconds, max(0, ceil(deadline - monotonic())))
            params = {"agent": agent, "seconds": remaining}
            if channel is not None:
                params['channel'] = channel
            route = "/api/wait?" + urlencode(params)
            try:
                return self.call(route, timeout=remaining + 8)
            except RelayError as error:
                # A lost HTTP response can leave the previous wait open until its deadline.
                if not error.retryable and not (interrupted and error.status == 409):
                    raise
                interrupted = True
                left = deadline - monotonic()
                if left <= 0:
                    return {
                        'state': 'reconnecting', 'retry_after_seconds': 2,
                        'message': '연결이 돌아오면 수신을 재개합니다. receive를 다시 호출하세요.',
                    }
                sleep(min(2, left))

    def upload_files(self, paths):
        if not isinstance(paths, list) or len(paths) > MAX_FILES:
            raise RelayError('파일은 최대 30개까지 첨부할 수 있습니다.')
        attachments = []
        try:
            for value in paths:
                if not isinstance(value, str) or not Path(value).is_absolute():
                    raise RelayError('첨부할 파일의 절대 경로를 입력하세요.')
                path = Path(value)
                upload_name(path.name)
                with path.open('rb') as handle:
                    size = os.fstat(handle.fileno()).st_size
                    upload_size(size)
                    attachments.append(self.call('/api/uploads?' + urlencode({'name': path.name}),
                                                 raw=handle, size=size, timeout=60))
            return attachments
        except (OSError, UploadError, RelayError) as error:
            self.discard_uploads(attachments)
            raise RelayError(str(error)) from error

    def discard_uploads(self, attachments):
        if attachments:
            try:
                self.call('/api/uploads/discard', {'attachments': attachments}, timeout=3)
            except RelayError:
                pass

    def reply(self, agent, message_id, body, handoff, next_agent=None, *, attachments=None):
        staged = self.upload_files([] if attachments is None else attachments)
        payload = {
            "sender": agent, "reply_to": message_id, "body": body, "handoff": handoff, "next_agent": next_agent,
            "attachments": staged,
        }
        try:
            for attempt in range(3):
                try:
                    return self.call("/api/messages", payload, timeout=60)
                except RelayError as error:
                    if not error.retryable or attempt == 2:
                        raise
                    # Each message accepts one reply per sender, so a repeated send is safe.
                    sleep(1)
        finally:
            self.discard_uploads(staged)


def parser():
    cli = argparse.ArgumentParser(description=APP_NAME)
    cli.add_argument("--port", type=int, help="Override [server] port in bin/settings.ini.")
    sub = cli.add_subparsers(dest="action", required=True)
    sub.add_parser('settings', help='Print the effective settings for the launcher.')
    server = sub.add_parser("serve")
    server.add_argument("--db", type=Path, default=PROJECT / "var/room.sqlite3")
    sub.add_parser("status").add_argument('--channel', default='general')
    sub.add_parser('channels', help='채널 ID와 shared/logs 경로 표시')
    sub.add_parser('board', help='게시판 최신 글 목록 표시')
    read_post = sub.add_parser('read-post', help='게시글 확인 및 열람 기록')
    read_post.add_argument('--agent', required=True)
    read_post.add_argument('--id', type=int, required=True)
    add = sub.add_parser('add', help='이름이 있는 에이전트를 로컬 채팅방에 등록')
    add.add_argument('--role', choices=['codex', 'claude'], required=True)
    add.add_argument('--name', required=True)
    send = sub.add_parser("start")
    send.add_argument("--to", dest="recipient", required=True, help='수신자 ID 또는 모두에게 보내는 all')
    send.add_argument('--channel', default='general')
    send.add_argument("--file", type=Path, help="UTF-8 메시지 파일. 생략 시 표준 입력.")
    for command in ["wait", "reply", "mcp"]:
        action = sub.add_parser(command)
        action.add_argument("--agent", required=True, help='등록된 에이전트 ID')
        if command == "wait":
            action.add_argument("--seconds", type=int, default=DEFAULT_WAIT_SECONDS)
            action.add_argument('--channel')
        elif command == "reply":
            action.add_argument("--id", type=int, required=True)
            text = action.add_mutually_exclusive_group()
            text.add_argument("--file", type=Path, help="UTF-8 답변 파일. 생략 시 표준 입력.")
            text.add_argument('--body', help='답변 본문 또는 PNG 설명')
            action.add_argument('--attach', dest='attachments', action='append', default=[], help='첨부할 파일의 절대 경로. 개당 10 MB, 최대 30번 사용.')
            action.add_argument('--next', dest='next_agent', help='이 답변을 받을 에이전트 ID')
    config = sub.add_parser("config", help="Codex/Claude MCP 설정 예시 출력")
    config.add_argument('--agent', help='등록된 에이전트 ID. 생략 시 기본 두 에이전트 출력.')
    return cli


def attachment_command(port, agent_id):
    args = ['--port', str(port), 'reply', '--agent', agent_id, '--id', '123', '--body', 'PNG', '--attach']
    if os.name == 'nt':
        relay = str(ARCHIVE.with_name('relay.cmd')).replace("'", "''")
        return f"```powershell\n& '{relay}' {' '.join(args)} 'C:/path/result.png'\n```"
    command = shell_join(['sh', str(ARCHIVE.with_name('relay.sh')), *args, '/path/to/result.png'])
    return f'```sh\n{command}\n```'


def connection_setup(port, agent_id, role, *, language='en'):
    exe = str(Path(sys.executable).resolve())
    args = [str(ARCHIVE)]
    try:
        configured_port = Settings().port()
    except (ConfigError, OSError, ValueError):
        configured_port = None
    if port != configured_port:
        args.extend(['--port', str(port)])
    args.extend(['mcp', '--agent', agent_id])
    server = 'local_chat' if agent_id == role else 'relay_' + agent_id.replace('-', '_')
    if role == 'codex':
        snippet = '\n'.join([
            f'[mcp_servers.{server}]',
            'command = ' + json.dumps(exe, ensure_ascii=False),
            'args = ' + json.dumps(args, ensure_ascii=False),
            f'tool_timeout_sec = {MCP_TOOL_TIMEOUT_SECONDS}',
        ])
        config_path = Path.home() / '.codex' / 'config.toml'
        config_format = 'toml'
    else:
        snippet = json.dumps({'mcpServers': {server: {'command': exe, 'args': args}}}, ensure_ascii=False, indent=2)
        config_path = Path.home() / '.claude.json'
        config_format = 'json'
    values = {
        'app': APP_NAME,
        'agent': agent_id,
        'server': server,
        'config_path': str(config_path),
        'configuration': f'```{config_format}\n{snippet}\n```',
        'wait_seconds': DEFAULT_WAIT_SECONDS,
        'legacy_command': attachment_command(port, agent_id),
    }
    return {
        'server': server, 'config': snippet, 'config_path': str(config_path),
        **render_connection_prompts(language, role, values),
    }


def main():
    for stream in [sys.stdout, sys.stderr]:
        stream.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    settings = Settings()
    try:
        port = settings.port() if args.port is None else args.port
        if not 1 <= port <= 65535:
            raise ValueError('Port must be an integer from 1 to 65535.')
    except (ConfigError, OSError, ValueError) as exc:
        print(f'bin/settings.ini: {exc}', file=sys.stderr)
        return 2
    client = Client(port)
    try:
        if args.action == 'settings':
            print(json.dumps({'port': port, 'host': LOOPBACK, 'listen_host': LISTEN_HOST}))
            return 0
        if args.action == "serve":
            from relay.server import serve
            serve(port, args.db, settings=settings)
            return 0
        if args.action == "mcp":
            from relay.mcp import run
            return run(client, args.agent)
        if args.action == "config":
            if args.agent:
                setup = client.call('/api/setup?' + urlencode({'agent': args.agent}))
                print(setup['config'])
            else:
                print('Codex config.toml:\n' + connection_setup(port, 'codex', 'codex')['config'])
                print('\nClaude Desktop claude_desktop_config.json:\n' + connection_setup(port, 'claude', 'claude')['config'])
            return 0
        if args.action == "status":
            result = client.call("/api/room?" + urlencode({'channel': args.channel}))
        elif args.action == 'channels':
            result = client.call('/api/channels')
        elif args.action == 'board':
            result = client.call('/api/board')
        elif args.action == 'read-post':
            result = client.call('/api/board/read', {'agent': args.agent, 'post_id': args.id})
        elif args.action == 'add':
            result = client.call('/api/participants', {'role': args.role, 'name': args.name})
        elif args.action == "wait":
            result = client.wait(args.agent, args.seconds, args.channel)
        else:
            if args.action == 'reply' and args.body is not None:
                body = args.body
            elif args.file:
                body = args.file.read_text(encoding="utf-8-sig")
            elif args.action == 'reply' and args.attachments:
                body = ''
            else:
                body = sys.stdin.buffer.read().decode("utf-8-sig")
            if args.action == "start":
                result = client.call("/api/messages", {
                    "sender": "user", "recipient": args.recipient, "body": body, "channel_id": args.channel,
                })
            else:
                result = client.reply(args.agent, args.id, body, True, args.next_agent, attachments=args.attachments)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (RelayError, ConfigError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
