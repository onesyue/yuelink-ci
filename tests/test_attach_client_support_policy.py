"""Exercise the offline builder CLI with the reviewed policy and signed fixtures."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ATTACH = ROOT / "scripts/attach-client-support-policy.py"
SOURCE = ROOT / "policy/client-support-policy.json"
REVIEWED = ROOT / "tests/fixtures/update-manifest-v1.json"
OLDER = ROOT / "tests/fixtures/update-manifest-v1.3.173.json"


class AttachClientSupportPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.candidate = directory / "candidate.json"
        self.source = directory / "policy.json"
        self.previous = directory / "previous.json"
        self.output = directory / "attached.json"
        self.previous.write_bytes(REVIEWED.read_bytes())
        self.source.write_bytes(SOURCE.read_bytes())
        self.candidate_value = {
            "version": "99.0.0",
            "publishedAt": "2099-01-01T00:00:00Z",
        }
        self.candidate.write_text(json.dumps(self.candidate_value), encoding="utf-8")

    def run_attach(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(ATTACH),
                "--candidate",
                str(self.candidate),
                "--policy",
                str(self.source),
                "--previous-manifest",
                str(self.previous),
                "--output",
                str(self.output),
            ],
            cwd=self.output.parent,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def assert_rejected(self, reason: str) -> None:
        completed = self.run_attach()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(reason, completed.stderr)
        self.assertFalse(self.output.exists())

    def test_attaches_exact_policy_bytes_without_mutating_inputs(self) -> None:
        inputs = [self.candidate, self.source, self.previous]
        original = [path.read_bytes() for path in inputs]
        completed = self.run_attach()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(self.output.read_bytes()),
            {
                **self.candidate_value,
                "compatibility": {
                    **json.loads(original[1]),
                    "policySourceSha256": hashlib.sha256(original[1]).hexdigest(),
                },
            },
        )
        for path, before in zip(inputs, original, strict=True):
            self.assertTrue(path.read_bytes() == before, f"{path.name} changed")

    def test_rejects_signed_or_already_attached_candidate(self) -> None:
        for existing_field in ({"sig": "test-signature"}, {"compatibility": {}}):
            with self.subTest(field=next(iter(existing_field))):
                self.candidate.write_text(
                    json.dumps({**self.candidate_value, **existing_field}),
                    encoding="utf-8",
                )
                self.assert_rejected("only an unsigned candidate without compatibility")

    def test_rejects_duplicate_keys_in_candidate_and_policy(self) -> None:
        original_candidate = self.candidate.read_bytes()
        self.candidate.write_bytes(b'{"publishedAt": null, "publishedAt": null}')
        self.assert_rejected("duplicate JSON key: publishedAt")

        self.candidate.write_bytes(original_candidate)
        self.source.write_bytes(b'{"policyRevision": 1, "policyRevision": 2}')
        self.assert_rejected("duplicate JSON key: policyRevision")

    def test_rejects_tampered_previous_manifest(self) -> None:
        previous = json.loads(self.previous.read_bytes())
        previous["notes"] += " Synthetic edit after signing."
        self.previous.write_text(json.dumps(previous), encoding="utf-8")
        self.assert_rejected("manifest signature is invalid")

    def test_rejects_signed_previous_manifest_below_reviewed_floor(self) -> None:
        prior = OLDER.read_bytes()
        self.previous.write_bytes(prior)
        # Some older authentic roots predate compatibility entirely.
        reason = (
            "manifest version is below the reviewed minimum"
            if "compatibility" in json.loads(prior)
            else "cannot silently remove existing policy"
        )
        self.assert_rejected(reason)

    def test_rejects_changed_policy_bytes_at_same_revision(self) -> None:
        previous_policy = json.loads(self.previous.read_bytes())["compatibility"]
        previous_digest = previous_policy.pop("policySourceSha256")
        reencoded = json.dumps(previous_policy).encode()
        if hashlib.sha256(reencoded).hexdigest() == previous_digest:
            reencoded += b" "
        self.source.write_bytes(reencoded)
        self.assert_rejected("same policyRevision changed")

    def test_existing_output_is_preserved(self) -> None:
        existing = b"synthetic existing output\n"
        self.output.write_bytes(existing)
        completed = self.run_attach()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("FileExistsError", completed.stderr)
        self.assertEqual(self.output.read_bytes(), existing)
