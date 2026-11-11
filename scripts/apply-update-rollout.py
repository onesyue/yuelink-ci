#!/usr/bin/env python3
"""Attach a force policy to an unsigned candidate. Percentage rollout is rejected.

Unset inputs leave the file bytes unchanged. A soft request is a prompt on every
platform. A hard request keeps global `force` soft and sets `platformForce` to
hard for every platform except iOS. 1.3.174 reads only `force`, so that global
value stays a prompt, including on iOS.
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
    """1.3.174 reads only `force` and `forceDeadline`. `platformForce` is ignored."""
    force = manifest.get("force", "soft")
    if force not in {"soft", "hard"} or not isinstance(force, str):
        raise ValueError("force must be soft or hard")
    if force == "hard" or "forceDeadline" in manifest:
        return "hard"
    return "soft"


def current_client_tier(manifest: dict, platform: str) -> str:
    """Agreed new-client reader. Only `platformForce: hard` forces, and never iOS.

    Historical `force: soft` plus `forcePlatforms` stays soft. This is the
    contract the producer emits. It is not a claim that a shipped client build
    already parses `platformForce`.
    """
    if platform == IOS or platform.startswith("ios"):
        return "soft"
    if manifest.get("platformForce") != "hard":
        return "soft"
    names = manifest.get("forcePlatforms")
    if not isinstance(names, list):
        return "soft"
    return "hard" if platform in names else "soft"


def apply_force(
    manifest: dict,
    *,
    force: str | None,
    reason: str | None,
) -> bool:
    """Merge the force policy. Returns whether the object changed.

    `force=hard` is the operator intent. The wire value of `force` stays soft.
    """
    if "rollout" in manifest or "percent" in manifest:
        raise ValueError("rollout percentage fields are not accepted")
    if "forceDeadline" in manifest:
        raise ValueError("a global deadline would force 1.3.174 including iOS")
    force_text = _present(force)
    reason_text = _present(reason)
    if force_text is None and reason_text is None:
        if manifest.get("force") == "hard":
            raise ValueError("global hard would force 1.3.174 including iOS")
        return False
    if force_text not in {"soft", "hard"}:
        raise ValueError("force must be soft or hard")
    if force_text == "soft":
        if reason_text is not None:
            raise ValueError("soft force does not take forceReason")
        changed = (
            manifest.get("force") != "soft"
            or "forcePlatforms" in manifest
            or "platformForce" in manifest
            or "forceReason" in manifest
        )
        manifest["force"] = "soft"
        manifest.pop("forcePlatforms", None)
        manifest.pop("platformForce", None)
        manifest.pop("forceReason", None)
        return changed
    if reason_text not in REASONS:
        raise ValueError("hard enforcement requires forceReason")
    platforms = non_ios_platforms(manifest)
    if IOS in platforms or any(name.startswith("ios") for name in platforms):
        raise ValueError("iOS is never forced")
    changed = (
        manifest.get("force") != "soft"
        or manifest.get("platformForce") != "hard"
        or manifest.get("forceReason") != reason_text
        or manifest.get("forcePlatforms") != platforms
    )
    manifest["force"] = "soft"
    manifest["platformForce"] = "hard"
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
    if legacy_client_tier(manifest) == "hard":
        raise ValueError("global hard would force 1.3.174 including iOS")
    if current_client_tier(manifest, IOS) != "soft":
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
