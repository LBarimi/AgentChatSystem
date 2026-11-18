from configparser import ConfigParser
from pathlib import Path
from threading import Lock

from relay.storage import atomic_text


APP_NAME = 'AgentChatSystem'
DEFAULT_PORT = 9081
DEFAULT_SETTINGS = '[server]\nport = 9081\n\n[ui]\nlanguage = auto\n'
LOOPBACK = '127.0.0.1'
LISTEN_HOST = '0.0.0.0'
LANGUAGES = ('ko', 'ja', 'en', 'zh-CN', 'zh-TW', 'ru', 'de')
MODULE = Path(__file__).resolve()
PROJECT = MODULE.parents[4] if MODULE.parents[1].suffix == '.pyz' else MODULE.parents[2]
SETTINGS_PATH = PROJECT / 'bin' / 'settings.ini'
ARCHIVE = PROJECT / 'bin' / 'internal' / f'{APP_NAME}.pyz'


class Settings:
    def __init__(self, path=SETTINGS_PATH):
        self.path = path
        self.lock = Lock()

    def _read(self):
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with self.path.open('x', encoding='utf-8', newline='\n') as handle:
                    handle.write(DEFAULT_SETTINGS)
            except FileExistsError:
                pass
        config = ConfigParser(interpolation=None)
        config.read(self.path, encoding='utf-8-sig')
        return config

    def port(self):
        port = self._read().getint('server', 'port', fallback=DEFAULT_PORT)
        if not 1 <= port <= 65535:
            raise ValueError('Set [server] port to an integer from 1 to 65535.')
        return port

    def language(self, config=None):
        if config is None:
            config = self._read()
        value = config.get('ui', 'language', fallback='auto')
        return value if value in LANGUAGES else 'auto'

    def set_language(self, language, *, initialize=False):
        if language not in LANGUAGES:
            raise ValueError('Choose a supported language.')
        with self.lock:
            config = self._read()
            current = self.language(config)
            if current == language or (initialize and current != 'auto'):
                return current
            if not config.has_section('ui'):
                config.add_section('ui')
            config.set('ui', 'language', language)
            with atomic_text(self.path) as handle:
                config.write(handle)
            return language
