#!/usr/bin/env python3
"""Bounded A/B coverage diagnostic; never emits a release acceptance receipt."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import selectors
import shutil
import signal
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
GROUP25_MANIFEST = ROOT / "tests/fixtures/flutter-coverage-group25-20261020.json"
GROUP25_FILES = [
    "test/domain/proxy_group_icon_test.dart",
    "test/domain/quota_reminder_test.dart",
    "test/domain/store_order_balance_20260926_test.dart",
    "test/domain/unlimited_and_expiry_test.dart",
    "test/domain/withdrawal_test.dart",
    "test/ffi/core_mock_proxy_test.dart",
    "test/ffi/core_mock_test.dart",
    "test/governance/apple_privacy_manifest_test.dart",
]


class EnvironmentNotReady(ValueError):
    pass


def group25_manifest() -> dict:
    data = json.loads(GROUP25_MANIFEST.read_text())
    if (data["schema"] != 1 or data["kind"] != "FIXED_GROUP25_DIAGNOSTIC"
            or data["release_acceptance"] is not False
            or data["source_sha"] != "2af5cd844480a097f8159c0f2562b009f6c39467"
            or data["builder_base"] != "508358ebef580843b6d1412c7023ada65872462c"
            or data["files"] != GROUP25_FILES or data["group_index"] != 25
            or data["workers"] != 1 or data["timeout_seconds"] != 300
            or data["outer_timeout_seconds"] != 350
            or set(data["file_sha256"]) != set(GROUP25_FILES)
            or set(data["runtime_files"]) != {"pubspec.yaml", "pubspec.lock"}
            or set(data["expected"]) != {"group", "withdrawal"}
            or data["sdk"] != {
                "frameworkVersion": "3.47.7", "dartSdkVersion": "3.13.5",
                "frameworkRevision": "abaf9c523780a608bd46686fd5e53740a07077f8",
                "engineRevision": "deb287481e3ce9468f3434937ced4240a70539ca"}
            or set(data["helper_sha256"]) != {
                "scripts/run-flutter-coverage.py", "scripts/count-flutter-machine-tests.py",
                ".github/actions/setup-flutter/install_flutter_sdk.py"}):
        raise ValueError("fixed group25 contract differs")
    for path, expected in data["helper_sha256"].items():
        if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != expected:
            raise ValueError("group25 helper identity differs")
    return data


def group25_cache(data: dict) -> dict:
    """Verify an existing official SDK receipt; never call download/install main."""
    if (sys.platform != "linux" or platform.machine() != "x86_64"
            or os.environ.get("RUNNER_OS") != "Linux"
            or os.environ.get("RUNNER_ARCH") != "X64"):
        raise EnvironmentNotReady("exact native Linux X64 executor unavailable")
    cache = os.environ.get("RUNNER_TOOL_CACHE")
    if not cache:
        raise EnvironmentNotReady("runner SDK cache root absent")
    installer = module("cached_sdk", ROOT / ".github/actions/setup-flutter/install_flutter_sdk.py")
    version = data["sdk"]["frameworkVersion"]
    archive = installer.RELEASES.get((version, "Linux"))
    if archive is None:
        raise EnvironmentNotReady("exact SDK archive identity absent")
    target = Path(cache) / "yue-flutter" / version / "Linux-X64"
    expected = installer.receipt(version, "Linux", "X64", archive[1])
    if not installer.valid_cached_sdk(target, expected, "Linux") or not all(
        (target / path).is_file() for path in (
            "bin/cache/flutter_tools.snapshot", "bin/cache/dart-sdk/bin/dart",
            "bin/cache/artifacts/engine/linux-x64/flutter_tester")
    ):
        raise EnvironmentNotReady("exact verified SDK cache incomplete; no download permitted")
    os.environ["PATH"] = str(target / "bin") + os.pathsep + os.environ["PATH"]
    try:
        sdk = json.loads(subprocess.check_output(
            [str(target / "bin/flutter"), "--version", "--machine"], text=True, timeout=30))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        raise EnvironmentNotReady("cached SDK identity could not be read") from error
    if any(sdk.get(key) != value for key, value in data["sdk"].items()):
        raise EnvironmentNotReady("cached Flutter/Dart/engine identity differs")
    if not Path("/usr/bin/time").is_file() or not shutil.which("timeout"):
        raise EnvironmentNotReady("existing GNU time/timeout unavailable")
    return sdk


def observe_command(command: list[str], worker: Path, output: Path, outer: float) -> dict:
    """Retain raw streams and arrival clocks; never rewrite machine timestamps."""
    start = time.monotonic_ns()
    record = {"command": command, "started_wall_ns": time.time_ns(),
              "started_monotonic_ns": start, "exit_code": None,
              "outer_timeout_seconds": outer, "deadline_monotonic_ns": start + int(outer * 1_000_000_000),
              "clock_scope": "observer arrival; not VM event generation",
              "outer_watchdog": False, "release_acceptance": False}
    process = None
    selector = selectors.DefaultSelector()
    buffers = {"stdout": b"", "stderr": b""}
    count, total = 0, 0
    try:
        with (output / "events.jsonl").open("wb") as stdout, \
             (output / "stderr.log").open("wb") as stderr, \
             (output / "arrivals.jsonl").open("w") as arrivals:
            process = subprocess.Popen(command, cwd=worker, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, start_new_session=True)
            for name in buffers:
                selector.register(getattr(process, name), selectors.EVENT_READ, name)
            while selector.get_map() or process.poll() is None:
                if time.monotonic_ns() - start > outer * 1_000_000_000:
                    record["outer_watchdog"] = True
                    raise TimeoutError("outer command watchdog reached")
                for key, _ in selector.select(0.1):
                    raw = os.read(key.fileobj.fileno(), 65536)
                    if not raw:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(raw)
                    if total > 64 * 1024 * 1024:
                        raise ValueError("diagnostic stream exceeded 64MiB bound")
                    handle = stdout if key.data == "stdout" else stderr
                    handle.write(raw)
                    handle.flush()
                    buffers[key.data] += raw
                    while b"\n" in buffers[key.data]:
                        line, buffers[key.data] = buffers[key.data].split(b"\n", 1)
                        count += 1
                        item = {"sequence": count, "stream": key.data,
                                "wall_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns(),
                                "line_sha256": hashlib.sha256(line + b"\n").hexdigest()}
                        try:
                            event = json.loads(line)
                            if isinstance(event, dict):
                                item["event_type"] = event.get("type")
                                item["machine_time"] = event.get("time")
                        except (ValueError, UnicodeDecodeError):
                            pass
                        if key.data == "stderr" and b"timeout:" in line and b"sending signal" in line:
                            item["timeout_signal_announcement"] = line.decode("utf-8", "replace")
                        arrivals.write(json.dumps(item) + "\n")
                        arrivals.flush()
            record["exit_code"] = process.wait(timeout=5)
    except BaseException as error:
        record["error"] = f"{type(error).__name__}: {error}"
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            record["exit_code"] = process.wait(timeout=5)
        if isinstance(error, KeyboardInterrupt):
            raise
    finally:
        selector.close()
        if process is not None:
            process.stdout.close()
            process.stderr.close()
        record.update(finished_wall_ns=time.time_ns(), finished_monotonic_ns=time.monotonic_ns(),
                      partial_line_bytes={key: len(value) for key, value in buffers.items()},
                      received_bytes=total)
        (output / "execution.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def group25_execute(source: Path, output: Path, data: dict) -> int:
    """Two predeclared diagnostics, never a singleton release fallback."""
    output.mkdir(parents=True, exist_ok=False)
    terminal = {"schema": 1, "status": "INCOMPLETE", "release_acceptance": False,
                "source_sha": data["source_sha"], "builder_base": data["builder_base"],
                "diagnostic_policy": os.environ.get("GITHUB_SHA"), "workers": 1,
                "commands": [], "cleanup_complete": False, "cleanup_errors": [],
                "manifest_sha256": hashlib.sha256(GROUP25_MANIFEST.read_bytes()).hexdigest()}
    runner = module("group25_runner", ROOT / "scripts/run-flutter-coverage.py")
    counter = module("group25_counter", ROOT / "scripts/count-flutter-machine-tests.py")
    gitlinks, workers = [], []
    previous_handler = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        terminal["sdk"] = group25_cache(data)
        terminal["resources_before"] = runner.resource_snapshot()
        runner.worker_count(terminal["resources_before"])  # Existing insufficient-capacity refusal.
        if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip() != data["source_sha"]:
            raise ValueError("group25 source checkout differs")
        subprocess.run(["git", "diff", "--exit-code", "HEAD"], cwd=source, check=True)
        if subprocess.check_output(["git", "rev-parse", "HEAD:lib"], cwd=source, text=True).strip() != data["lib_tree"]:
            raise ValueError("group25 lib tree differs")
        for path, expected in (data["file_sha256"] | data["runtime_files"]).items():
            if (source / path).is_symlink() or hashlib.sha256((source / path).read_bytes()).hexdigest() != expected:
                raise ValueError("group25 input differs: " + path)
        (output / "input.json").write_text(json.dumps(data, indent=2) + "\n")
        with tempfile.TemporaryDirectory(prefix="flutter-group25-") as temporary:
            try:
                for label, files in (("group", data["files"]), ("withdrawal", [data["files"][4]])):
                    directory = output / label
                    directory.mkdir()
                    worker = Path(temporary).resolve() / label
                    subprocess.run(["git", "worktree", "add", "--detach", str(worker), data["source_sha"]],
                                   cwd=source, check=True, timeout=60)
                    workers.append(worker)
                    runner.add_gitlinks(source, worker, data["source_sha"], gitlinks)
                    try:
                        subprocess.run(["flutter", "pub", "get", "--offline", "--enforce-lockfile"],
                                       cwd=worker, check=True, timeout=120)
                    except subprocess.SubprocessError as error:
                        raise EnvironmentNotReady("offline locked dependency cache unavailable") from error
                    cache = worker / "build/test_cache"
                    if cache.is_symlink() or cache.parent.is_symlink():
                        raise ValueError("compiler cache escaped owned worker")
                    if cache.exists():
                        shutil.rmtree(cache)
                    command = ["timeout", "--verbose", "--signal=TERM", "--kill-after=30s", "300",
                               "flutter", "test", "--no-pub", "--coverage", "--machine",
                               "--concurrency=1", "--coverage-path", str(directory / "coverage.info"), *files]
                    context = {"source_sha": data["source_sha"], "sdk": terminal["sdk"],
                               "manifest_sha256": terminal["manifest_sha256"], "files": files,
                               "worker": str(worker), "resources_before": runner.resource_snapshot(),
                               "cleanup_complete": False, "release_acceptance": False}
                    (directory / "context.json").write_text(json.dumps(context, indent=2) + "\n")
                    result = {"label": label, "files": files, "status": "FAILED", "exit_code": None}
                    terminal["commands"].append(result)
                    try:
                        observed = observe_command(runner.timed_command(command, directory / "resource.txt"),
                                                   worker, directory, data["outer_timeout_seconds"])
                        result["exit_code"] = observed["exit_code"]
                    except KeyboardInterrupt:
                        result["status"] = "CANCELED"
                        raise
                    finally:
                        context["resources_after"] = runner.resource_snapshot()
                        (directory / "context.json").write_text(json.dumps(context, indent=2) + "\n")
                    try:
                        if observed["exit_code"] != 0 or observed.get("error") or observed["partial_line_bytes"]["stdout"]:
                            raise ValueError("nonzero/incomplete command")
                        _, cases = runner.cases(directory / "events.jsonl", worker, files, counter)
                        coverage = runner.coverage_rows(directory / "coverage.info", worker)
                        actual = {"visible_cases": sum(cases.values()),
                                  "visible_skips": sum(n for key, n in cases.items() if key[2]),
                                  "cases_sha256": runner.digest(sorted([*key, n] for key, n in cases.items())),
                                  "coverage_sha256": runner.digest(sorted(coverage)), "coverage_rows": len(coverage)}
                        if actual != data["expected"][label]:
                            raise ValueError("exact diagnostic case/skip/coverage baseline differs")
                        result.update(status="PASSED", observed=actual)
                    except (ValueError, OSError, KeyError) as error:
                        result["problem"] = str(error)
                    subprocess.run(["git", "diff", "--exit-code", "HEAD"], cwd=worker, check=True)
            finally:
                for original, target in [*reversed(gitlinks), *((source, worker) for worker in reversed(workers))]:
                    try:
                        clean = subprocess.run(["git", "worktree", "remove", "--force", str(target)],
                                               cwd=original, capture_output=True, text=True, timeout=60, check=False)
                        if clean.returncode:
                            terminal["cleanup_errors"].append({"path": str(target), "exit_code": clean.returncode})
                    except (OSError, subprocess.SubprocessError) as error:
                        terminal["cleanup_errors"].append({"path": str(target), "problem": str(error)})
                terminal["cleanup_complete"] = not terminal["cleanup_errors"]
                for row in terminal["commands"]:
                    context_path = output / row["label"] / "context.json"
                    context = json.loads(context_path.read_text())
                    context["cleanup_complete"] = terminal["cleanup_complete"]
                    context_path.write_text(json.dumps(context, indent=2) + "\n")
        terminal["resources_after"] = runner.resource_snapshot()
        terminal["status"] = ("DIAGNOSTIC_PAIR_PASSED" if len(terminal["commands"]) == 2
                              and all(row["status"] == "PASSED" for row in terminal["commands"])
                              and terminal["cleanup_complete"] else "DIAGNOSTIC_PAIR_FAILED")
    except EnvironmentNotReady as error:
        terminal.update(status="ENVIRONMENT_NOT_READY", problem=str(error))
    except KeyboardInterrupt:
        terminal.update(status="CANCELED", problem="owner/process cancellation; remaining command not started")
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        terminal.update(status="DIAGNOSTIC_PAIR_FAILED", problem=str(error))
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        terminal["finished_wall_ns"] = time.time_ns()
        (output / "diagnostic.json").write_text(json.dumps(terminal, indent=2) + "\n")
    return 0 if terminal["status"] == "DIAGNOSTIC_PAIR_PASSED" else 1


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
    parser.add_argument("--group25", action="store_true", help="Fixed two-command diagnostic; never release acceptance")
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
    if args.group25:
        data = group25_manifest()
        if args.describe:
            print(json.dumps(data, indent=2))
            return
        if args.source is None or args.output is None:
            parser.error("group25 requires --source and a new --output")
        raise SystemExit(group25_execute(args.source.resolve(strict=True), args.output.resolve(), data))
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
