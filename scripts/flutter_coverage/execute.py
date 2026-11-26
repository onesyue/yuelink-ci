"""Run one worker's groups, including engine-fault splits and single-file retries."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from flutter_coverage.reports import cases, coverage_rows, recoverable, timeout_classification
from flutter_coverage.resources import read_text, timed_command
from flutter_coverage.settings import (
    ENGINE_FAULT_EXIT_CODES,
    ISOLATED,
    LIMITS,
    TIMEOUT_EXIT_CODE,
)
from flutter_coverage.worker import run_flutter_worker

_RESOURCE_PATTERNS = {
    "user_cpu_seconds": r"User time \(seconds\): ([0-9.]+)",
    "system_cpu_seconds": r"System time \(seconds\): ([0-9.]+)",
    "max_rss_kib": r"Maximum resident set size \(kbytes\): ([0-9]+)",
}


class Job:
    def __init__(self, out: Path, groups: list, worker: Path, counter, mode: str) -> None:
        self.out = out
        self.groups = groups
        self.worker = worker
        self.counter = counter
        self.mode = mode


def clear_compiler_cache(worker: Path) -> None:
    cache = worker / "build/test_cache"
    if cache.is_symlink() or cache.parent.is_symlink():
        raise ValueError("worker compiler cache escaped its owned directory")
    if cache.exists():
        shutil.rmtree(cache)


def invoke(job: Job, index: int, tests: list[str], report: Path, lcov: Path) -> subprocess.CompletedProcess:
    # Reuse successful ordinary compilations; isolate known crash cases.
    if len(tests) == 1 and tests[0] in ISOLATED:
        clear_compiler_cache(job.worker)
    command = [
        "timeout", "--signal=TERM",
        f"--kill-after={LIMITS['COVERAGE_GROUP_KILL_AFTER_SECONDS']}s",
        str(LIMITS["COVERAGE_GROUP_DEADLINE_SECONDS"]),
        "flutter", "test", "--no-pub", "--coverage", "--machine",
        "--concurrency=1", "--coverage-path", str(lcov), *tests,
    ]
    print(f"Flutter coverage group {index + 1}/{len(job.groups)}: {len(tests)} files", flush=True)
    attempt = report.stem
    group_receipt = {"index": index, "files": tests, "worker": job.worker.name,
                     "command": command, "started_at": time.time(), "exit_code": None}
    start = time.monotonic()
    result = None
    try:
        with report.open("w") as log:
            result = run_flutter_worker(
                timed_command(command, job.out / f"{attempt}.resource.txt"), job.worker, log)
        group_receipt["exit_code"] = result.returncode
    finally:
        _write_group_receipt(job, attempt, group_receipt, start)
    if result is None:
        raise RuntimeError("flutter group produced no result")
    return result


def _write_group_receipt(job: Job, attempt: str, group_receipt: dict, start: float) -> None:
    raw_resource = read_text(job.out / f"{attempt}.resource.txt")
    metrics = {}
    for key, pattern in _RESOURCE_PATTERNS.items():
        matched = re.search(pattern, raw_resource or "")
        metrics[key] = float(matched[1]) if matched else None
    group_receipt.update(elapsed_seconds=time.monotonic() - start, finished_at=time.time(),
                         resources=metrics,
                         resource_status="OBSERVED" if all(v is not None for v in metrics.values()) else "UNKNOWN",
                         resource_scope="per-command process tree; not concurrent job peak")
    (job.out / f"{attempt}.execution.json").write_text(json.dumps(group_receipt, indent=2) + "\n")


def preserve(job: Job, attempt: str, report: Path, lcov: Path | None, exit_code: int) -> str:
    """Keep the first failed attempt and its classification before any retry."""
    original = job.out / f"{attempt}.initial.jsonl"
    shutil.copyfile(report, original)
    if lcov is not None and lcov.is_file():
        shutil.copyfile(lcov, job.out / f"{attempt}.initial.info")
    classification = timeout_classification(original)
    (job.out / f"{attempt}.timeout.json").write_text(json.dumps({
        "classification": classification, "exit_code": exit_code, "report": original.name,
    }, indent=2) + "\n")
    return classification


def _write_retry_timeout(job: Job, attempt: str, report: Path, exit_code: int, classification: str) -> None:
    (job.out / f"{attempt}.retry.timeout.json").write_text(json.dumps({
        "classification": classification, "exit_code": exit_code, "report": report.name,
    }, indent=2) + "\n")


def retry_single(job: Job, index: int, attempt: str, path: str) -> tuple[Path, Path]:
    """Re-run one file once after an engine shutdown fault; never twice."""
    retry_report = job.out / f"{attempt}.retry.jsonl"
    retry_lcov = job.out / f"{attempt}.retry.info"
    result = invoke(job, index, [path], retry_report, retry_lcov)
    if result.returncode:
        clear_compiler_cache(job.worker)
        classification = timeout_classification(retry_report)
        _write_retry_timeout(job, attempt, retry_report, result.returncode, classification)
        if classification == "recorded_failure":
            raise ValueError(f"single-file retry for {path} recorded a failure; see {retry_report.name}")
        raise ValueError(
            f"engine shutdown fault timed out for file {path}; "
            f"single-file retry limit reached; see {retry_report.name}")
    return retry_report, retry_lcov


def retry_pure_timeout(job: Job, index: int, attempt: str, path: str) -> tuple[Path, Path]:
    """Re-run one file once after a timeout with no recorded test failure."""
    retry_report = job.out / f"{attempt}.retry.jsonl"
    retry_lcov = job.out / f"{attempt}.retry.info"
    result = invoke(job, index, [path], retry_report, retry_lcov)
    if result.returncode:
        clear_compiler_cache(job.worker)
        classification = timeout_classification(retry_report)
        _write_retry_timeout(job, attempt, retry_report, result.returncode, classification)
        if classification == "recorded_failure":
            raise ValueError(
                f"single-file retry for {path} recorded a failure; see {retry_report.name}")
        raise ValueError(
            f"infra-timeout: group {index} file {path} timed out after one retry "
            f"with no recorded test failure; see {retry_report.name}")
    return retry_report, retry_lcov


def stitch(job: Job, index: int, tests: list[str], report: Path, lcov: Path) -> None:
    """Bisect engine-fault timeouts and stitch validated successful leaves."""
    leaves: list[tuple[Path, Path]] = []

    def split(files: list[str], path: str = "") -> None:
        _split_half(job, index, files, path, leaves)

    split(tests)
    _join_leaves(leaves, report, lcov)


def _split_half(job: Job, index: int, files: list[str], path: str, leaves: list[tuple[Path, Path]]) -> None:
    mid = len(files) // 2
    for part, half in enumerate((files[:mid], files[mid:])):
        half_path = f"{path}{part}"
        attempt = f"{index}.half{half_path}"
        half_report = job.out / f"{attempt}.jsonl"
        half_lcov = job.out / f"{attempt}.info"
        half_result = invoke(job, index, half, half_report, half_lcov)
        if half_result.returncode:
            clear_compiler_cache(job.worker)
            code = half_result.returncode
            classification = preserve(job, attempt, half_report, None, code)
            if code in ENGINE_FAULT_EXIT_CODES and classification == "engine_shutdown_fault":
                if len(half) == 1:
                    half_report, half_lcov = retry_single(job, index, attempt, half[0])
                else:
                    _split_half(job, index, half, half_path, leaves)
                    continue
            else:
                if code == TIMEOUT_EXIT_CODE and classification == "no_recorded_failure":
                    raise ValueError(
                        f"infra-timeout: group {index} timed out after one retry "
                        f"with no recorded test failure; see {attempt}.initial.jsonl")
                raise subprocess.CalledProcessError(code, half_result.args)
        cases(half_report, job.worker, half, job.counter)
        if not half_lcov.is_file():
            raise ValueError(f"coverage report missing for group {index} half {half_path}")
        leaves.append((half_report, half_lcov))


def _join_leaves(leaves: list[tuple[Path, Path]], report: Path, lcov: Path) -> None:
    parts: list[str] = []
    coverage: list[str] = []
    offset = 0
    for part, (half_report, half_lcov) in enumerate(leaves):
        rewritten, max_id = _rewrite_events(half_report, offset, part, len(leaves))
        parts.append("\n".join(rewritten) + ("\n" if rewritten else ""))
        coverage.append(half_lcov.read_text())
        offset += max_id + 1
    report.write_text("".join(parts))
    lcov.write_text("".join(coverage))


def _rewrite_events(half_report: Path, offset: int, part: int, leaf_count: int) -> tuple[list[str], int]:
    max_id = 0
    rewritten = []
    for line in half_report.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            rewritten.append(line)
            continue
        if isinstance(event, dict):
            for record in (event, event.get("suite", {}), event.get("test", {})):
                for key in ("id", "suiteID", "testID"):
                    if isinstance(record.get(key), int):
                        max_id = max(max_id, record[key])
            for key in ("suiteID", "testID"):
                if key in event:
                    event[key] += offset
        if isinstance(event, dict) and event.get("type") == "suite":
            event["suite"]["id"] += offset
        elif isinstance(event, dict) and event.get("type") == "testStart":
            event["test"]["id"] += offset
            event["test"]["suiteID"] += offset
        elif isinstance(event, dict) and event.get("type") == "done" and part != leaf_count - 1:
            continue
        elif isinstance(event, dict) and event.get("type") == "start" and part != 0:
            continue
        rewritten.append(json.dumps(event))
    return rewritten, max_id


def _recover_group(job: Job, index: int, tests: list[str], report: Path, lcov: Path, result) -> None:
    clear_compiler_cache(job.worker)
    code = result.returncode
    # Preserve the first attempt even if successful halves replace
    # the canonical report. A retry never erases a real failure.
    classification = preserve(job, str(index), report, lcov, code)
    if job.mode != "directory_groups" or not recoverable(code, classification):
        if classification == "recorded_failure":
            raise ValueError(
                f"failed group {index} (exit {code}) recorded a failure; "
                f"see {index}.initial.jsonl")
        raise subprocess.CalledProcessError(code, result.args)
    if len(tests) > 1:
        stitch(job, index, tests, report, lcov)
        return
    if classification == "engine_shutdown_fault":
        retry_report, retry_lcov = retry_single(job, index, str(index), tests[0])
        shutil.copyfile(retry_report, report)
        shutil.copyfile(retry_lcov, lcov)
        return
    if code == TIMEOUT_EXIT_CODE and classification == "no_recorded_failure":
        retry_report, retry_lcov = retry_pure_timeout(job, index, str(index), tests[0])
        shutil.copyfile(retry_report, report)
        shutil.copyfile(retry_lcov, lcov)
        return
    raise subprocess.CalledProcessError(code, result.args)


def _normalize_lcov(lcov: Path, worker: Path) -> None:
    lines = lcov.read_text().splitlines(keepends=True)
    for i, line in enumerate(lines):
        if not line.startswith("SF:"):
            continue
        path = line[3:].strip()
        if path.startswith(str(worker) + "/"):
            path = path[len(str(worker)) + 1:]
        if not path.startswith("lib/") or ".." in Path(path).parts:
            raise ValueError("coverage source escaped the exact application tree")
        lines[i] = "SF:" + path + "\n"
    lcov.write_text("".join(lines))


def _run_group(job: Job, index: int, tests: list[str], union_lock, case_union, coverage_union):
    report = job.out / f"{index}.jsonl"
    lcov = job.out / f"{index}.info"
    result = invoke(job, index, tests, report, lcov)
    if result.returncode:
        _recover_group(job, index, tests, report, lcov, result)
    observed, visible = cases(report, job.worker, tests, job.counter)
    if not lcov.is_file():
        raise ValueError(f"coverage report missing for file {index}")
    _normalize_lcov(lcov, job.worker)
    rows = coverage_rows(lcov, job.worker)
    with union_lock:
        case_union.update(visible)
        coverage_union.update(rows)
    return index, observed, lcov


def execute(worker: Path, assignments: list[tuple[int, list[str]]], *, out: Path, groups: list,
            counter, mode: str, stop, union_lock, case_union, coverage_union) -> list:
    job = Job(out, groups, worker, counter, mode)
    completed = []
    try:
        for index, tests in assignments:
            if stop.is_set():
                break
            completed.append(_run_group(job, index, tests, union_lock, case_union, coverage_union))
        subprocess.run(["git", "diff", "--exit-code", "HEAD"], cwd=worker, check=True)
        return completed
    except BaseException:
        stop.set()
        raise
