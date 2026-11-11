#!/usr/bin/env bash
# Admit the force policy for a later root promotion.
# This script does not sign, upload, or write update.json.
set -euo pipefail

[ "${GITHUB_EVENT_NAME:-}" = workflow_dispatch ] && [ "${GITHUB_REF:-}" = refs/heads/master ] || {
  echo 'root promotion policy requires manual dispatch from master' >&2
  exit 1
}
[[ "${PROMOTE_VERSION:-}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  echo 'version must be X.Y.Z' >&2
  exit 1
}
[[ "${PROMOTE_BASELINE_VERSION:-}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  echo 'baseline must be X.Y.Z' >&2
  exit 1
}
case "${PROMOTE_FORCE:-}" in
  soft|hard) ;;
  *) echo 'force must be soft or hard' >&2; exit 1 ;;
esac
if [ -n "${PROMOTE_PERCENT:-}${ROLLOUT_PERCENT:-}${YUELINK_UPDATE_ROLLOUT_PERCENT:-}" ]; then
  echo 'percentage rollout is not accepted' >&2
  exit 1
fi
reason="${PROMOTE_FORCE_REASON:-}"
if [ "$PROMOTE_FORCE" = hard ]; then
  case "$reason" in
    security|incompatible) ;;
    *) echo 'hard enforcement requires forceReason' >&2; exit 1 ;;
  esac
else
  if [ -n "$reason" ]; then
    echo 'soft force does not take forceReason' >&2
    exit 1
  fi
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 - "$SCRIPT_DIR" "$PROMOTE_VERSION" "$PROMOTE_BASELINE_VERSION" "$PROMOTE_FORCE" "$reason" <<'PY'
import importlib.util
import sys
from pathlib import Path

script_dir, version, baseline, force, reason = sys.argv[1:6]
path = Path(script_dir) / "apply-update-rollout.py"
spec = importlib.util.spec_from_file_location("apply_update_rollout", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
platforms = {
    "android-arm64-v8a": {},
    "android-armeabi-v7a": {},
    "android-x86_64": {},
    "android-universal": {},
    "ios": {},
    "macos-universal": {},
    "windows-amd64-setup": {},
    "windows-amd64-portable": {},
    "linux-amd64-appimage": {},
}
manifest = {"schemaVersion": 1, "platforms": platforms}
module.apply_force(manifest, force=force, reason=reason or None)
if module.legacy_client_tier(manifest) != "soft":
    raise SystemExit("global hard would force 1.3.174 including iOS")
if module.current_client_tier(manifest, "ios") != "soft":
    raise SystemExit("iOS is never forced")
if "rollout" in manifest or "percent" in manifest or manifest.get("force") == "hard":
    raise SystemExit("global hard and percentage rollout are not accepted")
print(f"Admitted {force} root policy for {version} over {baseline}; iOS stays a prompt")
print(f"wireForce={manifest.get('force')}")
if force == "hard":
    print("global force stays soft so 1.3.174 remains a prompt, including iOS")
    print(f"platformForce={manifest['platformForce']}")
    print("forcePlatforms=" + ",".join(manifest["forcePlatforms"]))
PY
