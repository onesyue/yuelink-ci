#!/usr/bin/env python3
"""Run the complete Flutter corpus with independent compiler/cache lifetimes."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

REQUIRED_TESTS = {
    "test/services/user_custom_rules_test.dart",
    "test/services/subscription_parser_test.dart",
    "test/core/storage/persisted_schema_test.dart",
    "test/core/storage/settings_service_schema_test.dart",
}
MINIMUM_FILES = 500
MINIMUM_TESTS = 2044
WORKERS = 2


def partition(root: Path) -> list[list[str]]:
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "test/"], cwd=root
    ).decode().split("\0")
    tests = sorted(p for p in tracked if p.endswith("_test.dart"))
    actual = sorted(str(p.relative_to(root)) for p in (root / "test").rglob("*_test.dart"))
    if tests != actual or len(tests) < MINIMUM_FILES:
        raise ValueError("test inventory differs from the complete tracked corpus or scan floor")
    if not REQUIRED_TESTS.issubset(tests) or any((root / p).is_symlink() for p in tests):
        raise ValueError("required crash cases missing or test paths are symlinks")
    return [[p] for p in tests]


def main() -> None:
    root = Path.cwd()
    out = root / "coverage/source-gate"
    out.mkdir(parents=True, exist_ok=False)
    groups = partition(root)
    (out / "inventory.json").write_text(json.dumps(groups, indent=2) + "\n")
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip()
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", "test"], cwd=root, check=True)
    spec = importlib.util.spec_from_file_location(
        "machine_counter", Path(__file__).with_name("count-flutter-machine-tests.py")
    )
    counter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(counter)
    stop = threading.Event()

    def execute(worker: Path, assignments: list[tuple[int, list[str]]]) -> list[tuple[int, int, Path]]:
        completed = []
        try:
            for index, tests in assignments:
                if stop.is_set():
                    break
                # A fresh flutter command can still initialize its compiler from
                # this previous kernel. Remove only this disposable worker cache.
                cache = worker / "build/test_cache"
                if cache.is_symlink() or cache.parent.is_symlink():
                    raise ValueError("worker compiler cache escaped its owned directory")
                if cache.exists():
                    shutil.rmtree(cache)
                report = out / f"{index}.jsonl"
                lcov = out / f"{index}.info"
                command = [
                    "timeout", "--signal=TERM", "--kill-after=30s", "300",
                    "flutter", "test", "--no-pub", "--coverage", "--machine",
                    "--concurrency=1", "--coverage-path", str(lcov), *tests,
                ]
                print(f"Flutter coverage file {index + 1}/{len(groups)}: {tests[0]}", flush=True)
                with report.open("w") as log:
                    result = subprocess.run(command, cwd=worker, stdout=log,
                                            stderr=subprocess.STDOUT, timeout=350)
                if result.returncode:
                    raise subprocess.CalledProcessError(result.returncode, command)
                with report.open() as log:
                    observed = counter.count_test_starts(log)
                if not lcov.is_file() or not lcov.stat().st_size:
                    raise ValueError(f"coverage report missing for file {index}")
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
                completed.append((index, observed, lcov))
            subprocess.run(["git", "diff", "--exit-code", "HEAD"], cwd=worker, check=True)
            return completed
        except BaseException:
            stop.set()
            raise

    completed = []
    workers = []
    with tempfile.TemporaryDirectory(prefix="flutter-coverage-") as temporary:
        try:
            for index in range(WORKERS):
                worker = Path(temporary) / f"source-{index}"
                subprocess.run(["git", "worktree", "add", "--detach", str(worker), source],
                               cwd=root, check=True)
                workers.append(worker)
                subprocess.run(["flutter", "pub", "get", "--offline", "--enforce-lockfile"],
                               cwd=worker, check=True, timeout=120)
            assignments = list(enumerate(groups))
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                futures = [pool.submit(execute, worker, assignments[index::WORKERS])
                           for index, worker in enumerate(workers)]
                for future in as_completed(futures):
                    completed.extend(future.result())
            if sorted(index for index, _, _ in completed) != list(range(len(groups))):
                raise ValueError("complete test corpus did not execute")
            observed = sum(count for _, count, _ in completed)
            counter.enforce_floor(observed, MINIMUM_TESTS)
            coverage = [argument for _, _, path in sorted(completed)
                        for argument in ("--add-tracefile", str(path))]
            subprocess.run(["lcov", *coverage, "--output-file", "coverage/lcov.info"],
                           cwd=root, check=True)
            print(f"Flutter tests observed: {observed}; complete file inventory: {len(groups)}", flush=True)
        finally:
            for worker in workers:
                subprocess.run(["git", "worktree", "remove", "--force", str(worker)],
                               cwd=root, check=True, timeout=60)


if __name__ == "__main__":
    main()
