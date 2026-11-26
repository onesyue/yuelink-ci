#!/usr/bin/env python3
"""Run the complete Flutter corpus with independent compiler/cache lifetimes."""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from flutter_coverage.gitlinks import add_gitlinks
from flutter_coverage.inventory import (
    assert_directory_groups,
    declaration_weight,
    directory_groups,
    frozen_sdk,
    load_grouped_plan,
    parse_job_shard,
    partition,
    select_execution_plan,
    select_shard,
)
from flutter_coverage.reports import (
    ENGINE_SHUTDOWN_ERRORS,
    cases,
    coverage_rows,
    digest,
    recoverable,
    relative,
    timeout_classification,
)
from flutter_coverage.resources import read_text, resource_snapshot as _resource_snapshot, timed_command, worker_count
from flutter_coverage.session import run_coverage
from flutter_coverage.settings import (
    ENGINE_FAULT_EXIT_CODES,
    GIB,
    GROUPED_PLAN_SHA256,
    GROUP_FILE_CAP,
    GROUP_WEIGHT_BUDGET,
    ISOLATED,
    MACOS_TEST,
    MINIMUM_FILES,
    MINIMUM_TESTS,
    PLAN,
    REQUIRED_TESTS,
    TIMEOUT_EXIT_CODE,
    WORKER_MEMORY,
    WORKERS,
)
from flutter_coverage.worker import run_flutter_worker

# Callers patch these modules on this CLI module. The package uses the same objects.
assert os and platform and subprocess

def resource_snapshot() -> dict:
    return _resource_snapshot(read_text)


def grouped_plan(root: Path, inventory: list[list[str]]):
    return load_grouped_plan(root, inventory, PLAN, ISOLATED, MACOS_TEST)


def execution_plan(root: Path, inventory: list[list[str]]):
    """Select only an explicitly admitted corpus, never retry a failed mode."""
    return select_execution_plan(
        root, inventory, plan_path=PLAN, grouped_plan_sha256=GROUPED_PLAN_SHA256,
        grouped_plan_fn=grouped_plan, directory_groups_fn=directory_groups,
        frozen_sdk_fn=frozen_sdk, assert_directory_groups_fn=assert_directory_groups,
        macos_test=MACOS_TEST)


def main() -> None:
    run_coverage(
        runner_path=Path(__file__).resolve(),
        partition_fn=partition,
        execution_plan_fn=execution_plan,
        resource_snapshot_fn=resource_snapshot,
        parse_job_shard_fn=parse_job_shard,
        select_shard_fn=select_shard,
        worker_count_fn=worker_count,
        digest_fn=digest,
        add_gitlinks_fn=add_gitlinks,
        macos_test=MACOS_TEST,
        minimum_tests=MINIMUM_TESTS,
        worker_memory=WORKER_MEMORY)


if __name__ == "__main__":
    main()
