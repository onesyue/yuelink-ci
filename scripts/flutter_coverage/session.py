"""Prepare one coverage gate, run its workers, and write the receipt."""
from __future__ import annotations

import hashlib
import os
import importlib.util
import json
import subprocess
import tempfile
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from flutter_coverage.execute import execute
from flutter_coverage.settings import LIMITS


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def _initial_receipt(out: Path, groups, mode: str, plan_path: Path, runner_path: Path,
                     resources: dict, worker_memory: int) -> tuple[dict, Path]:
    receipt = {"schema": 1, "status": "INCOMPLETE", "groups": groups,
               "execution_mode": mode,
               "union_comparison": ("reviewed_baseline" if mode == "reviewed_groups"
                                    else "directory_groups; no frozen-snapshot equivalence claim"),
               "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
               "runner_sha256": hashlib.sha256(runner_path.read_bytes()).hexdigest(),
               "resources_before": resources, "workers": None,
               "worker_memory_reservation_bytes": worker_memory,
               "resource_acceptance": "NOT_EVALUATED; visible bounds are scheduling inputs only",
               "worker_selection": "CONSERVATIVE_UNKNOWN" if resources["capacity_status"] == "UNKNOWN" else "VISIBLE_BOUNDS",
               "aggregate_owned_job_peak_bytes": None,
               "aggregate_peak_scope": "UNKNOWN; group RSS is not concurrent job peak"}
    receipt_path = out / "execution.json"
    _write_json(receipt_path, receipt)
    return receipt, receipt_path


def _admit_capacity(receipt: dict, receipt_path: Path, resources: dict, worker_count_fn) -> int:
    try:
        capacity = worker_count_fn(resources)
    except ValueError as error:
        receipt.update(status="RESOURCE_REFUSED", resource_error=str(error))
        _write_json(receipt_path, receipt)
        raise
    receipt["workers"] = capacity
    return capacity


def _bind_runtime(root: Path, receipt: dict, receipt_path: Path, plan: dict, runner_path: Path):
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip()
    spec = importlib.util.spec_from_file_location(
        "machine_counter", runner_path.with_name("count-flutter-machine-tests.py"))
    counter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(counter)
    receipt["source_sha"] = source
    receipt["sdk"] = json.loads(subprocess.check_output(
        ["flutter", "--version", "--machine"], cwd=root, text=True,
        timeout=LIMITS["COVERAGE_FLUTTER_VERSION_TIMEOUT_SECONDS"]))
    if any(receipt["sdk"].get(key) != value for key, value in plan["sdk"].items()):
        raise ValueError("grouping SDK differs from reviewed baseline")
    receipt["counter_sha256"] = hashlib.sha256(Path(spec.origin).read_bytes()).hexdigest()
    _write_json(receipt_path, receipt)
    return source, counter


def _remove_worktrees(root: Path, workers: list[Path], gitlinks: list[tuple[Path, Path]]) -> list[str]:
    cleanup_errors = []
    owned = [*reversed(gitlinks), *((root, worker) for worker in workers)]
    for original, target in owned:
        try:
            subprocess.run(["git", "worktree", "remove", "--force", str(target)],
                           cwd=original, check=True,
                           timeout=LIMITS["COVERAGE_WORKTREE_REMOVE_TIMEOUT_SECONDS"])
        except (OSError, subprocess.SubprocessError) as error:
            cleanup_errors.append(str(error))
    return cleanup_errors


def _record_union(out: Path, receipt: dict, completed, case_union: Counter, coverage_union: set,
                  digest_fn, plan: dict, mode: str, shard) -> None:
    case_rows = [[*identity, count] for identity, count in sorted(case_union.items())]
    summary = {"cases_sha256": digest_fn(case_rows), "coverage_sha256": digest_fn(sorted(coverage_union)),
               "visible_cases": sum(case_union.values()),
               "visible_skips": sum(count for (_, _, skipped), count in case_union.items() if skipped),
               "coverage_rows": len(coverage_union)}
    receipt["union"] = summary
    _write_json(out / "case-union.json", case_rows)
    (out / "coverage-union.json").write_text(json.dumps(sorted(coverage_union)) + "\n")
    if shard is None and mode == "reviewed_groups" and summary != plan["baseline_union"]:
        raise ValueError("full-corpus case/skip or coverage union differs from reviewed baseline")


def _emit_lcov(root: Path, completed, shard) -> None:
    # Source-contract tests may execute no application library lines.
    # Flutter still emits their empty LCOV file; absent output refuses.
    nonempty = [path for _, _, path in sorted(completed) if path.stat().st_size]
    if not nonempty:
        if shard is None:
            raise ValueError("no application coverage collected from the complete corpus")
        (root / "coverage/lcov.info").write_text("")
        return
    coverage = [argument for path in nonempty for argument in ("--add-tracefile", str(path))]
    subprocess.run(["lcov", *coverage, "--output-file", "coverage/lcov.info"], cwd=root, check=True)


def _finish_shard(out: Path, shard, selected, expected_indexes, observed: int, source: str,
                  counter, minimum_tests: int) -> None:
    if shard is None:
        counter.enforce_floor(observed, minimum_tests)
        return
    # One shard is not the corpus. The join job enforces the floor.
    _write_json(out / "shard.json", {
        "schema": 1,
        "shard": shard[0],
        "count": shard[1],
        "indexes": expected_indexes,
        "files": [path for _, tests in selected for path in tests],
        "observed": observed,
        "source_sha": source,
    })


def _check_complete(completed, selected, shard) -> list[int]:
    expected_indexes = [index for index, _ in selected]
    if sorted(index for index, _, _ in completed) != expected_indexes:
        raise ValueError(
            "complete test corpus did not execute" if shard is None
            else "coverage shard did not execute its groups")
    return expected_indexes


def _launch(root: Path, temporary: str, capacity: int, source: str, add_gitlinks_fn, limits_pub: int):
    workers = []
    gitlinks = []
    for index in range(capacity):
        worker = Path(temporary).resolve() / f"source-{index}"
        subprocess.run(["git", "worktree", "add", "--detach", str(worker), source], cwd=root, check=True)
        workers.append(worker)
        add_gitlinks_fn(root, worker, source, gitlinks)
        subprocess.run(["flutter", "pub", "get", "--offline", "--enforce-lockfile"],
                       cwd=worker, check=True, timeout=limits_pub)
    return workers, gitlinks


def _run_pool(workers, selected, capacity, out, groups, counter, mode, stop, union_lock, case_union, coverage_union):
    completed = []
    with ThreadPoolExecutor(max_workers=capacity) as pool:
        futures = [pool.submit(
            execute, worker, selected[index::capacity], out=out, groups=groups, counter=counter,
            mode=mode, stop=stop, union_lock=union_lock, case_union=case_union,
            coverage_union=coverage_union) for index, worker in enumerate(workers)]
        for future in as_completed(futures):
            completed.extend(future.result())
    return completed


def _run_workers(root, out, groups, mode, plan, shard, selected, capacity, source, counter,
                 receipt, receipt_path, stop, case_union, coverage_union, union_lock,
                 resource_snapshot_fn, digest_fn, add_gitlinks_fn, minimum_tests: int) -> int:
    completed = []
    workers = []
    gitlinks = []
    with tempfile.TemporaryDirectory(prefix="flutter-coverage-") as temporary:
        try:
            workers, gitlinks = _launch(
                root, temporary, capacity, source, add_gitlinks_fn, LIMITS["COVERAGE_PUB_GET_TIMEOUT_SECONDS"])
            completed = _run_pool(
                workers, selected, capacity, out, groups, counter, mode, stop, union_lock,
                case_union, coverage_union)
            for _, target in gitlinks:
                subprocess.run(["git", "diff", "--exit-code", "HEAD"], cwd=target, check=True)
            expected_indexes = _check_complete(completed, selected, shard)
            observed = sum(count for _, count, _ in completed)
            _finish_shard(out, shard, selected, expected_indexes, observed, source, counter, minimum_tests)
            _record_union(out, receipt, completed, case_union, coverage_union, digest_fn, plan, mode, shard)
            _emit_lcov(root, completed, shard)
            receipt["tests_complete"] = True
        finally:
            receipt["resources_after"] = resource_snapshot_fn()
            _write_json(receipt_path, receipt)
            cleanup_errors = _remove_worktrees(root, workers, gitlinks)
            receipt["cleanup_complete"] = not cleanup_errors
            receipt["cleanup_errors"] = cleanup_errors
            _write_json(receipt_path, receipt)
            if cleanup_errors:
                raise RuntimeError("owned worktree cleanup failed")
    receipt["status"] = "COMPLETE"
    receipt["cleanup_complete"] = True
    _write_json(receipt_path, receipt)
    return observed


def run_coverage(*, runner_path: Path, partition_fn, execution_plan_fn, resource_snapshot_fn,
                 parse_job_shard_fn, select_shard_fn, worker_count_fn, digest_fn, add_gitlinks_fn,
                 macos_test: str, minimum_tests: int, worker_memory: int) -> None:
    root = Path.cwd()
    out = root / "coverage/source-gate"
    out.mkdir(parents=True, exist_ok=False)
    inventory = partition_fn(root)
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", "test", "lib", "pubspec.lock"],
                   cwd=root, check=True)
    plan, groups, mode, plan_path = execution_plan_fn(root, inventory)
    shard = parse_job_shard_fn(os.environ.get("COVERAGE_JOB_SHARD", ""))
    selected = select_shard_fn(groups, shard)
    _write_json(out / "inventory.json", inventory)
    assignments = {path: ("macos_integration" if path == macos_test else "source_contract")
                   for group in inventory for path in group}
    _write_json(out / "platform-assignments.json", assignments)
    resources = resource_snapshot_fn()
    receipt, receipt_path = _initial_receipt(out, groups, mode, plan_path, runner_path, resources, worker_memory)
    capacity = _admit_capacity(receipt, receipt_path, resources, worker_count_fn)
    source, counter = _bind_runtime(root, receipt, receipt_path, plan, runner_path)
    stop = threading.Event()
    case_union: Counter = Counter()
    coverage_union: set = set()
    union_lock = threading.Lock()
    observed = _run_workers(
        root, out, groups, mode, plan, shard, selected, capacity, source, counter, receipt,
        receipt_path, stop, case_union, coverage_union, union_lock, resource_snapshot_fn,
        digest_fn, add_gitlinks_fn, minimum_tests)
    print(f"Flutter tests observed: {observed}; complete file inventory: {len(groups)} groups/{len(inventory)} files",
          flush=True)
