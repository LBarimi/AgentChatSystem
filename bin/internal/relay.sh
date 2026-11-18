#!/bin/sh
set -eu

internal_dir=$(CDPATH= cd "$(dirname "$0")" && pwd -P)
archive="$internal_dir/AgentChatSystem.pyz"

if ! command -v python3 >/dev/null 2>&1; then
    echo 'AgentChatSystem requires Python 3.10 or later on PATH.' >&2
    exit 1
fi
if [ ! -f "$archive" ]; then
    python3 -B "$internal_dir/build.py"
fi
exec python3 -X utf8 "$archive" "$@"
