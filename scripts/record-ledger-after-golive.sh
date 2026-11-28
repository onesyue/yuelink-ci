#!/bin/bash
# After a stable release is live, record it in the signed yueto release ledger.
# release.sh calls this only when YUETO_ROOT points at a yueto checkout.
# Waits (bounded) until the live update.json shows the released version, then
# runs yueto's scripts/record-yuelink-ledger.sh. Never fails the release: a
# timeout or a recorder failure is a warning, and the manual runbook
# (docs/reconcile-yuelink-pin.md) still applies.
#
#   YUETO_ROOT=/path/to/yueto YUELINK_CHECKOUT=/path/to/yuelink \
#     scripts/record-ledger-after-golive.sh X.Y.Z
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CI_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
version="${1:-}"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "usage: $0 X.Y.Z" >&2
  exit 2
fi
: "${YUETO_ROOT:?YUETO_ROOT must point at a yueto checkout}"
: "${YUELINK_CHECKOUT:?YUELINK_CHECKOUT must point at a yuelink checkout}"
recorder="$YUETO_ROOT/scripts/record-yuelink-ledger.sh"
[ -f "$recorder" ] || { echo "::warning::$recorder missing; use docs/reconcile-yuelink-pin.md" >&2; exit 0; }
url="${LEDGER_MANIFEST_URL:-$(python3 "$SCRIPT_DIR/brand_literals.py" URL_UPDATE_MANIFEST)}"
wait_seconds="${LEDGER_WAIT_SECONDS:-5400}"
interval="${LEDGER_POLL_SECONDS:-30}"
deadline=$((SECONDS + wait_seconds))
live=""
while :; do
  live="$(curl -fsSL --max-time 25 -H 'Cache-Control: no-cache' "$url" 2>/dev/null |
    python3 -c 'import json,sys; print(json.load(sys.stdin).get("version",""))' 2>/dev/null || true)"
  [ "$live" = "$version" ] && break
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "::warning::live version '${live:-unknown}' != $version after ${wait_seconds}s; run the ledger step by hand (docs/reconcile-yuelink-pin.md)" >&2
    exit 0
  fi
  sleep "$interval"
done
echo "live update.json shows $version; recording the signed ledger"
if YUELINK_CI="$CI_ROOT" bash "$recorder"; then
  echo "ledger recorded (local signed commit in $YUETO_ROOT; open its PR and gate as usual)"
else
  echo "::warning::ledger recorder failed; use docs/reconcile-yuelink-pin.md" >&2
fi
exit 0
