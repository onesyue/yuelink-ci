from __future__ import annotations

import base64
import importlib.util
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/update-manifest-v1.json"
MANIFEST_HEALTH = ROOT / ".github/workflows/manifest-health.yml"
# 归档的旧签名根，用来证明「签名合法但版本更旧」会被单调地板拒绝。
# 指向**最近**的那一个而不是最老的：1.3.6 的重放比更早版本的重放现实得多
# （它就是上一版真正在 CDN 上服役过的根）。更早的那些仍留在仓里。
OLDER_FIXTURE = ROOT / "tests/fixtures/update-manifest-v1.3.6.json"
SPEC = importlib.util.spec_from_file_location(
    "verify_update_manifest", ROOT / "scripts/verify-update-manifest.py"
)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def _later_than(iso: str) -> str:
    """比给定 ISO 时间晚一天 —— 供「更高版本不能洗白更早发布时间」那条用。"""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    return (dt + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _days_after(iso: str, days: int) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    return (dt + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        """未登记的顶层字段必须被 **schema 门** 拒掉。

        🚨 2026-08-19 变异测试发现：这条原先只断言 `assert_untrusted`，而往已签名的
        清单里加任何字段都会让**签名**先对不上——于是把 schema 门整个短路掉
        （`unknown = set()`），这条测试**照样全绿**。也就是说「未知顶层字段一律拒绝」
        这个安全属性此前从未被真正测到，它一直是被签名检查顺带挡住的。

        判据因此必须落在异常**文案**上：确认是 schema 门说的话，不是签名门说的。
        """
        tampered = dict(self.manifest)
        tampered["unreviewedTrustHint"] = True
        raw = json.dumps(tampered, ensure_ascii=False).encode()
        with self.assertRaises(verifier.ManifestError) as caught:
            verifier.verify(raw)
        self.assertIn("unknown top-level", str(caught.exception))

    def test_allowlisted_optional_key_passes_the_schema_gate(self) -> None:
        """`notesEn`（英文发版说明）必须能过顶层 schema 这一关。

        判据刻意做成「**哪一道门拦下的**」而不是「有没有报错」：往已签名的清单里加
        任何字段都会让签名对不上，所以「加了 notesEn 之后 verify() 抛异常」这个观测
        对「schema 是否接受它」**完全没有信息量**——白名单删掉它，这个观测一模一样。
        所以断言必须落在异常**文案**上：被签名门拦下 = schema 放行了。
        """
        with_optional = dict(self.manifest)
        with_optional["notesEn"] = "- English release notes\n"
        raw = json.dumps(with_optional, ensure_ascii=False).encode()
        with self.assertRaises(verifier.ManifestError) as caught:
            verifier.verify(raw)
        self.assertNotIn("unknown top-level", str(caught.exception))
        self.assertIn("signature", str(caught.exception))

    def test_optional_key_absence_is_fine(self) -> None:
        """可选键缺席不算错——上一版真实签名根里没有 notesEn。"""
        raw = OLDER_FIXTURE.read_bytes()
        manifest = json.loads(raw)
        self.assertNotIn("notesEn", manifest)
        self.assertEqual(
            verifier._verify_signed_manifest(raw)["version"], manifest["version"]
        )

    def test_required_key_removal_is_rejected(self) -> None:
        """放宽成「必需子集 + 白名单」之后，必需键少一个仍然必须拒。"""
        stripped = dict(self.manifest)
        del stripped["releaseUrl"]
        raw = json.dumps(stripped, ensure_ascii=False).encode()
        with self.assertRaises(verifier.ManifestError) as caught:
            verifier.verify(raw)
        self.assertIn("missing required keys", str(caught.exception))

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

    def assert_policy_rejects(self, message: str, mutate) -> None:
        candidate = json.loads(self.raw)
        mutate(candidate)
        with self.assertRaisesRegex(verifier.ManifestError, message):
            verifier._validate_manifest_policy(candidate)

    def test_platform_set_is_exact(self) -> None:
        self.assert_policy_rejects(
            "exactly the reviewed 9 targets",
            lambda candidate: candidate["platforms"].pop("ios"),
        )

    def test_platform_record_cannot_gain_an_unreviewed_field(self) -> None:
        self.assert_policy_rejects(
            "only url and sha256",
            lambda candidate: candidate["platforms"]["ios"].update(
                {"installCommand": "curl | sh"}
            ),
        )

    def test_asset_url_host_path_and_extension_are_exact(self) -> None:
        for bad_url in (
            "http://yuetong.app/v1.3.7/YueLink-1.3.7-ios.ipa",
            "https://evil.example/v1.3.7/YueLink-1.3.7-ios.ipa",
            "https://yuetong.app/v1.3.7/YueLink-1.3.7-ios.zip",
            "https://yuetong.app/v1.3.7/YueLink-1.3.7-ios.ipa?next=evil",
            " https://yuetong.app/v1.3.7/YueLink-1.3.7-ios.ipa",
            "https://yuetong.app/v1.3.7/YueLink-1.3.7-ios.ipa\t",
            "https://yuetong.app:bad/v1.3.7/YueLink-1.3.7-ios.ipa",
            "https://[yuetong.app/v1.3.7/YueLink-1.3.7-ios.ipa",
        ):
            with self.subTest(url=bad_url):
                self.assert_policy_rejects(
                    "outside the reviewed CDN path",
                    lambda candidate, url=bad_url: candidate["platforms"]["ios"].update(
                        {"url": url}
                    ),
                )

    def test_sha256_is_canonical_lowercase_hex(self) -> None:
        self.assert_policy_rejects(
            "invalid SHA-256",
            lambda candidate: candidate["platforms"]["ios"].update(
                {"sha256": "A" * 64}
            ),
        )

    def test_release_url_is_pinned(self) -> None:
        self.assert_policy_rejects(
            "reviewed release page",
            lambda candidate: candidate.update({"releaseUrl": "https://evil.example/"}),
        )

    def test_manifest_lifetime_is_bounded(self) -> None:
        self.assert_policy_rejects(
            "lifetime exceeds 45 days",
            lambda candidate: candidate.update(
                {"expiresAt": _days_after(candidate["publishedAt"], 46)}
            ),
        )

    def test_future_publication_is_bounded(self) -> None:
        candidate = json.loads(self.raw)
        published = datetime.fromisoformat(
            candidate["publishedAt"].replace("Z", "+00:00")
        )
        with self.assertRaisesRegex(verifier.ManifestError, "far in the future"):
            verifier._validate_manifest_policy(
                candidate, now=published - timedelta(hours=7)
            )

    def test_verify_wires_the_signed_payload_into_policy_validation(self) -> None:
        candidate = json.loads(self.raw)
        candidate["platforms"]["ios"]["sha256"] = "not-a-digest"
        floor = json.loads(self.raw)
        with mock.patch.object(
            verifier, "_verify_signed_manifest", side_effect=[candidate, floor]
        ):
            with self.assertRaisesRegex(verifier.ManifestError, "invalid SHA-256"):
                verifier.verify(b"candidate", minimum_raw=b"floor")

    def assert_manifest_health_compares_signed_sidecars(self, workflow: str) -> None:
        self.assertIn(
            "done < <(jq -r '.platforms[] | [.url, .sha256] | @tsv' \"$tmp\")",
            workflow,
        )
        self.assertIn('"${url}.sha256"', workflow)
        self.assertIn('if [ "$sidecar" != "$expected" ]; then', workflow)
        self.assertIn("checksum sidecar disagrees with signed root", workflow)

    def test_manifest_health_compares_every_signed_checksum_sidecar(self) -> None:
        workflow = MANIFEST_HEALTH.read_text(encoding="utf-8")
        self.assert_manifest_health_compares_signed_sidecars(workflow)

    def test_manifest_health_rejects_sidecar_comparison_mutation(self) -> None:
        workflow = MANIFEST_HEALTH.read_text(encoding="utf-8")
        mutated = workflow.replace(
            'if [ "$sidecar" != "$expected" ]; then',
            'if false; then',
            1,
        )
        self.assertNotEqual(mutated, workflow)
        with self.assertRaises(AssertionError):
            self.assert_manifest_health_compares_signed_sidecars(mutated)


if __name__ == "__main__":
    unittest.main()
