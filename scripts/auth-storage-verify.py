#!/usr/bin/env python3
"""Run only the reviewed auth/storage tests; export no raw test diagnostics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

TEST_PATHS = (
    "test/modules/auth_sync_subscription_test.dart",
    "test/modules/auth_init_safety_test.dart",
    "test/modules/auth_dispose_guard_test.dart",
    "test/modules/auth_account_profiles_test.dart",
    "test/modules/auth_logout_revoke_test.dart",
    "test/core/storage",
    "test/modules/auth_login_fast_path_test.dart",
    "test/modules/auth_post_connect_resync_test.dart",
)
MAC_ONLY = "test/core/storage/secure_storage_macos_keychain_test.dart"
REQUIRED_CASES = (
    "secure host read timeout releases sync and ignores late completion",
    "obsolete secure read cannot install failure or resume sync",
)


def exact_sha(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ValueError("identity")
    return value


def summarize(log: Path, source: Path, exit_code: int) -> dict:
    """Consume test_core JSON events; names/messages/paths never pass through."""
    suites: dict[int, str | None] = {}
    tests: dict[int, dict] = {}
    completed: set[int] = set()
    required: set[str] = set()
    counts = {"passed": 0, "failed": 0, "skipped": 0, "allowed_platform_skipped": 0}
    failure_ids: list[str] = []
    done = False
    malformed = False
    for line in log.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            kind = event["type"]
            if done:
                raise ValueError("events after done")
            if kind == "suite":
                suite = event["suite"]
                path = Path(suite["path"])
                relative = (
                    str(path.resolve().relative_to(source.resolve()))
                    if path.is_absolute()
                    else str(path)
                )
                known = (
                    relative in TEST_PATHS
                    or relative == MAC_ONLY
                    or (
                        relative.startswith("test/core/storage/")
                        and ".." not in Path(relative).parts
                    )
                )
                suites[suite["id"]] = relative if known else None
            elif kind == "testStart":
                test = event["test"]
                if test["id"] in tests:
                    raise ValueError("duplicate test")
                tests[test["id"]] = test
            elif kind == "testDone":
                ident = event["testID"]
                test = tests[ident]
                if (
                    ident in completed
                    or type(event["skipped"]) is not bool
                    or type(event["hidden"]) is not bool
                ):
                    raise ValueError("invalid completion")
                completed.add(ident)
                result = event["result"]
                if result not in ("success", "failure", "error"):
                    raise ValueError("invalid result")
                path = suites.get(test["suiteID"])
                if result != "success":
                    counts["failed"] += 1
                    failure_ids.append(
                        hashlib.sha256(str(test["name"]).encode()).hexdigest()
                    )
                elif event["skipped"]:
                    counts["skipped"] += 1
                    if path == MAC_ONLY:
                        counts["allowed_platform_skipped"] += 1
                elif not event["hidden"]:
                    if path is None:
                        raise ValueError("unknown suite")
                    counts["passed"] += 1
                    if path == TEST_PATHS[0] and test["name"] in REQUIRED_CASES:
                        required.add(test["name"])
            elif kind == "done":
                done = event.get("success") is True
                if not done:
                    malformed = True
            # Other protocol events contain arbitrary diagnostics; discard them.
        except (ValueError, TypeError, KeyError):
            malformed = True
    passed = (
        exit_code == 0
        and done
        and not malformed
        and completed == set(tests)
        and counts["passed"] > 0
        and counts["failed"] == 0
        and counts["skipped"] == counts["allowed_platform_skipped"]
        and required == set(REQUIRED_CASES)
    )
    return {
        "status": "passed" if passed else "failed",
        "counts": counts,
        "machine_complete": done and completed == set(tests) and not malformed,
        "required_regressions_passed": len(required),
        "failure_name_sha256": sorted(set(failure_ids)),
        "platform_exclusions": [MAC_ONLY],
    }


def capture(
    command: list[str], cwd: Path, destination: Path, *, env: dict, timeout: int = 1200
) -> int:
    with destination.open("wb") as output:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        ).returncode


def verify(args: argparse.Namespace) -> int:
    source_sha, builder_sha = (
        exact_sha(args.expected_source),
        exact_sha(args.expected_builder),
    )
    source, builder = args.checkout.resolve(), Path(__file__).resolve().parents[1]
    temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    output = args.receipt.resolve()
    if not output.is_relative_to(temp) or output.exists():
        raise ValueError("receipt location")
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema": 1,
        "status": "failed",
        "source": source_sha,
        "builder": builder_sha,
        "sdk": "3.47.8",
        "timezone": "UTC",
        "concurrency": 1,
        "paths": list(TEST_PATHS),
        "stage": "identity",
        "exit_code": None,
    }
    result = 1
    env = dict(os.environ, TZ="UTC")
    try:
        with tempfile.TemporaryDirectory(prefix="auth-raw-", dir=temp) as directory:
            raw = Path(directory)
            for checkout, expected in ((source, source_sha), (builder, builder_sha)):
                code = capture(
                    ["git", "rev-parse", "HEAD"], checkout, raw / "identity", env=env
                )
                if code != 0 or (raw / "identity").read_text().strip() != expected:
                    raise ValueError("identity")
                if (
                    capture(
                        ["git", "diff", "--quiet", "HEAD", "--"],
                        checkout,
                        raw / "clean",
                        env=env,
                    )
                    != 0
                ):
                    raise ValueError("dirty tracked source")
            receipt["stage"] = "sdk"
            receipt["exit_code"] = capture(
                ["flutter", "--version", "--machine"], source, raw / "sdk", env=env
            )
            if (
                receipt["exit_code"] != 0
                or json.loads((raw / "sdk").read_text()).get("frameworkVersion")
                != "3.47.8"
            ):
                raise ValueError("sdk")
            receipt["stage"] = "dependencies"
            receipt["exit_code"] = capture(
                ["flutter", "pub", "get", "--enforce-lockfile"],
                source,
                raw / "pub",
                env=env,
            )
            if receipt["exit_code"] != 0:
                raise ValueError("dependencies")
            receipt["stage"] = "tests"
            receipt["exit_code"] = capture(
                [
                    "flutter",
                    "test",
                    "--no-pub",
                    "--concurrency=1",
                    "--machine",
                    *TEST_PATHS,
                ],
                source,
                raw / "machine",
                env=env,
            )
            receipt.update(summarize(raw / "machine", source, receipt["exit_code"]))
            result = 0 if receipt["status"] == "passed" else 1
    except subprocess.TimeoutExpired:
        receipt["outcome"] = "timeout"
    except KeyboardInterrupt:
        receipt["outcome"] = "cancelled"
    except (OSError, ValueError, TypeError):
        receipt["outcome"] = "verification_failed"
    finally:
        output.write_text(json.dumps(receipt, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--expected-builder", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    try:
        return verify(parser.parse_args())
    except (OSError, ValueError):
        return 1  # Never emit exception text, paths, configuration or environment.


if __name__ == "__main__":
    raise SystemExit(main())
