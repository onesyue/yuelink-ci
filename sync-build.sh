#!/usr/bin/env bash
# The public build.yml in this repository is the canonical release workflow.
# The private YueLink repository does not carry a product workflow, so this
# script no longer copies one. --check validates the public file itself.
# An optional source path is accepted and ignored so existing callers that
# still pass a checkout keep working. Write mode is refused.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ "${1:-}" != "--check" ]; then
  echo "sync-build.sh no longer copies a private template. Edit .github/workflows/build.yml in this repository." >&2
  exit 2
fi
shift
if [ "$#" -gt 1 ]; then
  echo "usage: ./sync-build.sh --check [ignored-source-path]" >&2
  exit 2
fi

python3 scripts/check_public_build.py .github/workflows/build.yml
