from __future__ import annotations

import base64
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/update-manifest-v1.json"
OLDER_FIXTURE = ROOT / "tests/fixtures/update-manifest-v1.2.133.json"
SPEC = importlib.util.spec_from_file_location(
    "verify_update_manifest", ROOT / "scripts/verify-update-manifest.py"
)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class UpdateManifestVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = FIXTURE.read_bytes()
        self.manifest = json.loads(self.raw)

    def assert_untrusted(self, raw: bytes) -> None:
        with self.assertRaises(verifier.ManifestError):
            verifier.verify(raw)

    def test_real_dart_release_fixture_is_valid(self) -> None:
        """A production manifest signed by the YueLink release plane is the contract."""
        verified = verifier.verify(self.raw)
        self.assertEqual(verified["version"], "1.2.134")

    def test_payload_tamper_is_rejected(self) -> None:
        tampered = dict(self.manifest)
        tampered["version"] = "9.9.9"
        self.assert_untrusted(json.dumps(tampered, ensure_ascii=False).encode())

    def test_signature_tamper_is_rejected(self) -> None:
        tampered = dict(self.manifest)
        signature = bytearray(base64.b64decode(tampered["sig"].removeprefix("ed25519:")))
        signature[0] ^= 0x01
        tampered["sig"] = "ed25519:" + base64.b64encode(signature).decode("ascii")
        with self.assertRaisesRegex(verifier.ManifestError, "signature is invalid"):
            verifier.verify(json.dumps(tampered, ensure_ascii=False).encode())

    def test_valid_signed_older_root_is_rejected_as_replay(self) -> None:
        older = OLDER_FIXTURE.read_bytes()
        # Prove this reaches the monotonic floor rather than failing signature
        # verification: it is an authentic archived production root.
        signed = verifier._verify_signed_manifest(older)
        self.assertEqual(signed["version"], "1.2.133")
        with self.assertRaisesRegex(verifier.ManifestError, "below the reviewed minimum"):
            verifier.verify(older)

    def test_newer_version_with_older_publication_time_is_rejected(self) -> None:
        # Exercise the independent timestamp half of the floor directly. A
        # higher version cannot legitimize an older source/publication epoch.
        candidate = dict(self.manifest)
        floor = dict(self.manifest)
        floor["version"] = "1.2.132"
        floor["publishedAt"] = "2026-08-15T04:34:31Z"
        with self.assertRaisesRegex(verifier.ManifestError, "publishedAt is below"):
            verifier._enforce_minimum(candidate, floor)

    def test_duplicate_top_level_key_is_rejected(self) -> None:
        duplicated = self.raw.replace(b"{", b'{"schemaVersion":1,', 1)
        self.assert_untrusted(duplicated)

    def test_duplicate_nested_key_is_rejected(self) -> None:
        needle = b'"android-arm64-v8a": {'
        duplicated = self.raw.replace(
            needle, needle + b'"url":"https://invalid.example/duplicate",', 1
        )
        self.assert_untrusted(duplicated)

    def test_unknown_top_level_key_is_rejected(self) -> None:
        tampered = dict(self.manifest)
        tampered["unreviewedTrustHint"] = True
        self.assert_untrusted(json.dumps(tampered, ensure_ascii=False).encode())

    def test_wrong_key_id_is_rejected(self) -> None:
        tampered = dict(self.manifest)
        tampered["keyId"] = "network-discovered-key"
        self.assert_untrusted(json.dumps(tampered, ensure_ascii=False).encode())

    def test_noncanonical_wire_format_verifies_same_canonical_payload(self) -> None:
        reordered = dict(reversed(list(self.manifest.items())))
        reformatted = json.dumps(
            reordered, ensure_ascii=False, indent=7, separators=(",", ": ")
        ).encode()
        self.assertNotEqual(reformatted, self.raw)
        self.assertEqual(verifier.verify(reformatted)["version"], "1.2.134")


if __name__ == "__main__":
    unittest.main()
