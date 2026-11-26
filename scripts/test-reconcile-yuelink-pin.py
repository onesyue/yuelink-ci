#!/usr/bin/env python3
"""Offline behavioral tests; also loaded by the repository policy suite."""

import hashlib
import importlib.util
import io
import json
import subprocess
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


def _run(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def make_checkout(
    root: Path,
    version: str = "1.2.3",
    *,
    lightweight: bool = False,
    dirty: bool = False,
    extra_commit: bool = False,
    tag_message: str = "release\n",
) -> tuple[Path, str]:
    """Build a local YueLink-shaped checkout. Returns (repo, tagged commit)."""
    repo = root / "yuelink"
    repo.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "master"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    _run(repo, "config", "user.email", "lane@example.com")
    _run(repo, "config", "user.name", "Lane Test")
    _run(repo, "config", "commit.gpgsign", "false")
    _run(repo, "config", "core.hooksPath", "/dev/null")
    (repo / "pubspec.yaml").write_text(
        f"name: yuelink\nversion: {version}+7\n", encoding="utf-8"
    )
    _run(repo, "add", "pubspec.yaml")
    _run(repo, "commit", "-q", "-m", "release")
    tagged = "HEAD"
    if extra_commit:
        (repo / "README").write_text("later\n", encoding="utf-8")
        _run(repo, "add", "README")
        _run(repo, "commit", "-q", "-m", "later")
        tagged = "HEAD~1"
    if lightweight:
        _run(repo, "tag", f"v{version}", tagged)
    else:
        _run(repo, "tag", "-a", f"v{version}", "-m", tag_message, tagged)
    if dirty:
        (repo / "pubspec.yaml").write_text(
            "name: yuelink\nversion: 9.9.9\n", encoding="utf-8"
        )
    sha = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", f"v{version}^{{commit}}"],
        text=True,
    ).strip()
    return repo, sha


class PinPlanTests(unittest.TestCase):
    def setUp(self):
        self.raw = (ROOT / "tests/fixtures/update-manifest-v1.json").read_bytes()
        self.manifest = json.loads(self.raw)
        self.version = self.manifest["version"]
        self.source = self.manifest["sourceCommit"]
        self.checkout = Path("/derived-release-checkout")
        # Keep signed fixture tests reproducible after its normal expiry.
        self.clock = mock.patch.object(pin, "datetime", wraps=datetime)
        clock = self.clock.start()
        clock.now.return_value = datetime.fromisoformat(self.manifest["publishedAt"])
        self.addCleanup(self.clock.stop)
        self.identity = mock.patch.object(
            pin,
            "derive_release_facts",
            return_value=(self.version, self.source),
        )
        self.identity.start()
        self.addCleanup(self.identity.stop)

    def test_verified_exact_bytes_produce_native_command(self):
        result = pin.plan(self.raw, self.checkout)
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
        result = pin.plan(alternate, self.checkout)
        self.assertEqual(
            result["yuelink"]["update_manifest_sha256"],
            hashlib.sha256(alternate).hexdigest(),
        )
        self.assertNotEqual(
            hashlib.sha256(self.raw).hexdigest(),
            result["yuelink"]["update_manifest_sha256"],
        )

    def test_expected_identity_must_match(self):
        cases = [
            {"version": "0.0.0"},
            {"source_commit": "0" * 40},
            {"source_commit": "$(false)"},
            {"update_manifest_sha256": "0" * 64},
        ]
        for kwargs in cases:
            with (
                self.subTest(**kwargs),
                self.assertRaisesRegex(ValueError, "drifts"),
            ):
                pin.plan(self.raw, self.checkout, **kwargs)

    def test_tampering_and_malformed_input_fail(self):
        altered = dict(self.manifest, sourceCommit="0" * 40)
        for raw in [
            b"{}",
            b"not json",
            json.dumps(altered).encode(),
            b"x" * (pin.MAX_BYTES + 1),
        ]:
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                pin.plan(raw, self.checkout)

    def test_expired_signed_manifest_fails(self):
        pin.datetime.now.return_value = datetime.fromisoformat(
            self.manifest["expiresAt"]
        )
        with self.assertRaisesRegex(ValueError, "expired"):
            pin.plan(self.raw, self.checkout)

    def test_future_signed_manifest_fails(self):
        pin.datetime.now.return_value = datetime(2020, 1, 1, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "future"):
            pin.plan(self.raw, self.checkout)

    def test_replay_floor_failure_propagates_without_plan(self):
        with (
            mock.patch.object(pin.verifier, "verify", side_effect=ValueError("replay")),
            self.assertRaisesRegex(ValueError, "replay"),
        ):
            pin.plan(self.raw, self.checkout)

    def test_missing_source_is_not_inferred(self):
        older = dict(self.manifest)
        older.pop("sourceCommit")
        with (
            mock.patch.object(pin.verifier, "verify", return_value=older),
            self.assertRaisesRegex(ValueError, "source"),
        ):
            pin.plan(self.raw, self.checkout)

    def test_cli_success_is_read_only_and_failure_has_no_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stable.json"
            path.write_bytes(self.raw)
            cases = [
                ([str(path), "--checkout", str(self.checkout)], 0),
                (
                    [
                        str(path),
                        "--checkout",
                        str(self.checkout),
                        "--version",
                        "0.0.0",
                        "--source-commit",
                        self.source,
                    ],
                    1,
                ),
            ]
            for argv, code in cases:
                stdout = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                    result = pin.main(argv)
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
                        "--checkout",
                        str(self.checkout),
                    ]
                )
            self.assertEqual(code, 1)
            self.assertEqual(stdout.getvalue(), "")

    def test_checkout_derives_three_facts_without_hand_flags(self):
        self.identity.stop()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                repo, sha = make_checkout(Path(tmp))
                raw = b'{"schema":"derived"}\n'
                manifest = {
                    "version": "1.2.3",
                    "sourceCommit": sha,
                    "expiresAt": "2099-01-01T00:00:00Z",
                    "publishedAt": self.manifest["publishedAt"],
                }
                digest = hashlib.sha256(raw).hexdigest()
                with mock.patch.object(pin.verifier, "verify", return_value=manifest):
                    result = pin.plan(raw, repo)
                    matched = pin.plan(
                        raw,
                        repo,
                        version="1.2.3",
                        source_commit=sha,
                        update_manifest_sha256=digest,
                    )
                self.assertEqual(result["status"], "PLAN_ONLY")
                self.assertEqual(
                    result["yuelink"],
                    {
                        "version": "1.2.3",
                        "source_commit": sha,
                        "update_manifest_sha256": digest,
                    },
                )
                self.assertEqual(matched["yuelink"], result["yuelink"])
        finally:
            self.identity.start()

    def test_caller_supplied_copy_of_derived_fact_is_refused(self):
        self.identity.stop()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                repo, sha = make_checkout(Path(tmp))
                raw = b'{"schema":"derived"}\n'
                manifest = {
                    "version": "1.2.3",
                    "sourceCommit": sha,
                    "expiresAt": "2099-01-01T00:00:00Z",
                    "publishedAt": self.manifest["publishedAt"],
                }
                cases = [
                    {"version": "9.9.9"},
                    {"source_commit": "0" * 40},
                    {"update_manifest_sha256": "ab" * 32},
                ]
                for kwargs in cases:
                    with (
                        self.subTest(**kwargs),
                        mock.patch.object(
                            pin.verifier, "verify", return_value=manifest
                        ),
                        self.assertRaisesRegex(ValueError, "drifts"),
                    ):
                        pin.plan(raw, repo, **kwargs)
        finally:
            self.identity.start()

    def test_signed_fixture_must_match_derived_checkout(self):
        self.identity.stop()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                repo, _sha = make_checkout(Path(tmp), version="1.2.3")
                with self.assertRaisesRegex(ValueError, "version"):
                    pin.plan(self.raw, repo)
        finally:
            self.identity.start()

    def test_lightweight_tag_dirty_pubspec_and_moved_head_are_refused(self):
        self.identity.stop()
        try:
            manifest_for = {
                "expiresAt": "2099-01-01T00:00:00Z",
                "publishedAt": self.manifest["publishedAt"],
            }
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                light, light_sha = make_checkout(root / "light", lightweight=True)
                dirty, dirty_sha = make_checkout(root / "dirty", dirty=True)
                moved, moved_sha = make_checkout(root / "moved", extra_commit=True)
                attested, attested_sha = make_checkout(root / "attested")
                _run(attested, "tag", "-d", "v1.2.3")
                _run(
                    attested,
                    "tag",
                    "-a",
                    "v1.2.3",
                    "-m",
                    "note\n\nATTESTED_SOURCE_COMMIT=" + ("0" * 40) + "\n",
                    attested_sha,
                )
                cases = [
                    (light, light_sha, "annotated"),
                    (dirty, dirty_sha, "drifts"),
                    (moved, moved_sha, "drifts"),
                    (attested, attested_sha, "drifts"),
                ]
                for repo, sha, pattern in cases:
                    manifest = {
                        "version": "1.2.3",
                        "sourceCommit": sha,
                        **manifest_for,
                    }
                    with (
                        self.subTest(repo=repo.name),
                        mock.patch.object(
                            pin.verifier, "verify", return_value=manifest
                        ),
                        self.assertRaisesRegex(ValueError, pattern),
                    ):
                        pin.plan(b"{}", repo)
        finally:
            self.identity.start()


if __name__ == "__main__":
    unittest.main()
