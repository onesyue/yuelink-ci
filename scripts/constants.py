"""Budgets, floors and non-brand endpoints for YueLink CI scripts.

One name per meaning. Brand hosts stay in ``scripts/brand_literals.py``;
this module does not repeat them and does not read another repository.
Policy floors keep the reviewed minimum. They are not knobs.

``CI_CHECK_ATTEMPTS`` and ``CI_CHECK_INTERVAL_SECONDS`` are the defaults
for the deploy-specific ``CI_CHECK_ATTEMPTS`` and ``CI_CHECK_INTERVAL``
environment overrides. ``WORKERS`` is the default for
``YUELINK_COVERAGE_MAX_WORKERS``. Call sites apply those overrides.
Nothing else here is deploy-specific.
"""

from __future__ import annotations

import sys

# --- Endpoints. Not brand hosts. ---

YUELINK_PRIVATE_REPOSITORY = "https://github.com/onesyue/yuelink.git"
ACTIONS_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
SLSA_PROVENANCE_PREDICATE = "https://slsa.dev/provenance/v1"
CLOUDFLARE_API_ORIGIN = "https://api.cloudflare.com"

# --- Timeouts and intervals, in seconds unless the name says otherwise. ---

ANTIFREEZE_GIT_TIMEOUT_SECONDS = 30
IOS_COMPILER_DISCOVERY_TIMEOUT_SECONDS = 60
IOS_NATIVE_BUILD_TIMEOUT_SECONDS = 2400
IOS_GIT_REV_PARSE_TIMEOUT_SECONDS = 60
IOS_SIMCTL_LIST_TIMEOUT_SECONDS = 120
IOS_OTOOL_PROBE_TIMEOUT_SECONDS = 120
GH_COMMAND_TIMEOUT_SECONDS = 120
CI_CHECK_ATTEMPTS = 30
CI_CHECK_INTERVAL_SECONDS = 20
GH_PROCESS_LIST_TIMEOUT_SECONDS = 2
GH_PROCESS_GROUP_POLL_SECONDS = 0.05
GH_READ_ATTEMPTS = 3
GH_READ_ATTEMPT_SECONDS = 60
GH_READ_TOTAL_SECONDS = 150
AUTH_STORAGE_COMMAND_TIMEOUT_SECONDS = 1200
ATTESTATION_DOWNLOAD_TIMEOUT_SECONDS = 150
CHANNEL_FETCH_TIMEOUT_SECONDS = 30
COVERAGE_WORKER_TIMEOUT_SECONDS = 350
COVERAGE_VM_STAT_TIMEOUT_SECONDS = 5
COVERAGE_GITLINK_TIMEOUT_SECONDS = 120
COVERAGE_FLUTTER_VERSION_TIMEOUT_SECONDS = 30
COVERAGE_GROUP_KILL_AFTER_SECONDS = 30
COVERAGE_GROUP_DEADLINE_SECONDS = 300
COVERAGE_PUB_GET_TIMEOUT_SECONDS = 120
COVERAGE_WORKTREE_REMOVE_TIMEOUT_SECONDS = 60
GO_MOD_TIDY_TIMEOUT_SECONDS = 120
GO_MOD_EDIT_TIMEOUT_SECONDS = 10
GO_LIST_TIMEOUT_SECONDS = 30
OSV_SCAN_TIMEOUT_SECONDS = 105
ARTIFACT_LIST_TIMEOUT_SECONDS = 60
RELEASE_CLOSURE_FETCH_TIMEOUT_SECONDS = 120
CURL_CONNECT_TIMEOUT_SECONDS = 10
SOURCE_FETCH_LOW_SPEED_BYTES_PER_SECOND = 1000
SOURCE_FETCH_LOW_SPEED_SECONDS = 60
SOURCE_FETCH_ATTEMPTS = 3
PUBLIC_TAG_LOOKUP_ATTEMPTS = 3
PUBLIC_TAG_VERIFY_ATTEMPTS = 12
PUBLIC_TAG_VERIFY_INTERVAL_SECONDS = 5
R2_PROOF_LIFETIME_SECONDS = 1800

# --- Counts, floors and read bounds. ---

MIN_TEST_FILES = 8
MIN_HASHED_FILES = 20
MIN_FILES = 80
MIN_PRIVATE_CHECKOUTS = 7
PAGE_LIMIT = 20
MAX_SUMS_BYTES = 65536
MAX_AGE_SECONDS = 15 * 60
MAX_FUTURE_SKEW_SECONDS = 2 * 60
MAX_ENVELOPE_BYTES = 64 * 1024
MIN_BODY_BYTES = 1024
MAX_BODY_BYTES = 1024 * 1024
MAX_LATENCY_MS = 20_000
MINIMUM_FILES = 500
MINIMUM_TESTS = 2044
WORKERS = 4
GROUP_FILE_CAP = 8
GROUP_WEIGHT_BUDGET = 8
# GNU timeout's status when the command exceeds its deadline. Not a knob.
TIMEOUT_EXIT_CODE = 124
GH_READ_OUTPUT_LIMIT = 16 * 1024 * 1024


def main(argv: list[str]) -> int:
    names = {
        name: value
        for name, value in globals().items()
        if name.isupper() and type(value) in (int, float, str)
    }
    if len(argv) != 2 or argv[1] not in names:
        print("usage: constants.py NAME", file=sys.stderr)
        return 2
    print(names[argv[1]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
