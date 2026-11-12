#!/bin/sh
# Policy CI plus the dial gate for one YueLink commit. Does not dispatch a release build.
set -eu
here=$(CDPATH='' cd -- "$(dirname "$0")" && pwd)
exec python3 "$here/ci_check.py" "$@"
