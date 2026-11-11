#!/usr/bin/env python3
"""Attach optional rollout and per-platform force keys to an unsigned candidate.

The release environment supplies the values. When they are unset this script
leaves the file bytes unchanged: a missing `rollout` means percent 100, and a
missing `forcePlatforms` keeps `force: hard` global.

Lowering percent to 0, or publishing the next manifest without hard, takes
effect on the next client check. An already-upgraded client is not downgraded.
Version comparison only moves forward, so a rollback build must use a higher
version.

Environment (empty means absent):
  YUELINK_UPDATE_ROLLOUT_SALT
  YUELINK_UPDATE_ROLLOUT_PERCENT
  YUELINK_UPDATE_FORCE_PLATFORMS   comma-separated platform keys

Reviewed force policy (policy/update-force.json, optional):
  {"version": "X.Y.Z", "force": "soft"|"hard",
   "forceReason": "security"|"incompatible"}
It applies only to a candidate whose version equals `version`, so it never
carries over to a later release by accident. A missing file changes nothing.
Without `forcePlatforms`, `force: hard` applies to every platform.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
from pathlib import Path


def load_verifier(path: Path):
    spec = importlib.util.spec_from_file_location("verify_update_manifest", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load verifier at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _present(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def parse_force_platforms(raw: str | None) -> list[str] | None:
    text = _present(raw)
    if text is None:
        return None
    names = [part.strip() for part in text.split(",")]
    if any(not name for name in names):
        raise ValueError("forcePlatforms contains an empty platform name")
    if len(names) != len(set(names)):
        raise ValueError("forcePlatforms contains a duplicate platform name")
    return names


FORCE_POLICY_KEYS = {"version", "force", "forceReason"}


def load_force_policy(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or set(value) != FORCE_POLICY_KEYS:
        raise ValueError("force policy must have exactly version, force, forceReason")
    if not isinstance(value["version"], str) or re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+", value["version"]
    ) is None:
        raise ValueError("force policy version must be X.Y.Z")
    if value["force"] not in {"soft", "hard"}:
        raise ValueError("force policy force must be soft or hard")
    if value["forceReason"] not in {"security", "incompatible"}:
        raise ValueError("force policy forceReason must be security or incompatible")
    return value


def apply_force_policy(manifest: dict, policy: dict | None) -> bool:
    """Attach force/forceReason when the policy names this exact version."""
    if policy is None or manifest.get("version") != policy["version"]:
        return False
    if "force" in manifest or "forceReason" in manifest:
        raise ValueError("candidate already carries force keys")
    manifest["force"] = policy["force"]
    manifest["forceReason"] = policy["forceReason"]
    return True


def apply_rollout(
    manifest: dict,
    *,
    salt: str | None,
    percent: str | None,
    force_platforms: list[str] | None,
) -> bool:
    """Merge the optional keys. Returns whether the object changed."""
    changed = False
    salt_text = _present(salt)
    percent_text = _present(percent)
    if (salt_text is None) != (percent_text is None):
        raise ValueError("rollout salt and percent must be set together")
    if salt_text is not None and percent_text is not None:
        if re.fullmatch(r"(?:0|[1-9][0-9]?|100)", percent_text) is None:
            raise ValueError("rollout percent must be an integer from 0 to 100")
        manifest["rollout"] = {"salt": salt_text, "percent": int(percent_text)}
        changed = True
    if force_platforms is not None:
        manifest["forcePlatforms"] = list(force_platforms)
        changed = True
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--verifier",
        type=Path,
        default=Path(__file__).with_name("verify-update-manifest.py"),
    )
    parser.add_argument("--salt", default=os.environ.get("YUELINK_UPDATE_ROLLOUT_SALT"))
    parser.add_argument(
        "--percent", default=os.environ.get("YUELINK_UPDATE_ROLLOUT_PERCENT")
    )
    parser.add_argument(
        "--force-platforms",
        default=os.environ.get("YUELINK_UPDATE_FORCE_PLATFORMS"),
    )
    parser.add_argument(
        "--force-policy",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "policy/update-force.json",
    )
    args = parser.parse_args()
    verifier = load_verifier(args.verifier)
    manifest = json.loads(args.manifest.read_bytes())
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    changed = apply_rollout(
        manifest,
        salt=args.salt,
        percent=args.percent,
        force_platforms=parse_force_platforms(args.force_platforms),
    )
    changed = apply_force_policy(manifest, load_force_policy(args.force_policy)) or changed
    # Shape only. The unsigned candidate does not yet carry promotionEvidence,
    # so the full signed-manifest policy must not run here.
    verifier._validate_rollout_extension(manifest)
    if not changed:
        return 0
    encoded = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    args.manifest.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
