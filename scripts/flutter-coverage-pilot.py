#!/usr/bin/env python3
"""Bounded A/B coverage diagnostic; never emits a release acceptance receipt."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "tests/fixtures/flutter-coverage-pilot-20261019.json"


def module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def manifest(path: Path) -> dict:
    data = json.loads(path.read_text())
    files = data["files"]
    digest = hashlib.sha256(("\n".join(files) + "\n").encode()).hexdigest()
    if (
        data["schema"] != 1
        or len(files) != 36
        or files != sorted(set(files))
        or digest != data["inventory_sha256"]
        or data["workers"] != 4
        or data["group_size"] != 8
        or data["timeout_seconds"] != 300
    ):
        raise ValueError("pilot inventory or execution contract changed")
    if any(
        not p.startswith("test/")
        or ".." in Path(p).parts
        or not p.endswith("_test.dart")
        for p in files
    ):
        raise ValueError("pilot path escaped the test corpus")
    expected_isolated = {
        "test/services/user_custom_rules_test.dart",
        "test/services/subscription_parser_test.dart",
        "test/core/storage/persisted_schema_test.dart",
        "test/core/storage/settings_service_schema_test.dart",
        "test/governance/mihomo_fork_ledger_test.dart",
    }
    if set(data["isolated"]) != expected_isolated or not expected_isolated.issubset(
        files
    ):
        raise ValueError("required compiler isolation boundary missing")
    for filename, digest in data["helper_sha256"].items():
        if filename not in {
            "run-flutter-coverage.py",
            "count-flutter-machine-tests.py",
        }:
            raise ValueError("unknown pilot helper")
        if (
            hashlib.sha256((ROOT / "scripts" / filename).read_bytes()).hexdigest()
            != digest
        ):
            raise ValueError("pilot helper differs from the frozen builder")
    if set(data["helper_sha256"]) != {
        "run-flutter-coverage.py",
        "count-flutter-machine-tests.py",
    }:
        raise ValueError("missing frozen builder helper")
    return data


def groups(data: dict, variant: str) -> list[list[str]]:
    early = [[p] for p in data["isolated"]]
    rest = [p for p in data["files"] if p not in data["isolated"]]
    size = 1 if variant == "A" else data["group_size"]
    return early + [rest[i : i + size] for i in range(0, len(rest), size)]


def relative(path: str, worker: Path, prefix: str) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        candidate = candidate.relative_to(worker)
    if ".." in candidate.parts or not str(candidate).startswith(prefix):
        raise ValueError(f"report path escaped {prefix}: {path}")
    return str(candidate)


def identities(report: Path, worker: Path, expected: list[str], counter) -> Counter:
    lines = report.read_text().splitlines()
    counter.count_test_starts(lines)  # Existing fail-closed start/done protocol.
    suites, started, finished = {}, {}, set()
    result = Counter()
    reached = set()
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue  # The existing parser already rejects corruption after start.
        if not isinstance(event, dict):
            continue
        if event.get("type") == "suite":
            suite = event["suite"]
            suites[suite["id"]] = relative(suite["path"], worker, "test/")
        elif event.get("type") == "testStart":
            test = event["test"]
            if test["id"] in started:
                raise ValueError("duplicate test identity")
            started[test["id"]] = test
        elif event.get("type") == "testDone":
            identity = event["testID"]
            if identity not in started or identity in finished:
                raise ValueError("missing or duplicate test terminal identity")
            finished.add(identity)
            if event["result"] != "success":
                raise ValueError("test failed despite terminal protocol success")
            if event["hidden"]:
                continue
            test = started[identity]
            path = suites[test["suiteID"]]
            if path not in expected:
                raise ValueError("unexpected test file executed")
            reached.add(path)
            name = test["name"].replace(str(worker), "<source>")
            result[(path, name, bool(event["skipped"]))] += 1
    if finished != set(started) or reached != set(expected):
        raise ValueError("visible cases or terminal test events missing")
    return result


def coverage(path: Path, worker: Path) -> set[tuple[str, str, str, bool]]:
    """Compare instrumented and hit line/branch/function unions, not hit counts."""
    rows = set()
    source = None
    for line in path.read_text().splitlines():
        if line.startswith("SF:"):
            source = relative(line[3:], worker, "lib/")
        elif line == "end_of_record":
            source = None
        elif line.startswith(("DA:", "BRDA:", "FNDA:")):
            if source is None:
                raise ValueError("coverage counter without a source record")
            kind, payload = line.split(":", 1)
            fields = payload.split(",")
            if kind == "DA":
                key, hits = fields[0], fields[1]
            elif kind == "BRDA":
                key, hits = ",".join(fields[:3]), fields[3]
            else:
                hits, key = payload.split(",", 1)
            hit = hits != "-" and int(hits) > 0
            rows.add((source, kind, key, False))  # Instrumentation membership.
            if hit:
                rows.add((source, kind, key, True))
    return rows


def compare(left: dict, right: dict) -> None:
    if left["cases"] != right["cases"]:
        raise ValueError("A/B visible case identities or skip classifications differ")
    if not left["coverage"] or left["coverage"] != right["coverage"]:
        raise ValueError("A/B coverage instrumentation or hit union differs/empty")


def run_variant(
    source: Path, output: Path, data: dict, variant: str, runner, counter
) -> dict:
    output.mkdir()
    batches = groups(data, variant)
    (output / "groups.json").write_text(json.dumps(batches, indent=2) + "\n")
    workers, gitlinks = [], []
    started = time.monotonic()
    env = dict(os.environ, TZ="UTC")
    stop = threading.Event()
    with tempfile.TemporaryDirectory(prefix=f"flutter-pilot-{variant}-") as temporary:
        try:
            for index in range(data["workers"]):
                worker = Path(temporary) / f"source-{index}"
                subprocess.run(
                    [
                        "git",
                        "worktree",
                        "add",
                        "--detach",
                        str(worker),
                        data["source_sha"],
                    ],
                    cwd=source,
                    check=True,
                )
                workers.append(worker)
                runner.add_gitlinks(source, worker, data["source_sha"], gitlinks)
                subprocess.run(
                    ["flutter", "pub", "get", "--offline", "--enforce-lockfile"],
                    cwd=worker,
                    env=env,
                    check=True,
                    timeout=120,
                )

            def execute(index: int) -> list[dict]:
                worker = workers[index]
                observed = []
                for number in range(index, len(batches), data["workers"]):
                    if stop.is_set():
                        raise RuntimeError(
                            "another pilot worker failed; no further group started"
                        )
                    tests = batches[number]
                    cache = worker / "build/test_cache"
                    if cache.is_symlink() or cache.parent.is_symlink():
                        raise ValueError("compiler cache escaped disposable worker")
                    if cache.exists():
                        shutil.rmtree(cache)
                    report, lcov = output / f"{number}.jsonl", output / f"{number}.info"
                    command = [
                        "/usr/bin/time",
                        "-v",
                        "-o",
                        str(output / f"{number}.resource.txt"),
                        "timeout",
                        "--signal=TERM",
                        "--kill-after=30s",
                        str(data["timeout_seconds"]),
                        "flutter",
                        "test",
                        "--no-pub",
                        "--coverage",
                        "--machine",
                        "--concurrency=1",
                        "--coverage-path",
                        str(lcov),
                        *tests,
                    ]
                    begin = time.monotonic()
                    with report.open("w") as stream:
                        process = subprocess.run(
                            command,
                            cwd=worker,
                            env=env,
                            stdout=stream,
                            stderr=subprocess.STDOUT,
                            timeout=350,
                            check=False,
                        )
                    receipt = {
                        "index": number,
                        "files": tests,
                        "elapsed_seconds": time.monotonic() - begin,
                        "exit_code": process.returncode,
                        "command": command,
                    }
                    (output / f"{number}.timing.json").write_text(
                        json.dumps(receipt, indent=2) + "\n"
                    )
                    process.check_returncode()
                    cases = identities(report, worker, tests, counter)
                    hits = coverage(
                        lcov, worker
                    )  # Missing LCOV is an explicit failure; empty contract files are legal.
                    observed.append({"cases": cases, "coverage": hits})
                return observed

            def protected_execute(index: int) -> list[dict]:
                try:
                    return execute(index)
                except BaseException:
                    stop.set()
                    raise

            with ThreadPoolExecutor(max_workers=data["workers"]) as pool:
                results = list(pool.map(protected_execute, range(data["workers"])))
            combined = {"cases": Counter(), "coverage": set()}
            for lane in results:
                for result in lane:
                    combined["cases"].update(result["cases"])
                    combined["coverage"].update(result["coverage"])
            if {key[0] for key in combined["cases"]} != set(data["files"]):
                raise ValueError("pilot corpus did not execute completely")
            for worker in workers:
                subprocess.run(
                    ["git", "diff", "--exit-code", "HEAD"], cwd=worker, check=True
                )
            for _, target in gitlinks:
                subprocess.run(
                    ["git", "diff", "--exit-code", "HEAD"], cwd=target, check=True
                )
            combined["elapsed_seconds"] = time.monotonic() - started
            return combined
        finally:
            for original, target in reversed(gitlinks):
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(target)],
                    cwd=original,
                    check=True,
                    timeout=60,
                )
            for worker in workers:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(worker)],
                    cwd=source,
                    check=True,
                    timeout=60,
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--describe",
        action="store_true",
        help="Print the bounded experiment; execute nothing",
    )
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--order", choices=("AB", "BA"), default="AB")
    args = parser.parse_args()
    data = manifest(args.manifest)
    if args.describe:
        print(
            json.dumps(
                {
                    "manifest": data,
                    "A": groups(data, "A"),
                    "B": groups(data, "B"),
                    "acceptance": "NOT_EXECUTED; diagnostic only",
                },
                indent=2,
            )
        )
        return
    if sys.platform != "linux" or args.source is None or args.output is None:
        parser.error("execution requires Linux, --source and a new --output directory")
    source, output = args.source.resolve(strict=True), args.output.resolve()
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source, text=True
    ).strip()
    if head != data["source_sha"]:
        raise ValueError("source checkout differs from frozen pilot SHA")
    subprocess.run(["git", "diff", "--exit-code", "HEAD"], cwd=source, check=True)
    tracked = set(
        subprocess.check_output(
            ["git", "ls-files", "--", "test/"], cwd=source, text=True
        ).splitlines()
    )
    if not set(data["files"]).issubset(tracked) or any(
        (source / p).is_symlink() for p in data["files"]
    ):
        raise ValueError("pilot file absent or symlinked in frozen source")
    sdk = json.loads(
        subprocess.check_output(["flutter", "--version", "--machine"], text=True)
    )
    if any(sdk.get(key) != value for key, value in data["sdk"].items()):
        raise ValueError("Flutter/Dart/engine differs from frozen pilot SDK")
    subprocess.run(
        ["/usr/bin/time", "--version"], check=True, stdout=subprocess.DEVNULL
    )
    output.mkdir(parents=True, exist_ok=False)
    (output / "input.json").write_text(
        json.dumps(
            {
                "manifest": data,
                "order": args.order,
                "sdk": sdk,
                "platform": platform.platform(),
                "cpu_count": os.cpu_count(),
                "meminfo": Path("/proc/meminfo").read_text(),
                "cpu_max": Path("/sys/fs/cgroup/cpu.max").read_text()
                if Path("/sys/fs/cgroup/cpu.max").exists()
                else "UNKNOWN",
                "memory_max": Path("/sys/fs/cgroup/memory.max").read_text()
                if Path("/sys/fs/cgroup/memory.max").exists()
                else "UNKNOWN",
            },
            indent=2,
        )
        + "\n"
    )
    runner = module("coverage_runner", ROOT / "scripts/run-flutter-coverage.py")
    counter = module("machine_counter", ROOT / "scripts/count-flutter-machine-tests.py")
    results = {}
    for variant in args.order:
        results[variant] = run_variant(
            source, output / variant, data, variant, runner, counter
        )
    compare(results["A"], results["B"])
    receipt = {
        "status": "DIAGNOSTIC_PAIR_PASSED",
        "release_acceptance": False,
        "files": len(data["files"]),
        "visible_cases": sum(results["A"]["cases"].values()),
        "seconds": {key: value["elapsed_seconds"] for key, value in results.items()},
    }
    (output / "comparison.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
