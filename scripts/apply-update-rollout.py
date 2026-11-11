#!/usr/bin/env python3
"""Deprecated alias for an unsigned candidate force policy.

Unset and explicit soft inputs delegate to ``apply-update-force.py``.
``force=hard`` is rejected. It is not rewritten into ``platformForce``.
Platform-force inputs belong on the new helper. Percentage rollout is rejected.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path


def load_force():
    path = Path(__file__).with_name("apply-update-force.py")
    spec = importlib.util.spec_from_file_location("apply_update_force", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load force helper at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FORCE = load_force()
load_verifier = FORCE.load_verifier
legacy_client_tier = FORCE.legacy_client_tier
current_client_tier = FORCE.current_client_tier
_present = FORCE._present


def apply_force(manifest: dict, *, force: str | None, reason: str | None) -> bool:
    """Delegate soft and unset policy. Reject the retired hard alias."""
    if _present(force) == "hard":
        raise ValueError(
            "operator force=hard is retired; set platform-force=hard and an "
            "explicit non-iOS list on apply-update-force.py"
        )
    return FORCE.apply_policy(
        manifest,
        force=force,
        platform_force=None,
        reason=reason,
        platforms=None,
    )


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
    if _present(os.environ.get("YUELINK_UPDATE_PLATFORM_FORCE")) or _present(
        os.environ.get("YUELINK_UPDATE_FORCE_PLATFORMS")
    ):
        raise ValueError("platform force inputs belong to apply-update-force.py")
    retired = [
        name
        for name in (
            "YUELINK_UPDATE_ROLLOUT_SALT",
            "YUELINK_UPDATE_ROLLOUT_PERCENT",
            "PROMOTE_PERCENT",
            "ROLLOUT_PERCENT",
        )
        if _present(os.environ.get(name))
    ]
    if retired:
        raise ValueError("percentage rollout is not accepted")
    if _present(args.force) == "hard":
        raise ValueError(
            "operator force=hard is retired; set platform-force=hard and an "
            "explicit non-iOS list on apply-update-force.py"
        )
    FORCE.process_manifest(
        args.manifest,
        verifier_path=args.verifier,
        force=args.force,
        platform_force=None,
        reason=args.force_reason,
        platforms=None,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
