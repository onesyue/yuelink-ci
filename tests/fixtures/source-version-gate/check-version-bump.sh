#!/usr/bin/env bash
# Check committed source, not dirty working-tree release metadata.
set -euo pipefail
exec python3 "$(dirname "${BASH_SOURCE[0]}")/version_gate.py" "$@"
