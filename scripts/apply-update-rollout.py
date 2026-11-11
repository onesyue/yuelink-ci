#!/usr/bin/env python3
"""Attach a force policy to an unsigned candidate. Percentage rollout is rejected.

Unset inputs leave the file bytes unchanged. `force: soft` is a prompt on every
platform. `force: hard` requires `forceReason` and lists every platform except
iOS, so a client that understands `forcePlatforms` does not hard-gate iOS.

1.3.174 reads only `force`. A hard value is global on that client, including
iOS. Callers that need those clients to stay on a prompt must use soft.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path


IOS = "ios"
REASONS = {"security", "incompatible"}
REJECTED_ENV = (
    "YUELINK_UPDATE_ROLLOUT_SALT",
    "YUELINK_UPDATE_ROLLOUT_PERCENT",
    "YUELINK_UPDATE_FORCE_PLATFORMS",
    "PROMOTE_PERCENT",
    "ROLLOUT_PERCENT",
)


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


def non_ios_platforms(manifest: dict) -> list[str]:
    platforms = manifest.get("platforms")
    if not isinstance(platforms, dict) or IOS not in platforms:
        raise ValueError("ios platform is required so it can be excluded from force")
    names = []
    for name in platforms:
        if not isinstance(name, str) or not name:
            raise ValueError("platform names must be non-empty strings")
        if name == IOS or name.startswith("ios-") or name.startswith("ios"):
            continue
        names.append(name)
    if not names or len(names) != len(platforms) - 1:
        raise ValueError("force must list every non-iOS platform and no iOS alias")
    return sorted(names)


def legacy_client_tier(manifest: dict) -> str:
    """1.3.174 reads only `force`. Absent is soft. `forcePlatforms` is ignored."""
    force = manifest.get("force", "soft")
    if force not in {"soft", "hard"} or not isinstance(force, str):
        raise ValueError("force must be soft or hard")
    return force


def current_client_tier(manifest: dict, platform: str) -> str:
    """`forcePlatforms` limits hard. iOS is soft unless a rejected global hard is present."""
    force = legacy_client_tier(manifest)
    if platform == IOS or platform.startswith("ios"):
        names = manifest.get("forcePlatforms")
        if force == "hard" and (names is None or (isinstance(names, list) and platform in names)):
            return "hard"
        return "soft"
    if force != "hard":
        return "soft"
    names = manifest.get("forcePlatforms")
    if names is None:
        return "hard"
    return "hard" if platform in names else "soft"


def apply_force(
    manifest: dict,
    *,
    force: str | None,
    reason: str | None,
) -> bool:
    """Merge the force policy. Returns whether the object changed."""
    if "rollout" in manifest:
        raise ValueError("rollout percentage fields are not accepted")
    force_text = _present(force)
    reason_text = _present(reason)
    if force_text is None and reason_text is None:
        return False
    if force_text not in {"soft", "hard"}:
        raise ValueError("force must be soft or hard")
    if force_text == "soft":
        if reason_text is not None:
            raise ValueError("soft force does not take forceReason")
        changed = manifest.get("force") != "soft" or "forcePlatforms" in manifest
        manifest["force"] = "soft"
        manifest.pop("forcePlatforms", None)
        return changed
    if reason_text not in REASONS:
        raise ValueError("hard enforcement requires forceReason")
    platforms = non_ios_platforms(manifest)
    if IOS in platforms or any(name.startswith("ios") for name in platforms):
        raise ValueError("iOS is never forced")
    changed = (
        manifest.get("force") != "hard"
        or manifest.get("forceReason") != reason_text
        or manifest.get("forcePlatforms") != platforms
    )
    manifest["force"] = "hard"
    manifest["forceReason"] = reason_text
    manifest["forcePlatforms"] = platforms
    return changed


def reject_retired_env() -> None:
    present = [name for name in REJECTED_ENV if _present(os.environ.get(name))]
    if present:
        raise ValueError("percentage rollout and explicit platform overrides are not accepted")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--verifier",
        type=Path,
        default=Path(__file__).with_name("verify-update-manifest.py"),
    )
    parser.add_argument("--force", default=os.environ.get("YUELINK_UPDATE_FORCE"))
    parser.add_argument(
        "--force-reason",
        default=os.environ.get("YUELINK_UPDATE_FORCE_REASON"),
    )
    args = parser.parse_args()
    reject_retired_env()
    verifier = load_verifier(args.verifier)
    manifest = json.loads(args.manifest.read_bytes())
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    changed = apply_force(manifest, force=args.force, reason=args.force_reason)
    verifier._validate_rollout_extension(manifest)
    if current_client_tier(manifest, IOS) == "hard":
        raise ValueError("iOS is never forced")
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
