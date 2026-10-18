#!/usr/bin/env python3
"""Run every tracked Flutter test, isolating two coverage teardown crash cases."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess

ISOLATED = {
    "test/services/user_custom_rules_test.dart",
    "test/services/subscription_parser_test.dart",
}
MINIMUM_FILES = 500
MINIMUM_TESTS = 2044


def partition(root: Path) -> list[list[str]]:
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "test/"], cwd=root
    ).decode().split("\0")
    tests = sorted(p for p in tracked if p.endswith("_test.dart"))
    actual = sorted(str(p.relative_to(root)) for p in (root / "test").rglob("*_test.dart"))
    if tests != actual or len(tests) < MINIMUM_FILES:
        raise ValueError("test inventory differs from the complete tracked corpus or scan floor")
    if not ISOLATED.issubset(tests) or any((root / p).is_symlink() for p in tests):
        raise ValueError("isolated tests missing or test paths are symlinks")
    groups = [sorted(ISOLATED), [p for p in tests if p not in ISOLATED]]
    assert sorted(p for group in groups for p in group) == tests
    return groups


def main() -> None:
    root = Path.cwd()
    out = root / "coverage/source-gate"
    out.mkdir(parents=True, exist_ok=False)
    groups = partition(root)
    (out / "inventory.json").write_text(json.dumps(groups, indent=2) + "\n")
    spec = importlib.util.spec_from_file_location(
        "machine_counter", Path(__file__).with_name("count-flutter-machine-tests.py")
    )
    counter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(counter)
    observed = 0
    coverage = []
    for index, tests in enumerate(groups):
        report = out / f"{index}.jsonl"
        lcov = out / f"{index}.info"
        print(f"Flutter coverage group {index}: {len(tests)} test files", flush=True)
        command = [
            "timeout", "--signal=TERM", "--kill-after=30s", "1800",
            "flutter", "test", "--no-pub", "--coverage", "--machine",
            f"--concurrency={1 if index == 0 else 2}", "--coverage-path", str(lcov), *tests,
        ]
        with report.open("w") as log:
            process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True)
            for line in process.stdout:
                log.write(line)
                print(line, end="", flush=True)
            status = process.wait()
        if status:
            raise subprocess.CalledProcessError(status, command[:10])
        with report.open() as log:
            observed += counter.count_test_starts(log)
        if not lcov.is_file() or not lcov.stat().st_size:
            raise ValueError(f"coverage report missing for group {index}")
        coverage.extend(["--add-tracefile", str(lcov)])
    counter.enforce_floor(observed, MINIMUM_TESTS)
    subprocess.run(["lcov", *coverage, "--output-file", "coverage/lcov.info"], check=True)
    print(f"Flutter tests observed: {observed}; complete file inventory: {sum(map(len, groups))}", flush=True)


if __name__ == "__main__":
    main()
