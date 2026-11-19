import ast
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_network

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from relay.cli import connection_setup
from relay import cli
from relay.prompts import prompt_catalog, setup_verification
from relay.settings import LANGUAGES


class SetupPrompts(unittest.TestCase):
    def test_all_languages_render_commands_and_keep_installation_separate_from_listening(self):
        for language in LANGUAGES:
            for role in ('codex', 'claude'):
                with self.subTest(language=language, role=role), patch('relay.cli.Settings.port', return_value=9081):
                    setup = connection_setup(9092, role + '-2', role, language=language)
                    self.assertEqual(setup['server'], 'acs_' + role + '_2')
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
                    self.assertEqual(setup['language'], language)
                    messages = next(ast.literal_eval(node.value) for node in ast.parse(script).body
                                    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                                    and node.targets[0].id == 'messages')
                    self.assertEqual(messages, prompt_catalog(language)['verification'])
                    self.assertEqual(set(messages), set(prompt_catalog('en')['verification']))
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

    def test_localized_probe_results_and_failures_keep_protocol_values_stable(self):
        packets = [
            {'id': 1, 'result': {'serverInfo': {'name': 'local-chat-codex'}}},
            {'id': 2, 'result': {'tools': [{'name': name} for name in ('receive','reply','participants')]}},
            {'id': 3, 'result': {'content': [{'type': 'text', 'text': '{"participants":[{"id":"codex"}]}'}]}},
        ]
        def result(rows=packets, code=0):
            return SimpleNamespace(stdout='\n'.join(json.dumps(row) for row in rows), stderr='fixture error', returncode=code)
        for language in LANGUAGES:
            script = setup_verification(['python', 'bridge.pyz'], 'codex', language=language)
            messages = prompt_catalog(language)['verification']
            with self.subTest(language=language), patch('subprocess.run', return_value=result()) as run, redirect_stdout(StringIO()) as output:
                exec(script, {})
                reported = json.loads(output.getvalue())
                self.assertEqual(reported['bridge'], 'verified')
                self.assertEqual(reported['agent'], 'codex')
                self.assertEqual(reported['message'], messages['verified'])
                self.assertEqual(reported['client_reload_message'], messages['client_reload'])
                self.assertFalse(run.call_args.kwargs['shell'])
                self.assertEqual(run.call_args.kwargs['timeout'], 20)
            wrong_server = [dict(packets[0], result={'serverInfo': {'name': 'different'}}), *packets[1:]]
            missing_tools = [packets[0], dict(packets[1], result={'tools': []}), packets[2]]
            missing_agent = [*packets[:2], dict(packets[2], result={'content': [{'type':'text','text':'{"participants":[]}'}]})]
            for expected, response in [('process_failed', result(code=1)), ('verification_failed', result([])),
                                       ('wrong_server', result(wrong_server)), ('missing_tools', result(missing_tools)),
                                       ('missing_agent', result(missing_agent))]:
                with self.subTest(language=language, failure=expected), patch('subprocess.run', return_value=response):
                    with self.assertRaises(RuntimeError) as error:
                        exec(script, {})
                    self.assertIn(messages[expected], str(error.exception))
                    if expected == 'verification_failed':
                        self.assertIn(messages['missing_response'], str(error.exception))
            for expected, cause in [('timeout', subprocess.TimeoutExpired('python', 20)), ('process_failed', OSError('launch denied'))]:
                with self.subTest(language=language, failure=expected), patch('subprocess.run', side_effect=cause):
                    with self.assertRaises(RuntimeError) as error:
                        exec(script, {})
                    self.assertIn(messages[expected], str(error.exception))

    def test_cli_config_headings_follow_saved_language_and_explicit_override(self):
        class Output(StringIO):
            def reconfigure(self, **kwargs):
                pass
        for saved, explicit in [('ko', None), ('ja', 'de'), ('auto', None)]:
            language = explicit or ('en' if saved == 'auto' else saved)
            args = ['acs', 'config'] + (['--language', explicit] if explicit else [])
            settings = SimpleNamespace(port=lambda:9081, language=lambda:saved)
            with self.subTest(saved=saved, explicit=explicit), patch('relay.cli.Settings', return_value=settings), \
                    patch.object(sys, 'argv', args), patch.object(sys, 'stdout', Output()) as output, patch.object(sys, 'stderr', Output()):
                self.assertEqual(cli.main(), 0)
                self.assertIn(prompt_catalog(language)['config_codex'] + ':', output.getvalue())
                self.assertIn(prompt_catalog(language)['config_claude'] + ':', output.getvalue())
                self.assertIn('[mcp_servers.acs_codex]', output.getvalue())
                self.assertIn('"acs_claude"', output.getvalue())
                self.assertIn('"mcpServers"', output.getvalue())

    def test_acs_names_match_config_commands_and_prompts_for_default_and_numbered_agents(self):
        cases = [
            ('codex', 'codex', 'acs_codex', 'local_chat'),
            ('claude', 'claude', 'acs_claude', 'local_chat'),
            ('codex', 'codex-2', 'acs_codex_2', 'relay_codex_2'),
            ('claude', 'claude-2', 'acs_claude_2', 'relay_claude_2'),
            ('codex', 'codex-12', 'acs_codex_12', 'relay_codex_12'),
            ('claude', 'claude-12', 'acs_claude_12', 'relay_claude_12'),
        ]
        for language in LANGUAGES:
            for role, agent, server, legacy in cases:
                with self.subTest(language=language, agent=agent):
                    setup = connection_setup(9081, agent, role, language=language)
                    self.assertEqual(setup['server'], server)
                    self.assertIn(server, setup['prompt'])
                    self.assertNotIn(legacy, setup['prompt'])
                    self.assertIn(legacy, setup['install_prompt'])
                    commands = [json.loads(block) for block in re.findall(r'```json\n(.*?)\n```', setup['install_prompt'], re.S)]
                    register, inspect = [command for command in commands if isinstance(command, list)]
                    self.assertEqual(inspect[3], server)
                    self.assertEqual(register[register.index('--') - 1], server)
                    self.assertEqual(register[-2:], ['--agent', agent])
                    if role == 'codex':
                        self.assertTrue(setup['config'].startswith('[mcp_servers.' + server + ']\n'))
                    else:
                        self.assertEqual(list(json.loads(setup['config'])['mcpServers']), [server])

    def test_unrecognized_language_falls_back_to_english_without_changing_configuration(self):
        for role in ('codex', 'claude'):
            english = connection_setup(9081, role, role)
            fallback = connection_setup(9081, role, role, language='unsupported')
            self.assertEqual(fallback, english)
            for language in LANGUAGES:
                self.assertEqual(connection_setup(9081, role, role, language=language)['config'], english['config'])


class SetupConnectionCheck(unittest.TestCase):
    setUp = test_network.NetworkAccess.setUp
    stop = test_network.NetworkAccess.stop

    def test_setup_api_uses_saved_language_and_explicit_request_language(self):
        for language in LANGUAGES:
            self.server.settings.set_language(language)
            for role in ('codex', 'claude'):
                saved = cli.Client(self.port).call('/api/setup?agent=' + role)
                explicit = cli.Client(self.port).call('/api/setup?agent=' + role + '&lang=en')
                self.assertEqual(saved['language'], language)
                self.assertEqual(saved['server'], 'acs_' + role)
                self.assertEqual(explicit['language'], 'en')
                self.assertEqual(saved['config'], explicit['config'])
                self.assertEqual(self.server.settings.language(), language)
                self.assertIn(prompt_catalog(language)['verification']['verified'], saved['install_prompt'])
                self.assertTrue(saved['prompt'].startswith('[AgentChatSystem:' + role + ']'))

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
