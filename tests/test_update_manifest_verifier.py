from __future__ import annotations

import base64
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/update-manifest-v1.json"
# 归档的旧签名根，用来证明「签名合法但版本更旧」会被单调地板拒绝。
# 指向**最近**的那一个而不是最老的：1.3.5 的重放比更早版本的重放现实得多
# （它就是上一版真正在 CDN 上服役过的根）。更早的那些仍留在仓里。
OLDER_FIXTURE = ROOT / "tests/fixtures/update-manifest-v1.3.5.json"
SPEC = importlib.util.spec_from_file_location(
    "verify_update_manifest", ROOT / "scripts/verify-update-manifest.py"
)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def _later_than(iso: str) -> str:
    """比给定 ISO 时间晚一天 —— 供「更高版本不能洗白更早发布时间」那条用。"""
    from datetime import datetime, timedelta, timezone

    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    return (dt + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        self.assertEqual(verified["version"], self.manifest["version"])

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
        # 期望值从夹具派生，不写死版本号 —— 换夹具时不必再改这一行，
        # 也就不会出现「改了夹具、忘了改断言」的假红/假绿。
        self.assertEqual(signed["version"], json.loads(older)["version"])
        # 但必须真的比当前地板旧，否则这条测试会在某次换夹具后悄悄变成空转。
        self.assertLess(
            tuple(int(x) for x in signed["version"].split(".")),
            tuple(int(x) for x in self.manifest["version"].split(".")),
        )
        with self.assertRaisesRegex(verifier.ManifestError, "below the reviewed minimum"):
            verifier.verify(older)

    def test_newer_version_with_older_publication_time_is_rejected(self) -> None:
        # Exercise the independent timestamp half of the floor directly. A
        # higher version cannot legitimize an older source/publication epoch.
        candidate = dict(self.manifest)
        floor = dict(self.manifest)
        # 🚨 两个值都从夹具**派生**，不写死。写死会随发版腐烂：原先钉的
        # publishedAt 是 2026-08-16，等夹具换成更新的根之后，候选反而比"地板"
        # 还新，`publishedAt is below` 永远不会触发 —— 断言看着在，其实空转。
        floor["version"] = "0.0.1"                      # 版本一定更低
        floor["publishedAt"] = _later_than(self.manifest["publishedAt"])  # 时间一定更晚
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
        self.assertEqual(
            verifier.verify(reformatted)["version"], self.manifest["version"]
        )


if __name__ == "__main__":
    unittest.main()
