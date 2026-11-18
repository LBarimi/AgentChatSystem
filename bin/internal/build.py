"""Build the local executable from the source tree."""

from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
import zipapp


def main():
    if sys.version_info < (3, 10):
        raise SystemExit('AgentChatSystem requires Python 3.10 or later.')
    internal = Path(__file__).resolve().parent
    target = internal / 'AgentChatSystem.pyz'
    # Create beside the target so Windows inherits the checkout's ACL, not the
    # owner-only ACL of a TemporaryDirectory that would survive the final move.
    with NamedTemporaryFile(prefix='.build-', suffix='.pyz', dir=internal, delete=False) as temporary:
        candidate = Path(temporary.name)
    try:
        zipapp.create_archive(internal.parent.parent / 'src', candidate, compressed=True)
        candidate.replace(target)
    finally:
        candidate.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
