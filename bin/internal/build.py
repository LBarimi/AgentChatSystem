"""Build the local executable from the source tree."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import zipapp


def main():
    if sys.version_info < (3, 10):
        raise SystemExit('AgentChatSystem requires Python 3.10 or later.')
    internal = Path(__file__).resolve().parent
    target = internal / 'AgentChatSystem.pyz'
    with TemporaryDirectory(prefix='.build-', dir=internal) as directory:
        candidate = Path(directory) / target.name
        zipapp.create_archive(internal.parent.parent / 'src', candidate, compressed=True)
        candidate.replace(target)


if __name__ == '__main__':
    main()
