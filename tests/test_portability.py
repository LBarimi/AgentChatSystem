import json
import os
from pathlib import Path
import runpy
from shutil import copy2
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest
from unittest.mock import patch
from urllib.request import build_opener, ProxyHandler, Request
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from relay import automation, storage
from relay.server import Handler, LocalHTTPServer


class PlatformSmoke(unittest.TestCase):
    def test_bind_skips_reverse_dns(self):
        with patch('socket.getfqdn', side_effect=AssertionError('Unexpected reverse DNS lookup')):
            with LocalHTTPServer(('0.0.0.0', 0), Handler) as server:
                self.assertEqual(server.server_name, '0.0.0.0')
                self.assertGreater(server.server_port, 0)

    def test_source_launch_and_mcp_round_trip(self):
        packager = runpy.run_path(str(ROOT / 'bin/internal/package_release.py'))
        with TemporaryDirectory(prefix='agent-chat-smoke-') as temporary:
            if os.name == 'nt':
                # Model a normal checkout, not Python's owner-only temporary directory.
                subprocess.run(['icacls', temporary, '/inheritance:e'], check=True,
                               stdout=subprocess.DEVNULL, timeout=10)
            home = Path(temporary).resolve() / 'checkout with spaces'
            for path in (*packager['SOURCE_FILES'], *packager['OTHER_FILES'], 'release.json'):
                destination = home / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                copy2(ROOT / path, destination)

            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            (home / 'bin/settings.ini').write_text(f'[server]\nport = {port}\n', encoding='utf-8')
            report = subprocess.check_output([sys.executable, '-B', 'bin/internal/package_release.py'],
                                             cwd=home, encoding='utf-8', timeout=20)
            archive = Path(json.loads(report)['archive'])
            self.assertEqual(archive.name, 'AgentChatSystem-v1.0.0.zip')
            with ZipFile(archive) as release:
                prefix = 'AgentChatSystem-v1.0.0/'
                metadata = json.loads(release.read(prefix + 'release.json'))
                self.assertEqual(metadata['version'], '1.0.0')
                for launcher in metadata['entrypoints'].values():
                    self.assertTrue(release.read(prefix + launcher))
                self.assertNotIn(b'\r', release.read(prefix + 'bin/start.sh'))
                self.assertNotIn(b'\r', release.read(prefix + 'bin/internal/relay.sh'))
                self.assertIn(b'port = 9081', release.read(prefix + 'bin/settings.ini'))

            windows = os.name == 'nt'
            start = ['cmd.exe', '/d', '/c', r'bin\start.cmd'] if windows else ['sh', 'bin/start.sh']
            launch_env = os.environ.copy()
            if windows:
                # Get-Command can return multiple applications, including Store aliases.
                extra_python = home / 'extra python installation'
                extra_python.mkdir()
                (extra_python / 'python.cmd').write_text('@echo off\nexit /b 99\n', encoding='ascii')
                launch_env['PATH'] = os.pathsep.join((
                    str(Path(sys.executable).parent), str(extra_python), launch_env.get('PATH', ''),
                ))
            process = None
            try:
                if windows:
                    subprocess.run([*start, '-NoOpen'], cwd=home, env=launch_env, check=True, timeout=25)
                else:
                    process = subprocess.Popen(start, cwd=home, stdin=subprocess.DEVNULL)
                opener = build_opener(ProxyHandler({}))

                def call(route, payload=None):
                    data = json.dumps(payload).encode('utf-8') if payload is not None else None
                    request = Request(f'http://127.0.0.1:{port}{route}', data, {
                        'Content-Type': 'application/json', 'X-Local-Relay': '1',
                    })
                    with opener.open(request, timeout=3) as response:
                        return json.load(response)

                deadline = monotonic() + 10
                while True:
                    try:
                        self.assertEqual(call('/api/health')['service'], 'AgentChatSystem')
                        break
                    except OSError:
                        if monotonic() > deadline or (process and process.poll() is not None):
                            raise
                        sleep(0.05)

                self.assertTrue((home / packager['RUNTIME']).is_file())
                if windows:
                    protected_acl = subprocess.check_output([
                        'powershell', '-NoProfile', '-Command',
                        '[System.IO.File]::GetAccessControl($env:AGENTCHAT_TEST_RUNTIME).AreAccessRulesProtected',
                    ], env=dict(launch_env, AGENTCHAT_TEST_RUNTIME=str(home / packager['RUNTIME'])),
                        encoding='utf-8', timeout=10)
                    self.assertEqual(protected_acl.strip(), 'False',
                                     'The runtime must inherit access permissions from bin/internal.')
                example = call('/api/setup?agent=claude&lang=en')
                shell = 'powershell' if windows else 'sh'
                self.assertIn(f'```{shell}\n', example['prompt'])
                config = json.loads(example['config'])['mcpServers'][example['server']]
                message = call('/api/messages', {
                    'sender': 'user', 'recipient': 'claude', 'body': 'Cross-platform test',
                })['message']
                requests = [('receive', {'seconds': 0}), ('reply', {'message_id': message['id'], 'body': 'Received.'})]
                packets = ''.join(json.dumps({
                    'jsonrpc': '2.0', 'id': index, 'method': 'tools/call',
                    'params': {'name': name, 'arguments': args},
                }) + '\n' for index, (name, args) in enumerate(requests, 1))
                result = subprocess.run([config['command'], *config['args']], input=packets,
                                        encoding='utf-8', capture_output=True, check=True, timeout=10)
                received, replied = [json.loads(json.loads(line)['result']['content'][0]['text'])
                                     for line in result.stdout.splitlines()]
                self.assertEqual(received['message']['id'], message['id'])
                self.assertEqual(replied['message']['reply_to'], message['id'])

                location = home / ('custom store' if windows else 'custom:store')
                channel = call('/api/channels/folder', {'channel_id': 'general', 'folder': str(location)})
                self.assertEqual(Path(channel['shared']), location / 'shared')
                self.assertTrue((location / 'logs/chat.jsonl').is_file())
            finally:
                if windows:
                    subprocess.run([*start, '-Stop'], cwd=home, check=True, timeout=15)
                elif process:
                    process.terminate()
                    process.wait(timeout=10)

    def test_file_manager_dispatch(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory).resolve()
            channel = storage.ChannelStorage(folder)
            if os.name == 'nt':
                with patch('os.startfile') as open_folder:
                    channel.open_folder(folder, 'shared')
                    open_folder.assert_called_once_with(folder / 'shared', 'explore')
            else:
                with patch.object(storage.subprocess, 'Popen') as open_folder:
                    open_folder.return_value.wait.return_value = 0
                    channel.open_folder(folder, 'shared')
                    opener = 'open' if sys.platform == 'darwin' else 'xdg-open'
                    self.assertEqual(open_folder.call_args.args[0], [opener, str(folder / 'shared')])

    @unittest.skipIf(os.name == 'nt', 'Windows keeps its existing native executable checks')
    def test_find_posix_cli_on_path(self):
        with TemporaryDirectory() as directory:
            for role in ('codex', 'claude'):
                cli = Path(directory) / role
                cli.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
                cli.chmod(0o755)
                with patch.dict(os.environ, {'PATH': directory}):
                    self.assertEqual(automation.executable(role), str(cli.absolute()))
