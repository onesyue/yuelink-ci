"""Offline behavioral and workflow boundaries for the auth-only Linux lane."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "auth_verify", ROOT / "scripts/auth-storage-verify.py"
)
assert SPEC and SPEC.loader
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)
SECRET = "fixture-token-12345678-1234-1234-1234-123456789abc https://fixture.invalid"


def events() -> list[dict]:
    result = [{"type": "suite", "suite": {"id": 0, "path": verify.TEST_PATHS[0]}}]
    for ident, name in enumerate(verify.REQUIRED_CASES):
        result += [
            {"type": "testStart", "test": {"id": ident, "suiteID": 0, "name": name}},
            {
                "type": "testDone",
                "testID": ident,
                "result": "success",
                "hidden": False,
                "skipped": False,
            },
        ]
    return result + [{"type": "done", "success": True}]


class AuthStorageTests(unittest.TestCase):
    def summary(self, data: list[dict], code: int = 0) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "machine"
            log.write_text("".join(json.dumps(event) + "\n" for event in data))
            return verify.summarize(log, root, code)

    def test_pass_counts_and_discards_arbitrary_messages(self):
        data = events()
        data.insert(1, {"type": "print", "message": SECRET})
        actual = self.summary(data)
        self.assertEqual(actual["status"], "passed")
        self.assertEqual(actual["counts"]["passed"], 2)
        self.assertNotIn(SECRET, json.dumps(actual))
        self.assertEqual(actual["platform_exclusions"], [verify.MAC_ONLY])

    def test_failure_names_are_hashes_and_hidden_failures_fail(self):
        for hidden in (True, False):
            data = events()
            data[1]["test"]["name"] = SECRET
            data[2].update(result="error", hidden=hidden)
            data.insert(2, {"type": "error", "error": SECRET, "stackTrace": SECRET})
            actual = self.summary(data)
            self.assertEqual(actual["status"], "failed")
            self.assertNotIn(SECRET, json.dumps(actual))
            self.assertRegex(actual["failure_name_sha256"][0], r"^[a-f0-9]{64}$")

    def test_missing_required_incomplete_nonzero_duplicate_and_malformed_fail(self):
        variants = [
            events()[:-1],
            events()[:-2] + events()[-1:],
            events() + events()[-1:],
            [{"type": "testDone"}],
            [],
        ]
        missing = events()
        missing[1]["test"]["name"] = "other test"
        variants.append(missing)
        bad_done = events()
        bad_done[-1]["success"] = False
        variants.append(bad_done)
        for data in variants:
            with self.subTest(data=data):
                self.assertEqual(self.summary(data)["status"], "failed")
        self.assertEqual(self.summary(events(), 1)["status"], "failed")

    def test_skips_never_count_as_pass_and_only_exact_mac_file_allowed(self):
        for path, expected in (
            (verify.MAC_ONLY, "passed"),
            (verify.TEST_PATHS[1], "failed"),
        ):
            data = events()
            data[-1:-1] = [
                {"type": "suite", "suite": {"id": 1, "path": path}},
                {"type": "testStart", "test": {"id": 3, "suiteID": 1, "name": SECRET}},
                {
                    "type": "testDone",
                    "testID": 3,
                    "result": "success",
                    "hidden": False,
                    "skipped": True,
                },
            ]
            actual = self.summary(data)
            self.assertEqual(actual["status"], expected)
            self.assertEqual(actual["counts"]["passed"], 2)
            self.assertEqual(actual["counts"]["skipped"], 1)
        skipped_required = events()
        skipped_required[2]["skipped"] = True
        self.assertEqual(self.summary(skipped_required)["status"], "failed")

    def run_fixture(
        self, failing_stage: str | None = None, interrupt: Exception | None = None
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = (root / "source").resolve()
            source.mkdir()
            args = argparse.Namespace(
                checkout=source,
                expected_source="a" * 40,
                expected_builder="b" * 40,
                receipt=root / "out" / "receipt.json",
            )
            commands = []

            def capture(command, cwd, destination, *, env, timeout=1200):
                commands.append(command)
                self.assertEqual(env["TZ"], "UTC")
                if destination.name == failing_stage:
                    if interrupt is not None:
                        raise interrupt
                    destination.write_text(SECRET)
                    return 9
                if destination.name == "identity":
                    destination.write_text(("a" if cwd == source else "b") * 40)
                elif destination.name == "sdk":
                    destination.write_text(json.dumps({"frameworkVersion": "3.47.8"}))
                elif destination.name == "machine":
                    destination.write_text(
                        "".join(json.dumps(e) + "\n" for e in events())
                    )
                else:
                    destination.write_text(SECRET)
                return 0

            with (
                patch.dict(os.environ, RUNNER_TEMP=str(root)),
                patch.object(verify, "capture", side_effect=capture),
            ):
                code = verify.verify(args)
            receipt = json.loads(args.receipt.read_text())
            self.assertNotIn(SECRET, args.receipt.read_text())
            self.assertEqual(list(root.glob("auth-raw-*")), [])
            return code, receipt, commands

    def test_runner_uses_full_modules_locked_dependencies_and_machine(self):
        code, receipt, commands = self.run_fixture()
        self.assertEqual(code, 0)
        self.assertEqual(receipt["status"], "passed")
        self.assertIn(["flutter", "pub", "get", "--enforce-lockfile"], commands)
        self.assertEqual(
            commands[-1],
            [
                "flutter",
                "test",
                "--no-pub",
                "--concurrency=1",
                "--machine",
                *verify.TEST_PATHS,
            ],
        )
        self.assertEqual(len(verify.TEST_PATHS), 8)

    def test_failures_short_circuit_and_preserve_only_enum_and_code(self):
        for stage in ("identity", "clean", "sdk", "pub", "machine"):
            code, receipt, commands = self.run_fixture(stage)
            self.assertNotEqual(code, 0)
            self.assertEqual(receipt["status"], "failed")
            if stage != "machine":
                self.assertFalse(any(c[:2] == ["flutter", "test"] for c in commands))
        code, receipt, _ = self.run_fixture(
            "machine", subprocess.TimeoutExpired("secret", 1)
        )
        self.assertEqual(code, 1)
        self.assertEqual(receipt["outcome"], "timeout")

    def test_linux_auth_job_is_sha_based_and_receipt_only(self):
        import re
        text = (ROOT / ".github/workflows/dial-gate.yml").read_text()
        match = re.search(
            r"(?m)^  linux_auth_storage:\n(.*?)(?=^  [A-Za-z0-9_]+:\n|\Z)",
            text,
            re.S,
        )
        self.assertIsNotNone(match)
        job = match.group(0)
        self.assertIn("inputs.run_pr_checks", job)
        self.assertIn("ubuntu-24.04", job)
        self.assertIn("inputs.candidate_ref || inputs.source_sha", job)
        self.assertNotIn("refs/pull/", job)
        self.assertIn("auth-storage-receipt/receipt.json", job)
        self.assertEqual(job.count("actions/upload-artifact@"), 1)
        for forbidden in (
            "productionBuild",
            "pull_request_target",
            "tee ",
            ".log",
            "curl ",
            "release.sh",
        ):
            self.assertNotIn(forbidden, job)

    def test_pinned_flutter_started_process_array_never_exports_uri(self):
        for uri in (None, SECRET):
            data = events()
            data.insert(
                1, [{"event": "test.startedProcess", "params": {"vmServiceUri": uri}}]
            )
            actual = self.summary(data)
            self.assertEqual(actual["status"], "passed")
            self.assertTrue(actual["machine_complete"])
            self.assertEqual(
                actual["protocol_diagnostics"]["started_process_events"], 1
            )
            self.assertNotIn(SECRET, json.dumps(actual))
        valid = {"event": "test.startedProcess", "params": {"vmServiceUri": None}}
        for bad in (
            [],
            [valid, valid],
            [{**valid, "event": SECRET}],
            [{**valid, "extra": SECRET}],
            [
                {
                    "event": "test.startedProcess",
                    "params": {"vmServiceUri": {"secret": SECRET}},
                }
            ],
            [
                {
                    "event": "test.startedProcess",
                    "params": {"vmServiceUri": None, "extra": SECRET},
                }
            ],
        ):
            data = events()
            data.insert(1, bad)
            actual = self.summary(data)
            self.assertEqual(actual["status"], "failed")
            self.assertEqual(actual["protocol_diagnostics"]["invalid_event_arrays"], 1)
            self.assertNotIn(SECRET, json.dumps(actual))

    def test_hidden_loading_completion_is_not_a_pass_and_must_complete(self):
        data = events()
        start = {
            "type": "testStart",
            "test": {"id": 9, "suiteID": 0, "name": "loading fixture"},
        }
        end = {
            "type": "testDone",
            "testID": 9,
            "result": "success",
            "hidden": True,
            "skipped": False,
        }
        data[1:1] = [start, end]
        actual = self.summary(data)
        self.assertEqual(actual["status"], "passed")
        self.assertEqual(actual["counts"]["passed"], 2)
        self.assertEqual(actual["protocol_diagnostics"]["hidden_completions"], 1)
        data.remove(end)
        actual = self.summary(data)
        self.assertEqual(actual["status"], "failed")
        self.assertEqual(actual["protocol_diagnostics"]["missing_completions"], 1)

    def test_protocol_diagnostics_are_closed_counts_and_remain_fail_closed(self):
        variants = []
        duplicate = events()
        duplicate.insert(2, duplicate[1])
        variants.append((duplicate, "duplicate_starts"))
        duplicate = events()
        duplicate.insert(3, duplicate[2])
        variants.append((duplicate, "duplicate_completions"))
        missing = events()
        del missing[1]
        variants.append((missing, "completion_without_start"))
        after = events()
        after.append({"type": "print", "message": SECRET})
        variants.append((after, "events_after_done"))
        for data, reason in variants:
            actual = self.summary(data)
            self.assertEqual(actual["status"], "failed")
            self.assertGreater(actual["protocol_diagnostics"][reason], 0)
            self.assertNotIn(SECRET, json.dumps(actual))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "machine"
            log.write_text(
                SECRET + "\n" + "".join(json.dumps(e) + "\n" for e in events())
            )
            actual = verify.summarize(log, root, 0)
            self.assertEqual(actual["status"], "failed")
            self.assertEqual(actual["protocol_diagnostics"]["non_json_lines"], 1)
            self.assertNotIn(SECRET, json.dumps(actual))

    def test_two_macos_skips_require_both_exact_file_and_case(self):
        self.assertEqual(len(verify.MAC_SKIP_CASES), 2)
        for path, name in verify.MAC_SKIP_CASES:
            for actual_path, actual_name, expected in (
                (path, name, "passed"),
                (verify.TEST_PATHS[0], name, "failed"),
                (path, name + " extra", "failed"),
                (path, SECRET, "failed"),
            ):
                data = events()
                data[-1:-1] = [
                    {"type": "suite", "suite": {"id": 5, "path": actual_path}},
                    {
                        "type": "testStart",
                        "test": {"id": 9, "suiteID": 5, "name": actual_name},
                    },
                    {
                        "type": "testDone",
                        "testID": 9,
                        "result": "success",
                        "hidden": False,
                        "skipped": True,
                    },
                ]
                actual = self.summary(data)
                self.assertEqual(actual["status"], expected)
                self.assertEqual(actual["counts"]["passed"], 2)
                self.assertEqual(actual["counts"]["skipped"], 1)
                self.assertNotIn(SECRET, json.dumps(actual))
