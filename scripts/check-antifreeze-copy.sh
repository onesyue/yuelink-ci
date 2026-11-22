#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: bash scripts/check-antifreeze-copy.sh <pinned-yuelink-checkout>" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cmp -- "$script_dir/antifreeze_lint.py" "${1%/}/scripts/antifreeze_lint.py"
