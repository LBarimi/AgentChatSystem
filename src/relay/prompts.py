from functools import cache
from importlib.resources import files
import json
from textwrap import dedent

from relay.settings import APP_NAME, LANGUAGES


@cache
def _prompt_catalogs():
    return json.loads(files('relay').joinpath('prompt_locales.json').read_text(encoding='utf-8'))


def prompt_catalog(language):
    return _prompt_catalogs()[language if language in LANGUAGES else 'en']


def listening_marker(agent_id):
    return f'[{APP_NAME}:{agent_id}]'


def setup_verification(command, agent_id):
    """A bounded, read-only MCP check that never edits client configuration."""
    return dedent(f'''\
        import json
        import subprocess

        command = {command!r}
        agent = {agent_id!r}
        packets = [
            {{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {{
                "protocolVersion": "2025-03-26", "capabilities": {{}},
                "clientInfo": {{"name": "acs-setup-check", "version": "1.0.0"}},
            }}}},
            {{"jsonrpc": "2.0", "method": "notifications/initialized"}},
            {{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}},
            {{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {{
                "name": "participants", "arguments": {{}},
            }}}},
        ]
        result = subprocess.run(command, input="".join(json.dumps(p) + "\\n" for p in packets),
                                text=True, encoding="utf-8", capture_output=True, timeout=20,
                                shell=False)
        if result.returncode:
            raise RuntimeError("MCP process failed: " + result.stderr[-2000:])
        replies = {{}}
        for line in result.stdout.splitlines():
            packet = json.loads(line)
            if "id" in packet:
                replies[packet["id"]] = packet
        for ident in (1, 2, 3):
            if ident not in replies or "error" in replies[ident] or replies[ident].get("result", {{}}).get("isError"):
                detail = replies.get(ident, {{"error": "Missing response"}})
                raise RuntimeError("MCP verification failed: " + json.dumps(detail, ensure_ascii=False)[:2000])
        if replies[1]["result"]["serverInfo"]["name"] != "local-chat-" + agent:
            raise RuntimeError("Wrong MCP server identity")
        names = {{tool["name"] for tool in replies[2]["result"]["tools"]}}
        if not {{"receive", "reply", "participants"}} <= names:
            raise RuntimeError("Required MCP tools are missing")
        content = replies[3]["result"]["content"]
        participants = json.loads(next(item["text"] for item in content if item["type"] == "text"))
        if not any(item["id"] == agent for item in participants["participants"]):
            raise RuntimeError("Agent is not registered in this ACS instance")
        print(json.dumps({{"bridge": "verified", "agent": agent, "client_reload": "check separately"}}))
        ''')


def render_connection_prompts(language, role, values):
    catalog = prompt_catalog(language)
    values = values | {key: catalog[key] for key in ('work_tools', 'legacy_png')}
    specific = catalog[f'setup_{role}']
    install = [paragraph.format_map(values) for paragraph in (
        specific[:1] + catalog['setup_before'] + specific[1:] + catalog['setup_after'])]
    listening = [listening_marker(values['agent'])]
    listening.extend(paragraph.format_map(values) for paragraph in catalog['listening'])
    return {'install_prompt': '\n\n'.join(install), 'prompt': '\n\n'.join(listening)}
