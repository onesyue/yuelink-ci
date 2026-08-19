from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/verify-release-page-health.py"
WORKFLOW = ROOT / ".github/workflows/manifest-health.yml"
REAL_FIXTURE = ROOT / "tests/fixtures/yue-to-download-health.json"
REAL_PUBLIC_KEY = ROOT / "security/yue-to-download-health-ed25519.pem"
SPEC = importlib.util.spec_from_file_location("release_page_health", SCRIPT)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def _run(command: list[str], *, input_bytes: bytes | None = None) -> bytes:
    completed = subprocess.run(
        command,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr.decode("utf-8", "replace"))
    return completed.stdout


class ReleasePageHealthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        for executable in ("jq", "openssl"):
            if shutil.which(executable) is None:
                raise AssertionError(f"required test executable missing: {executable}")
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="release-health-test-")
        cls.temp = Path(cls.temp_dir.name)
        cls.private_key = cls.temp / "private.pem"
        cls.public_key = cls.temp / "public.pem"
        _run(
            [
                "openssl",
                "genpkey",
                "-algorithm",
                "ED25519",
                "-out",
                str(cls.private_key),
            ]
        )
        public_pem = _run(
            [
                "openssl",
                "pkey",
                "-in",
                str(cls.private_key),
                "-pubout",
            ]
        )
        cls.public_key.write_bytes(public_pem)
        cls.now = 1_787_184_000
        cls.payload = {
            "schemaVersion": 1,
            "probeId": verifier.PROBE_ID,
            "url": verifier.RELEASE_URL,
            "finalUrl": verifier.RELEASE_URL,
            "publicUrl": verifier.PUBLIC_URL,
            "checkedAt": "2026-08-20T00:00:00Z",
            "checkedAtUnix": cls.now,
            "httpStatus": 200,
            "bytes": 21_873,
            "latencyMs": 850,
            "contentSha256": "a" * 64,
            "errorCode": "none",
            "ok": True,
            "checks": {name: True for name in verifier.CHECK_KEYS},
        }

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _signed_envelope(self, payload: dict | None = None) -> dict:
        payload = copy.deepcopy(payload if payload is not None else self.payload)
        canonical = _run(
            ["jq", "-cjS", "."],
            input_bytes=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        payload_path = self.temp / "payload-to-sign.json"
        payload_path.write_bytes(canonical)
        signature = _run(
            [
                "openssl",
                "pkeyutl",
                "-sign",
                "-inkey",
                str(self.private_key),
                "-rawin",
                "-in",
                str(payload_path),
            ]
        )
        fingerprint = verifier._public_key_fingerprint(self.public_key)
        return {
            "alg": verifier.ALGORITHM,
            "canonicalization": verifier.CANONICALIZATION,
            "keyId": f"ed25519:sha256:{fingerprint}",
            "payload": payload,
            "payloadSha256": hashlib.sha256(canonical).hexdigest(),
            "signature": base64.b64encode(signature).decode("ascii"),
        }

    def _write(self, envelope: dict, name: str = "envelope.json") -> Path:
        path = self.temp / name
        path.write_text(
            json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        return path

    def _verify(self, envelope: dict, *, now: int | None = None) -> dict:
        dynamic_key_id = envelope.get("keyId")
        with mock.patch.object(verifier, "TRUSTED_KEY_ID", dynamic_key_id):
            return verifier.verify(
                self._write(envelope),
                public_key=self.public_key,
                now_unix=self.now if now is None else now,
            )

    def _assert_rejected(self, envelope: dict, message: str, *, now: int | None = None) -> None:
        with self.assertRaisesRegex(verifier.ReleaseHealthError, message):
            self._verify(envelope, now=now)

    def test_valid_dynamic_ed25519_envelope_passes(self) -> None:
        verified = self._verify(self._signed_envelope())
        self.assertEqual(verified, self.payload)

    def test_real_deployed_fixture_passes_with_pinned_key(self) -> None:
        self.assertTrue(REAL_FIXTURE.is_file(), "real signed health fixture must be committed")
        self.assertTrue(REAL_PUBLIC_KEY.is_file(), "real health public key must be committed")
        raw = json.loads(REAL_FIXTURE.read_text(encoding="utf-8"))
        checked = raw["payload"]["checkedAtUnix"]
        verified = verifier.verify(
            REAL_FIXTURE, public_key=REAL_PUBLIC_KEY, now_unix=checked
        )
        self.assertEqual(verified["publicUrl"], verifier.PUBLIC_URL)
        self.assertEqual(raw["keyId"], verifier.TRUSTED_KEY_ID)
        self.assertEqual(
            verifier._public_key_fingerprint(REAL_PUBLIC_KEY),
            verifier.TRUSTED_KEY_ID.removeprefix("ed25519:sha256:"),
        )

    def test_signature_mutation_is_rejected(self) -> None:
        envelope = self._signed_envelope()
        signature = bytearray(base64.b64decode(envelope["signature"]))
        signature[0] ^= 1
        envelope["signature"] = base64.b64encode(signature).decode("ascii")
        self._assert_rejected(envelope, "signature is invalid")

    def test_payload_hash_mutation_is_rejected_before_signature(self) -> None:
        envelope = self._signed_envelope()
        envelope["payloadSha256"] = "0" * 64
        self._assert_rejected(envelope, "payloadSha256 is invalid")

    def test_replay_and_future_timestamp_are_rejected(self) -> None:
        envelope = self._signed_envelope()
        with self.subTest(case="stale"):
            self._assert_rejected(
                envelope, "is stale", now=self.now + verifier.MAX_AGE_SECONDS + 1
            )
        with self.subTest(case="future"):
            self._assert_rejected(
                envelope,
                "too far in the future",
                now=self.now - verifier.MAX_FUTURE_SKEW_SECONDS - 1,
            )

    def test_url_mutations_are_rejected_even_when_resigned(self) -> None:
        for field in ("url", "finalUrl", "publicUrl"):
            with self.subTest(field=field):
                payload = copy.deepcopy(self.payload)
                payload[field] = "https://evil.example/"
                self._assert_rejected(self._signed_envelope(payload), "URL|publicUrl")

    def test_status_and_failure_mutations_are_rejected_even_when_resigned(self) -> None:
        mutations = (
            ("httpStatus", 503, "HTTP 200"),
            ("ok", False, "reports failure"),
            ("errorCode", "timeout", "errorCode=none"),
        )
        for field, value, message in mutations:
            with self.subTest(field=field):
                payload = copy.deepcopy(self.payload)
                payload[field] = value
                self._assert_rejected(self._signed_envelope(payload), message)

    def test_probe_identity_mutations_are_rejected_even_when_resigned(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["probeId"] = "unreviewed-probe"
        self._assert_rejected(self._signed_envelope(payload), "probe identity")

        envelope = self._signed_envelope()
        envelope["keyId"] = "ed25519:sha256:" + "0" * 64
        dynamic_key_id = self._signed_envelope()["keyId"]
        with mock.patch.object(verifier, "TRUSTED_KEY_ID", dynamic_key_id):
            with self.assertRaisesRegex(
                verifier.ReleaseHealthError, "does not match the pinned key"
            ):
                verifier.verify(
                    self._write(envelope),
                    public_key=self.public_key,
                    now_unix=self.now,
                )

    def test_each_required_identity_check_is_fail_closed(self) -> None:
        for check in verifier.CHECK_KEYS:
            with self.subTest(check=check):
                payload = copy.deepcopy(self.payload)
                payload["checks"][check] = False
                self._assert_rejected(
                    self._signed_envelope(payload), "every reviewed.*must pass"
                )

    def test_timestamp_forms_must_agree(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["checkedAtUnix"] += 1
        self._assert_rejected(self._signed_envelope(payload), "disagree")

    def test_numeric_and_body_identity_bounds_are_fail_closed(self) -> None:
        mutations = (
            ("schemaVersion", 1.0, "schemaVersion must be an integer"),
            ("checkedAtUnix", float(self.now), "checkedAtUnix must be an integer"),
            ("httpStatus", 200.0, "httpStatus must be an integer"),
            ("bytes", verifier.MIN_BODY_BYTES - 1, "body size"),
            ("bytes", verifier.MAX_BODY_BYTES + 1, "body size"),
            ("latencyMs", -1, "latency"),
            ("latencyMs", verifier.MAX_LATENCY_MS + 1, "latency"),
            ("contentSha256", "A" * 64, "canonical lowercase"),
        )
        for field, value, message in mutations:
            with self.subTest(field=field, value=value):
                payload = copy.deepcopy(self.payload)
                payload[field] = value
                self._assert_rejected(self._signed_envelope(payload), message)

    def test_duplicate_and_unknown_fields_are_rejected(self) -> None:
        envelope = self._signed_envelope()
        raw = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
        duplicate = raw.replace(b"{", b'{"alg":"Ed25519",', 1)
        path = self.temp / "duplicate.json"
        path.write_bytes(duplicate)
        with mock.patch.object(verifier, "TRUSTED_KEY_ID", envelope["keyId"]):
            with self.assertRaisesRegex(verifier.ReleaseHealthError, "duplicate JSON key"):
                verifier.verify(path, public_key=self.public_key, now_unix=self.now)

        envelope["unreviewedKey"] = True
        self._assert_rejected(envelope, "keys are not exact")

    def test_algorithm_and_canonicalization_are_pinned(self) -> None:
        for field, value, message in (
            ("alg", "Ed448", "algorithm"),
            ("canonicalization", "json-unsorted-v1", "canonicalization"),
        ):
            with self.subTest(field=field):
                envelope = self._signed_envelope()
                envelope[field] = value
                self._assert_rejected(envelope, message)

    def test_public_key_substitution_is_rejected_by_literal_anchor(self) -> None:
        envelope = self._signed_envelope()
        with self.assertRaisesRegex(verifier.ReleaseHealthError, "reviewed anchor"):
            verifier.verify(
                self._write(envelope),
                public_key=self.public_key,
                now_unix=self.now,
            )

    def assert_workflow_contract(self, workflow: str) -> None:
        self.assertIn("python3 scripts/verify-release-page-health.py", workflow)
        self.assertIn(verifier.PUBLIC_URL, workflow)
        self.assertIn("--max-filesize 65536", workflow)
        self.assertNotIn("--headless=new", workflow)
        self.assertNotIn("--dump-dom", workflow)
        self.assertNotIn("setup-chrome", workflow)
        self.assertIn("done < <(jq -r '.platforms[].url' \"$tmp\")", workflow)
        self.assertIn(
            "--range 0-0 --output /dev/null --write-out '%{http_code}' \"$url\"",
            workflow,
        )
        self.assertIn("release asset probe: $url HTTP $asset_code", workflow)
        asset_loop = workflow.split("while IFS= read -r url; do", 1)[1].split(
            "done < <(jq -r '.platforms[].url'", 1
        )[0]
        self.assertEqual(asset_loop.count("curl "), 1)
        self.assertEqual(asset_loop.count("--range 0-0"), 1)

    def test_workflow_uses_signed_probe_and_bounded_asset_gets(self) -> None:
        self.assert_workflow_contract(WORKFLOW.read_text(encoding="utf-8"))

    def test_workflow_contract_rejects_verifier_and_range_mutations(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "python3 scripts/verify-release-page-health.py",
            "--max-filesize 65536",
            "--range 0-0 --output /dev/null --write-out '%{http_code}' \"$url\"",
        ):
            with self.subTest(required=required):
                mutated = workflow.replace(required, "", 1)
                self.assertNotEqual(mutated, workflow)
                with self.assertRaises(AssertionError):
                    self.assert_workflow_contract(mutated)

        unbounded_extra = workflow.replace(
            "while IFS= read -r url; do",
            'while IFS= read -r url; do\n            curl --output /dev/null "$url"',
            1,
        )
        self.assertNotEqual(unbounded_extra, workflow)
        with self.assertRaises(AssertionError):
            self.assert_workflow_contract(unbounded_extra)


if __name__ == "__main__":
    unittest.main()
