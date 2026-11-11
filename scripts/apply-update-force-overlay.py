#!/usr/bin/env python3
"""Rewrite the updater-candidate force block to the four-input helper.

Recognizes exactly one of three environment profiles inside the single
``Generate updater manifest candidate`` step and emits the canonical profile.
Unknown, mixed, partial, duplicated, comment-only, or post-signing anchors
fail before any write. An already canonical step is left unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path


STEP = "      - name: Generate updater manifest candidate\n"
SIGN = "      - name: Sign immutable release archive\n"
ATTESTED = (
    "          ATTESTED_SOURCE_COMMIT: ${{ needs.source_binding.outputs.source_commit }}\n"
)
ENV_FORCE = "          YUELINK_UPDATE_FORCE: ${{ vars.YUELINK_UPDATE_FORCE }}\n"
ENV_PLATFORM = (
    "          YUELINK_UPDATE_PLATFORM_FORCE: ${{ vars.YUELINK_UPDATE_PLATFORM_FORCE }}\n"
)
ENV_REASON = (
    "          YUELINK_UPDATE_FORCE_REASON: ${{ vars.YUELINK_UPDATE_FORCE_REASON }}\n"
)
ENV_PLATFORMS = (
    "          YUELINK_UPDATE_FORCE_PLATFORMS: ${{ vars.YUELINK_UPDATE_FORCE_PLATFORMS }}\n"
)
ENV_SALT = "          YUELINK_UPDATE_ROLLOUT_SALT: ${{ vars.YUELINK_UPDATE_ROLLOUT_SALT }}\n"
ENV_PERCENT = (
    "          YUELINK_UPDATE_ROLLOUT_PERCENT: ${{ vars.YUELINK_UPDATE_ROLLOUT_PERCENT }}\n"
)

PROFILE_A = ATTESTED + ENV_SALT + ENV_PERCENT + ENV_PLATFORMS
PROFILE_B = ATTESTED + ENV_FORCE + ENV_REASON
PROFILE_C = ATTESTED + ENV_FORCE + ENV_PLATFORM + ENV_REASON + ENV_PLATFORMS

CALL_OLD = (
    "          python3 .release-builder/scripts/apply-update-rollout.py \\\n"
    '            --manifest "$MANIFEST_NAME"'
)
CALL_NEW = (
    "          python3 .release-builder/scripts/apply-update-force.py \\\n"
    '            --manifest "$MANIFEST_NAME"'
)
COMMENT_A = (
    "          # Optional cohort. Unset release-environment variables leave the\n"
    "          # keys absent (percent 100, global hard). The policy builder checks\n"
    "          # percent 0-100 and that forcePlatforms names exist in platforms.\n"
    "          # Lowering percent to 0 or removing hard takes effect on the next\n"
    "          # client check and does not downgrade an already-updated install.\n"
)
COMMENT_B = (
    "          # Optional force policy. Unset variables leave force absent (a prompt).\n"
    "          # hard requires forceReason and lists every platform except iOS.\n"
    "          # Percentage rollout is rejected. Removing hard on a later signed\n"
    "          # manifest takes effect on the next client check and does not\n"
    "          # downgrade an already-updated install.\n"
)
COMMENT_C = (
    "          # Apply the reviewed force policy before signing. Global force hard\n"
    "          # with a reason and no forcePlatforms forces every platform, including\n"
    "          # iOS. platformForce still names non-iOS platforms only. Never modify\n"
    "          # signed bytes.\n"
)


def _step_body(source: str) -> str:
    if source.count(STEP) != 1:
        raise ValueError("updater candidate step is missing or duplicated")
    start = source.index(STEP)
    rest = source[start + len(STEP):]
    marker = "\n      - name: "
    end = rest.find(marker)
    if end < 0:
        raise ValueError("updater candidate step has no following step")
    return source[start:start + len(STEP) + end]


def transform(source: str) -> str:
    body = _step_body(source)
    if source.count(SIGN) != 1:
        raise ValueError("archive signing step is missing or duplicated")
    profiles = {
        "a": body.count(PROFILE_A),
        "b": body.count(PROFILE_B),
        "c": body.count(PROFILE_C),
    }
    present = [name for name, count in profiles.items() if count]
    if any(count > 1 for count in profiles.values()) or len(present) != 1:
        raise ValueError("updater force environment block is unknown, mixed or duplicated")
    old_calls = body.count(CALL_OLD)
    new_calls = body.count(CALL_NEW)
    if old_calls + new_calls != 1:
        raise ValueError("updater force helper is missing, duplicated or not executable")
    call = CALL_NEW if new_calls else CALL_OLD
    if source.index(call) > source.index(SIGN):
        raise ValueError("updater force helper is after signing")
    profile = present[0]
    if profile == "c" and new_calls == 1:
        if body.count(COMMENT_C) != 1 or "YUELINK_UPDATE_ROLLOUT_" in body:
            raise ValueError("canonical force block is incomplete or mixed")
        return source
    if profile == "c" or new_calls:
        raise ValueError("partial force migration is rejected")
    env = PROFILE_A if profile == "a" else PROFILE_B
    comment = COMMENT_A if profile == "a" else COMMENT_B
    if body.count(env) != 1 or body.count(comment) != 1:
        raise ValueError("force anchor is ambiguous")
    updated = (
        source.replace(env, PROFILE_C, 1)
        .replace(comment, COMMENT_C, 1)
        .replace(CALL_OLD, CALL_NEW, 1)
    )
    if updated == source or "YUELINK_UPDATE_ROLLOUT_" in _step_body(updated):
        raise ValueError("force overlay did not reach the candidate step")
    return updated


def apply(path: Path) -> None:
    path.write_text(transform(path.read_text()))


if __name__ == "__main__":
    try:
        apply(Path(sys.argv[1]))
    except (ValueError, IndexError) as exc:
        raise SystemExit(str(exc) or "usage: apply-update-force-overlay.py <workflow>") from exc
