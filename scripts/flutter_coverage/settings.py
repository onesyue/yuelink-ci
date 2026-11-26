"""Paths and named limits for the Flutter coverage runner."""
from __future__ import annotations

import runpy
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
ROOT = SCRIPTS.parent
PLAN = ROOT / "tests/fixtures/flutter-coverage-groups-20261028.json"
LIMITS = runpy.run_path(str(SCRIPTS / "constants.py"))

REQUIRED_TESTS = {
    "test/services/user_custom_rules_test.dart",
    "test/services/subscription_parser_test.dart",
    "test/core/storage/persisted_schema_test.dart",
    "test/core/storage/settings_service_schema_test.dart",
}
MINIMUM_FILES = LIMITS["MINIMUM_FILES"]
MINIMUM_TESTS = LIMITS["MINIMUM_TESTS"]
WORKERS = LIMITS["WORKERS"]
MACOS_TEST = "test/core/storage/secure_storage_macos_keychain_test.dart"
ISOLATED = REQUIRED_TESTS | {
    "test/governance/mihomo_fork_ledger_test.dart",
    "test/core/storage/subscription_reset_operation_store_test.dart",
    "test/core/storage/tun_datapath_accel_latch_test.dart",
    # flutter_tester segfaults at process shutdown after all tests pass when
    # co-scheduled under --coverage (group 16 timeout, Build YueLink 41320915784).
    "test/services/rotating_log_file_test.dart",
}
# This accepted control is immutable. A corrupt plan must not select a different
# execution mode merely because its source bindings no longer match.
GROUPED_PLAN_SHA256 = "818d2d37d03aa6666595fb4e8af16881c971d4d8865702e0543ab95a77fdd584"
GIB = 1024 ** 3
# Scheduling headroom above the observed 1.53 GiB sample maximum, not a
# full-corpus peak-memory claim. Unknown capacity admits only one worker.
WORKER_MEMORY = 5 * GIB // 2
GROUP_FILE_CAP = LIMITS["GROUP_FILE_CAP"]
# A declaration is the cost unit. Eight small files still share a group.
# A file with many tests is not packed beside them, which is what made
# group 53 spend 300s and then split. Missing declarations and callers
# without a tree keep weight 1.
GROUP_WEIGHT_BUDGET = LIMITS["GROUP_WEIGHT_BUDGET"]
# flutter_tester can segfault or lose its VM service while coverage is being
# collected after every test already passed. Depending on timing the tool then
# hangs until `timeout` fires (124) or exits with its generic test failure code
# (1). Both are admitted only together with an `engine_shutdown_fault` report;
# any failed test terminal, unknown error or other exit code still refuses.
TIMEOUT_EXIT_CODE = LIMITS["TIMEOUT_EXIT_CODE"]
ENGINE_FAULT_EXIT_CODES = frozenset({1, TIMEOUT_EXIT_CODE})
GITLINK_DEPTH_BUDGET = 8
