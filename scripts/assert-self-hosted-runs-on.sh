#!/usr/bin/env bash
# Admission is fail-closed, including missing parser dependencies.
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/assert-self-hosted-runs-on.py" "$@"
