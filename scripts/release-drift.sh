#!/usr/bin/env bash
# Read-only monitor; refresh remote refs without changing the checkout.
set -euo pipefail
exec python3 "$(dirname "${BASH_SOURCE[0]}")/release_drift.py" "$@"
