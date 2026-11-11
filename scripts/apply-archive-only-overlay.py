#!/usr/bin/env python3
"""Apply the reviewed archive-only boundary; accept old or migrated templates."""
from __future__ import annotations

import re
from pathlib import Path
import sys

LEGACY_CALL = '          bash scripts/ci/promote_in_pipeline.sh "$APP_VERSION"'
ARCHIVE_CALL = '          bash scripts/ci/promote_in_pipeline.sh archive-only "$APP_VERSION"'
LEGACY_STEP = '      - name: Sign root manifest and promote (in-pipeline)'
ARCHIVE_STEP = '      - name: Sign immutable release archive'

# Remove historical automatic-root claims when the private template still has
# them. A migrated private template may already carry its own updated comments.
COMMENT_UPDATES = (
    (
        '    # 2026-09-23 (L9): the updater signing seed and the R2 credentials that can\n'
        '    # move the bucket-root update.json live only in `yuelink-release`: `v*`\n'
        '    # tags only, one required reviewer. This is the single human click per\n'
        '    # release — builds and per-platform R2 uploads stay unattended; assembling the candidate and moving the root\n'
        '    # wait for approval. Inert until the owner moves the secrets (see\n'
        '    # docs/archive/2026-09-23-ci-credential-migration.md in the workspace root).\n',
        '    # Signing credentials remain in yuelink-release. This job archives the\n'
        '    # signed candidate only; root/rollout promotion is a separate action.\n'
        '    # Do not infer reviewer protection from the environment name.\n',
    ),
    (
        '      # Public CI never receives the updater private key and never writes the\n'
        '      # bucket-root update.json. The private sign-release-manifest workflow\n'
        '      # promotes this candidate only after signing and archive verification.\n',
        '      # Version-prefix uploads never change the bucket-root update.json.\n'
        '      # The final archive-only step signs and verifies the candidate;\n'
        '      # Build never promotes the root.\n',
    ),
    (
        '# target. This public builder never receives the updater-manifest private key.\n',
        '# target. The archive-only signing step cannot promote the updater root.\n',
    ),
    (
        '              echo "## Candidate published; in-pipeline promotion follows"\n',
        '              echo "## Candidate published; immutable signing archive follows"\n',
    ),
    (
        '              echo "Public CI produced \\`$TAG/$MANIFEST_NAME\\`; the signed root moves in the last step of this job."\n',
        '              echo "Public CI produced \\`$TAG/$MANIFEST_NAME\\`; the final step signs its immutable archive without moving the root."\n',
    ),
    (
        '      # In-pipeline promotion (2026-09-03). Build green ⇒ the signed root\n'
        '      # update.json moves to this version, here, with no operator and no\n'
        '      # second machine. The previous "protected plane" promoters (private\n'
        '      # sign-release-manifest workflow / bastion sealed signer / workstation\n'
        '      # wrapper) stay as fallback but are no longer on the release path: they\n'
        '      # were billing-frozen, never had a successful live run, or ran 40x slower\n'
        '      # to R2 and burned two version numbers (v1.3.34, v1.3.36) with\n'
        '      # half-finished immutable bindings. Owner accepted the trade-off of the\n'
        "      # signing seed living in this public repo's secrets; every check the\n"
        '      # protected promoter performed is reused inside the script.\n',
        '      # Build/sign and client exposure are separate. Mode comes FIRST so an\n'
        '      # older private script rejects it as an invalid version before any write.\n',
    ),
    (
        '            echo "::error::UPDATE_MANIFEST_ED25519_PRIVATE_KEY_B64 secret is not configured; root manifest was NOT promoted (fallback: scripts/ci/release-promote-local.sh on bastion)"\n',
        '            echo "::error::updater signing key is not configured; signed archive was NOT created and root was not changed"\n',
    ),
)


def transform(source: str) -> str:
    calls = re.findall(r'^.*bash scripts/ci/promote_in_pipeline\.sh.*$', source, re.M)
    if calls not in ([LEGACY_CALL], [ARCHIVE_CALL]):
        raise ValueError('private template must have exactly one reviewed archive call anchor')
    if source.splitlines().count(LEGACY_STEP) + source.splitlines().count(ARCHIVE_STEP) != 1:
        raise ValueError('private template archive step is missing, unknown or duplicated')
    source = source.replace(LEGACY_CALL, ARCHIVE_CALL).replace(LEGACY_STEP, ARCHIVE_STEP)
    for old, new in COMMENT_UPDATES:
        if source.count(old) > 1:
            raise ValueError('ambiguous archive policy comment anchor')
        source = source.replace(old, new)
    return source


def apply(path: Path) -> None:
    result = transform(path.read_text())
    path.write_text(result)


if __name__ == '__main__':
    apply(Path(sys.argv[1]))
