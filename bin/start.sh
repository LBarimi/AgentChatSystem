#!/bin/sh
set -eu

bin_dir=$(CDPATH= cd "$(dirname "$0")" && pwd -P)
exec sh "$bin_dir/internal/relay.sh" serve "$@"
