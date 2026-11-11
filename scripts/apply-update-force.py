#!/usr/bin/env python3
"""Attach an explicit force policy to an unsigned candidate.

``--force hard`` plus ``--force-reason`` writes global ``force: hard`` and
``forceReason`` with no ``forcePlatforms`` and no ``platformForce``. That
forces every platform, including iOS. A selective hard update remains
``--platform-force hard`` plus an explicit non-iOS asset list. Percentage
rollout is rejected.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import tempfile
from pathlib import Path


IOS = "ios"
REASONS = {"security", "incompatible"}
RETIRED_ENV = (
    "YUELINK_UPDATE_ROLLOUT_SALT",
    "YUELINK_UPDATE_ROLLOUT_PERCENT",
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


def resolve_input(cli_value: str | None, env_name: str) -> str | None:
    """An explicit CLI value overrides the environment variable."""
    chosen = _present(cli_value)
    if chosen is not None:
        return chosen
    return _present(os.environ.get(env_name))


def legacy_client_tier(manifest: dict) -> str:
    """1.3.174 reads only ``force`` and ``forceDeadline``. ``platformForce`` is ignored."""
    force = manifest.get("force", "soft")
    if force not in {"soft", "hard"} or not isinstance(force, str):
        raise ValueError("force must be soft or hard")
    if force == "hard" or "forceDeadline" in manifest:
        return "hard"
    return "soft"


def current_client_tier(manifest: dict, platform: str) -> str:
    """Producer-side reader for the bytes this publisher emits.

    ``force: hard`` or ``forceDeadline`` forces every platform, including iOS.
    ``platformForce: hard`` forces only the named platforms. Shipped 1.3.174
    reads ``force`` and ignores ``platformForce``; ``legacy_client_tier`` is
    that reader.
    """
    if manifest.get("force") == "hard" or "forceDeadline" in manifest:
        return "hard"
    if manifest.get("platformForce") != "hard":
        return "soft"
    names = manifest.get("forcePlatforms")
    if not isinstance(names, list):
        return "soft"
    return "hard" if platform in names else "soft"


def _parse_names(text: str, manifest: dict, allowed: set[str]) -> list[str]:
    platforms = manifest.get("platforms")
    if not isinstance(platforms, dict):
        raise ValueError("candidate platforms are missing")
    parts = [part.strip() for part in text.split(",")]
    if any(part == "" for part in parts):
        raise ValueError("platform list contains an empty entry")
    if len(parts) != len(set(parts)):
        raise ValueError("platform list contains a duplicate")
    names = []
    for name in parts:
        if name == IOS or name.startswith("ios"):
            raise ValueError("platformForce must name non-iOS platforms")
        if name not in allowed:
            raise ValueError(f"unknown platform {name}")
        if name not in platforms:
            raise ValueError(f"platform {name} is not in the candidate")
        names.append(name)
    return sorted(names)


def apply_policy(
    manifest: dict,
    *,
    force: str | None,
    platform_force: str | None,
    reason: str | None,
    platforms: str | None,
    verifier=None,
) -> bool:
    """Merge an explicit policy. Returns whether the object changed.

    ``force=hard`` stays global. It is not rewritten into ``platformForce``.
    """
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    if "rollout" in manifest or "percent" in manifest:
        raise ValueError("rollout percentage fields are not accepted")
    if "forceDeadline" in manifest:
        raise ValueError("a global deadline would force 1.3.174 including iOS")
    force_text = _present(force)
    platform_text = _present(platform_force)
    reason_text = _present(reason)
    list_text = _present(platforms)
    if force_text == "hard":
        if reason_text not in REASONS:
            raise ValueError("hard enforcement requires forceReason")
        if platform_text is not None or list_text is not None:
            raise ValueError("global hard does not take a platform list")
        changed = (
            manifest.get("force") != "hard"
            or manifest.get("forceReason") != reason_text
            or "forcePlatforms" in manifest
            or "platformForce" in manifest
        )
        manifest["force"] = "hard"
        manifest["forceReason"] = reason_text
        manifest.pop("forcePlatforms", None)
        manifest.pop("platformForce", None)
        return changed
    if force_text not in {None, "soft"}:
        raise ValueError("force must be soft or hard")
    if platform_text is None:
        if reason_text is not None or list_text is not None:
            raise ValueError("a platform list or reason requires platform-force=hard")
        if force_text is None:
            return False
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
    if platform_text != "hard":
        raise ValueError("platform-force must be hard")
    if reason_text not in REASONS:
        raise ValueError("hard enforcement requires forceReason")
    if list_text is None:
        raise ValueError("platform force requires an explicit platform list")
    if verifier is None:
        verifier = load_verifier(Path(__file__).with_name("verify-update-manifest.py"))
    names = _parse_names(list_text, manifest, set(verifier.EXPECTED_PLATFORM_ARTIFACTS))
    changed = (
        manifest.get("force") != "soft"
        or manifest.get("platformForce") != "hard"
        or manifest.get("forceReason") != reason_text
        or manifest.get("forcePlatforms") != names
    )
    manifest["force"] = "soft"
    manifest["platformForce"] = "hard"
    manifest["forceReason"] = reason_text
    manifest["forcePlatforms"] = names
    return changed


def reject_retired_env() -> None:
    present = [name for name in RETIRED_ENV if _present(os.environ.get(name))]
    if present:
        raise ValueError("percentage rollout is not accepted")


def commit_text(path: Path, text: str) -> None:
    """Replace ``path`` from a same-directory temporary file.

    A failed write or replace leaves the original bytes in place and removes
    the temporary file.
    """
    mode = path.stat().st_mode & 0o777
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        if fd >= 0:
            os.close(fd)
        if temporary.exists():
            temporary.unlink()
        raise


def process_manifest(
    path: Path,
    *,
    verifier_path: Path,
    force: str | None,
    platform_force: str | None,
    reason: str | None,
    platforms: str | None,
) -> None:
    raw = path.read_bytes()
    manifest = json.loads(raw)
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    if "sig" in manifest:
        raise ValueError("signed manifest cannot be rewritten")
    verifier = load_verifier(verifier_path)
    changed = apply_policy(
        manifest,
        force=force,
        platform_force=platform_force,
        reason=reason,
        platforms=platforms,
        verifier=verifier,
    )
    verifier._validate_rollout_extension(manifest)
    if not changed:
        return
    encoded = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    commit_text(path, encoded + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--verifier",
        type=Path,
        default=Path(__file__).with_name("verify-update-manifest.py"),
    )
    parser.add_argument("--force", default=None)
    parser.add_argument("--platform-force", default=None)
    parser.add_argument("--force-reason", default=None)
    parser.add_argument("--force-platforms", default=None)
    args = parser.parse_args()
    reject_retired_env()
    process_manifest(
        args.manifest,
        verifier_path=args.verifier,
        force=resolve_input(args.force, "YUELINK_UPDATE_FORCE"),
        platform_force=resolve_input(args.platform_force, "YUELINK_UPDATE_PLATFORM_FORCE"),
        reason=resolve_input(args.force_reason, "YUELINK_UPDATE_FORCE_REASON"),
        platforms=resolve_input(args.force_platforms, "YUELINK_UPDATE_FORCE_PLATFORMS"),
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
