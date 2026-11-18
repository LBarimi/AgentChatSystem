from functools import cache
from importlib.resources import files
import json

from relay.settings import APP_NAME, LANGUAGES


@cache
def _prompt_catalogs():
    return json.loads(files('relay').joinpath('prompt_locales.json').read_text(encoding='utf-8'))


def prompt_catalog(language):
    return _prompt_catalogs()[language if language in LANGUAGES else 'en']


def listening_marker(agent_id):
    return f'[{APP_NAME}:{agent_id}]'


def render_connection_prompts(language, role, values):
    catalog = prompt_catalog(language)
    values = values | {key: catalog[key] for key in ('work_tools', 'legacy_png')}
    install = [paragraph.format_map(values) for paragraph in catalog[f'setup_{role}']]
    install.append(values['work_tools'])
    listening = [listening_marker(values['agent'])]
    listening.extend(paragraph.format_map(values) for paragraph in catalog['listening'])
    return {'install_prompt': '\n\n'.join(install), 'prompt': '\n\n'.join(listening)}
