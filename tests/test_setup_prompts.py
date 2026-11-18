import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_network

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from relay.cli import connection_setup
from relay.prompts import setup_verification
from relay.settings import LANGUAGES


class SetupPrompts(unittest.TestCase):
    def test_all_languages_render_commands_and_keep_installation_separate_from_listening(self):
        for language in LANGUAGES:
            for role in ('codex', 'claude'):
                with self.subTest(language=language, role=role), patch('relay.cli.Settings.port', return_value=9081):
                    setup = connection_setup(9092, role + '-2', role, language=language)
                    prompt = setup['install_prompt']
                    blocks = [json.loads(block) for block in re.findall(r'```json\n(.*?)\n```', prompt, re.S)]
                    commands = [block for block in blocks if isinstance(block, list)]
                    self.assertEqual(len(commands), 2)
                    register, inspect = commands
                    self.assertEqual(register[:3], [role, 'mcp', 'add'])
                    self.assertEqual(inspect[:4], [role, 'mcp', 'get', setup['server']])
                    launch = register[register.index('--') + 1:]
                    self.assertEqual(launch[-5:], ['--port', '9092', 'mcp', '--agent', role + '-2'])
                    if role == 'claude':
                        self.assertEqual(register[register.index('--scope') + 1], 'user')
                        self.assertEqual(register[register.index('--transport') + 1], 'stdio')
                    script, = re.findall(r'```python\n(.*?)```', prompt, re.S)
                    compile(script, 'generated-setup-check', 'exec')
                    self.assertNotIn('{verification}', prompt)
                    self.assertIn('http://127.0.0.1:9092/api/health', prompt)
                    self.assertTrue(setup['prompt'].startswith('[AgentChatSystem:' + role + '-2]'))
                    self.assertIn('receive(seconds=40)', setup['prompt'])
                    self.assertNotIn('[[attach_png:', prompt)

    def test_literal_arguments_survive_spaces_quotes_unicode_and_shell_metacharacters(self):
        archive = ROOT / "경로 with spaces O'Brien $() & `tick`" / 'AgentChatSystem.pyz'
        python_path = ROOT / "Python O'Brien $() & `tick`.exe"
        with patch('relay.cli.ARCHIVE', archive), patch('relay.cli.sys.executable', str(python_path)), \
                patch('relay.cli.Settings.port', return_value=9081):
            setup = connection_setup(9092, 'claude-2', 'claude', language='en')
        config = json.loads(setup['config'])['mcpServers'][setup['server']]
        expected = [str(python_path.resolve()), str(archive), '--port', '9092', 'mcp', '--agent', 'claude-2']
        self.assertEqual([config['command'], *config['args']], expected)
        script, = re.findall(r'```python\n(.*?)```', setup['install_prompt'], re.S)
        assignments = {node.targets[0].id: ast.literal_eval(node.value) for node in ast.parse(script).body
                       if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                       and node.targets[0].id in ('command', 'agent')}
        self.assertEqual(assignments['command'], expected)
        self.assertEqual(assignments['agent'], 'claude-2')

    def test_codex_config_path_uses_custom_home(self):
        custom = ROOT / 'var' / 'custom codex home'
        with patch.dict(os.environ, {'CODEX_HOME': str(custom)}), patch('relay.cli.Settings.port', return_value=9081):
            setup = connection_setup(9081, 'codex', 'codex', language='ko')
        self.assertEqual(setup['config_path'], str(custom / 'config.toml'))
        self.assertIn('tool_timeout_sec = 90', setup['config'])

    def test_probe_reports_launch_errors_without_claiming_success(self):
        script = setup_verification([sys.executable, '-c',
                                     "import sys; sys.stderr.write('fixture launch failed'); sys.exit(7)"], 'codex')
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-'], input=script, text=True,
                                encoding='utf-8', capture_output=True, timeout=25)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('"bridge": "verified"', result.stdout)
        self.assertIn('fixture launch failed', result.stderr)


class SetupConnectionCheck(unittest.TestCase):
    setUp = test_network.NetworkAccess.setUp
    stop = test_network.NetworkAccess.stop

    def check_bridge(self, agent):
        command = [sys.executable, '-B', str(ROOT / 'src'), '--port', str(self.port), 'mcp', '--agent', agent]
        script = setup_verification(command, agent)
        return subprocess.run([sys.executable, '-B', '-X', 'utf8', '-'], input=script, text=True,
                              encoding='utf-8', capture_output=True, timeout=25)

    def test_embedded_probe_checks_real_stdio_tools_and_leaves_chat_untouched(self):
        for role in ('codex', 'claude'):
            with self.subTest(role=role):
                result = self.check_bridge(role)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['bridge'], 'verified')
                self.assertEqual(json.loads(result.stdout)['agent'], role)
        room = self.server.room
        self.assertEqual(room.snapshot('general')['total_messages'], 0)
        self.assertEqual(room.db.execute('SELECT COUNT(*) FROM board_posts').fetchone()[0], 0)
        self.assertEqual(room.waiters, {})
        self.assertEqual(room.users.snapshot(), [])

    def test_probe_does_not_claim_success_for_missing_agent(self):
        self.server.room.remove_participant('codex')
        result = self.check_bridge('codex')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('"bridge": "verified"', result.stdout)
        self.assertIn('Agent is not registered', result.stderr)


if __name__ == '__main__':
    unittest.main()
