#!/usr/bin/env python3
"""Offline behavioral tests; also loaded by the repository policy suite."""

import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "reconcile_pin", ROOT / "scripts/reconcile-yuelink-pin.py"
)
pin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pin)


class PinPlanTests(unittest.TestCase):
    def setUp(self):
        self.raw = (ROOT / "tests/fixtures/update-manifest-v1.json").read_bytes()
        self.manifest = json.loads(self.raw)
        self.version = self.manifest["version"]
        self.source = self.manifest["sourceCommit"]
        # Keep signed fixture tests reproducible after its normal expiry.
        self.clock = mock.patch.object(pin, "datetime", wraps=datetime)
        clock = self.clock.start()
        clock.now.return_value = datetime.fromisoformat(self.manifest["publishedAt"])
        self.addCleanup(self.clock.stop)

    def test_verified_exact_bytes_produce_native_command(self):
        result = pin.plan(self.raw, self.version, self.source)
        self.assertEqual(result["status"], "PLAN_ONLY")
        self.assertEqual(
            result["yuelink"],
            {
                "version": self.version,
                "source_commit": self.source,
                "update_manifest_sha256": hashlib.sha256(self.raw).hexdigest(),
            },
        )
        self.assertEqual(
            result["command_from_yueto_root"].split(),
            [
                "python3",
                "scripts/release-yaml.py",
                "write-yuelink",
                "--version",
                self.version,
                "--source-commit",
                self.source,
                "--update-manifest-sha256",
                hashlib.sha256(self.raw).hexdigest(),
            ],
        )

    def test_byte_hash_is_not_canonical_json_hash(self):
        alternate = json.dumps(self.manifest).encode()
        result = pin.plan(alternate, self.version, self.source)
        self.assertEqual(
            result["yuelink"]["update_manifest_sha256"],
            hashlib.sha256(alternate).hexdigest(),
        )
        self.assertNotEqual(
            hashlib.sha256(self.raw).hexdigest(),
            result["yuelink"]["update_manifest_sha256"],
        )

    def test_expected_identity_must_match(self):
        for version, source in [
            ("0.0.0", self.source),
            (self.version, "0" * 40),
            (self.version, "$(false)"),
        ]:
            with (
                self.subTest(version=version, source=source),
                self.assertRaises(ValueError),
            ):
                pin.plan(self.raw, version, source)

    def test_tampering_and_malformed_input_fail(self):
        altered = dict(self.manifest, sourceCommit="0" * 40)
        for raw in [
            b"{}",
            b"not json",
            json.dumps(altered).encode(),
            b"x" * (pin.MAX_BYTES + 1),
        ]:
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                pin.plan(raw, self.version, self.source)

    def test_expired_signed_manifest_fails(self):
        pin.datetime.now.return_value = datetime.fromisoformat(
            self.manifest["expiresAt"]
        )
        with self.assertRaisesRegex(ValueError, "expired"):
            pin.plan(self.raw, self.version, self.source)

    def test_future_signed_manifest_fails(self):
        pin.datetime.now.return_value = datetime(2020, 1, 1, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "future"):
            pin.plan(self.raw, self.version, self.source)

    def test_replay_floor_failure_propagates_without_plan(self):
        with (
            mock.patch.object(pin.verifier, "verify", side_effect=ValueError("replay")),
            self.assertRaisesRegex(ValueError, "replay"),
        ):
            pin.plan(self.raw, self.version, self.source)

    def test_missing_source_is_not_inferred(self):
        older = dict(self.manifest)
        older.pop("sourceCommit")
        with (
            mock.patch.object(pin.verifier, "verify", return_value=older),
            self.assertRaisesRegex(ValueError, "source"),
        ):
            pin.plan(self.raw, self.version, self.source)

    def test_cli_success_is_read_only_and_failure_has_no_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stable.json"
            path.write_bytes(self.raw)
            for version, code in [(self.version, 0), ("0.0.0", 1)]:
                stdout = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                    result = pin.main(
                        [
                            str(path),
                            "--version",
                            version,
                            "--source-commit",
                            self.source,
                        ]
                    )
                self.assertEqual(result, code)
                if code:
                    self.assertEqual(stdout.getvalue(), "")
                else:
                    self.assertEqual(
                        json.loads(stdout.getvalue())["status"], "PLAN_ONLY"
                    )
                self.assertEqual(path.read_bytes(), self.raw)
                self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_missing_file_fails_without_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                code = pin.main(
                    [
                        str(Path(tmp) / "absent"),
                        "--version",
                        self.version,
                        "--source-commit",
                        self.source,
                    ]
                )
            self.assertEqual(code, 1)
            self.assertEqual(stdout.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
