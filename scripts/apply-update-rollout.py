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
