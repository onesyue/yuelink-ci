"""Canonical force helper. Global hard covers every platform, including iOS."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/apply-update-force.py"
VERIFIER = ROOT / "scripts/verify-update-manifest.py"
CLEARED = {
    "YUELINK_UPDATE_ROLLOUT_SALT",
    "YUELINK_UPDATE_ROLLOUT_PERCENT",
    "YUELINK_UPDATE_FORCE_PLATFORMS",
    "YUELINK_UPDATE_PLATFORM_FORCE",
    "PROMOTE_PERCENT",
    "ROLLOUT_PERCENT",
    "YUELINK_UPDATE_FORCE",
    "YUELINK_UPDATE_FORCE_REASON",
}


def load():
    spec = importlib.util.spec_from_file_location("apply_update_force", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


APPLY = load()


class ApplyUpdateForceTest(unittest.TestCase):
    def manifest(self) -> dict:
        raw = json.loads((ROOT / "tests/fixtures/update-manifest-v1.json").read_text())
        raw.pop("sig", None)
        return raw

    def non_ios(self, manifest: dict) -> list[str]:
        return sorted(name for name in manifest["platforms"] if not str(name).startswith("ios"))

    def test_three_readers_cover_legacy_current_and_ios(self) -> None:
        verifier = APPLY.load_verifier(VERIFIER)
        soft = self.manifest()
        self.assertTrue(APPLY.apply_policy(
            soft, force="soft", platform_force=None, reason=None, platforms=None, verifier=verifier,
        ))
        self.assertEqual(soft["force"], "soft")
        self.assertNotIn("platformForce", soft)
        self.assertEqual(APPLY.legacy_client_tier(soft), "soft")
        self.assertEqual(APPLY.current_client_tier(soft, "ios"), "soft")
        self.assertEqual(APPLY.current_client_tier(soft, "windows-amd64-setup"), "soft")
        verifier._validate_rollout_extension(soft)

        hard = self.manifest()
        selected = ",".join(self.non_ios(hard))
        self.assertTrue(APPLY.apply_policy(
            hard, force=None, platform_force="hard", reason="security",
            platforms=selected, verifier=verifier,
        ))
        self.assertEqual(hard["force"], "soft")
        self.assertEqual(hard["platformForce"], "hard")
        self.assertEqual(hard["forcePlatforms"], self.non_ios(hard))
        self.assertNotIn("forceDeadline", hard)
        self.assertEqual(APPLY.legacy_client_tier(hard), "soft")
        self.assertEqual(APPLY.current_client_tier(hard, "ios"), "soft")
        self.assertEqual(APPLY.current_client_tier(hard, "windows-amd64-setup"), "hard")
        verifier._validate_rollout_extension(hard)

        historical = self.manifest()
        historical["force"] = "soft"
        historical["forcePlatforms"] = self.non_ios(historical)
        self.assertEqual(APPLY.current_client_tier(historical, "windows-amd64-setup"), "soft")
        verifier._validate_rollout_extension(historical)

        hole = self.manifest()
        hole["force"] = "hard"
        hole["forceReason"] = "security"
        self.assertEqual(APPLY.legacy_client_tier(hole), "hard")
        self.assertEqual(APPLY.current_client_tier(hole, "ios"), "hard")
        self.assertEqual(APPLY.current_client_tier(hole, "windows-amd64-setup"), "hard")
        self.assertFalse(APPLY.apply_policy(
            hole, force=None, platform_force=None, reason=None, platforms=None,
        ))

    def test_global_hard_validates_and_signs(self) -> None:
        policy = json.loads((ROOT / "policy/update-force.json").read_text())
        self.assertEqual(set(policy), {"version", "force", "forceReason"})
        self.assertEqual(policy["force"], "hard")
        self.assertIn(policy["forceReason"], {"security", "incompatible"})
        self.assertNotIn("forcePlatforms", policy)
        manifest = self.manifest()
        verifier = APPLY.load_verifier(VERIFIER)
        self.assertTrue(APPLY.apply_policy(
            manifest, force=policy["force"], platform_force=None,
            reason=policy["forceReason"], platforms=None, verifier=verifier,
        ))
        self.assertEqual(manifest["force"], "hard")
        self.assertEqual(manifest["forceReason"], policy["forceReason"])
        self.assertNotIn("forcePlatforms", manifest)
        self.assertNotIn("platformForce", manifest)
        self.assertEqual(APPLY.legacy_client_tier(manifest), "hard")
        self.assertEqual(APPLY.current_client_tier(manifest, "ios"), "hard")
        self.assertEqual(APPLY.current_client_tier(manifest, "windows-amd64-setup"), "hard")
        verifier._validate_rollout_extension(manifest)
        verifier._validate_manifest_policy(manifest)
        manifest["keyId"] = verifier.CURRENT_KEY_ID
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "isolated.pem"
            subprocess.run(
                ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)],
                check=True, capture_output=True,
            )
            public = subprocess.check_output(
                ["openssl", "pkey", "-in", str(private), "-pubout", "-outform", "DER"]
            )
            public_b64 = base64.b64encode(public[-32:]).decode()
            payload = Path(directory) / "payload"
            payload.write_bytes(
                verifier.SIGNATURE_DOMAIN + verifier._canonical_json(manifest).encode()
            )
            signature = subprocess.check_output([
                "openssl", "pkeyutl", "-sign", "-inkey", str(private),
                "-rawin", "-in", str(payload),
            ])
            sealed = dict(manifest)
            sealed["sig"] = "ed25519:" + base64.b64encode(signature).decode()
            raw = json.dumps(sealed).encode()
            with mock.patch.object(verifier, "CURRENT_PUBLIC_KEY_B64", public_b64):
                verified = verifier._verify_signed_manifest(raw)
            self.assertEqual(verified["force"], "hard")
            self.assertNotIn("forcePlatforms", verified)
            verifier._validate_rollout_extension(verified)
            tampered = raw.replace(b'"force": "hard"', b'"force": "soft"', 1)
            with mock.patch.object(verifier, "CURRENT_PUBLIC_KEY_B64", public_b64):
                with self.assertRaises(verifier.ManifestError):
                    verifier._verify_signed_manifest(tampered)

    def test_one_selected_platform_leaves_the_others_soft(self) -> None:
        manifest = self.manifest()
        APPLY.apply_policy(
            manifest, force="soft", platform_force="hard", reason="incompatible",
            platforms="windows-amd64-setup",
        )
        self.assertEqual(manifest["forcePlatforms"], ["windows-amd64-setup"])
        self.assertEqual(APPLY.current_client_tier(manifest, "windows-amd64-setup"), "hard")
        self.assertEqual(APPLY.current_client_tier(manifest, "linux-amd64-appimage"), "soft")
        self.assertEqual(APPLY.current_client_tier(manifest, "ios"), "soft")
        self.assertEqual(APPLY.legacy_client_tier(manifest), "soft")

    def test_rejections_do_not_mutate_the_object(self) -> None:
        cases = (
            {"force": "hard", "platform_force": "hard", "reason": "security",
             "platforms": "windows-amd64-setup"},
            {"force": None, "platform_force": "soft", "reason": "security",
             "platforms": "windows-amd64-setup"},
            {"force": None, "platform_force": "hard", "reason": None,
             "platforms": "windows-amd64-setup"},
            {"force": None, "platform_force": "hard", "reason": "security", "platforms": None},
            {"force": None, "platform_force": None, "reason": "security", "platforms": None},
            {"force": None, "platform_force": "hard", "reason": "security", "platforms": "ios"},
            {"force": None, "platform_force": "hard", "reason": "security", "platforms": "android"},
            {"force": None, "platform_force": "hard", "reason": "security",
             "platforms": "windows-amd64-setup,windows-amd64-setup"},
            {"force": None, "platform_force": "hard", "reason": "security",
             "platforms": "windows-amd64-setup,,linux-amd64-appimage"},
            {"force": None, "platform_force": "hard", "reason": "marketing",
             "platforms": "windows-amd64-setup"},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                original = self.manifest()
                before = json.dumps(original, sort_keys=True)
                with self.assertRaises(ValueError):
                    APPLY.apply_policy(original, verifier=APPLY.load_verifier(VERIFIER), **kwargs)
                self.assertEqual(json.dumps(original, sort_keys=True), before)

    def run_cli(self, overrides: dict[str, str], args: list[str] | None = None, payload: dict | None = None):
        env = {key: value for key, value in os.environ.items() if key not in CLEARED}
        env.update(overrides)
        body = self.manifest() if payload is None else payload
        before = json.dumps(body).encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_bytes(before)
            os.chmod(path, 0o640)
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(path), *(args or [])],
                env=env, capture_output=True, text=True, check=False,
            )
            return completed, before, path.read_bytes(), path.stat().st_mode & 0o777

    def test_cli_selected_platform_and_idempotence(self) -> None:
        completed, before, after, mode = self.run_cli({
            "YUELINK_UPDATE_PLATFORM_FORCE": "hard",
            "YUELINK_UPDATE_FORCE_REASON": "security",
            "YUELINK_UPDATE_FORCE_PLATFORMS": "linux-amd64-appimage, windows-amd64-setup",
        })
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(mode, 0o640)
        manifest = json.loads(after)
        self.assertEqual(manifest["force"], "soft")
        self.assertEqual(manifest["platformForce"], "hard")
        self.assertEqual(
            manifest["forcePlatforms"],
            ["linux-amd64-appimage", "windows-amd64-setup"],
        )
        self.assertNotIn("rollout", manifest)
        env = {key: value for key, value in os.environ.items() if key not in CLEARED}
        env.update({
            "YUELINK_UPDATE_PLATFORM_FORCE": "hard",
            "YUELINK_UPDATE_FORCE_REASON": "security",
            "YUELINK_UPDATE_FORCE_PLATFORMS": "windows-amd64-setup,linux-amd64-appimage",
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_bytes(after)
            again = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(path)],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertEqual(path.read_bytes(), after)
        self.assertNotEqual(before, after)

    def test_cli_argument_overrides_environment(self) -> None:
        completed, _before, after, _mode = self.run_cli(
            {
                "YUELINK_UPDATE_PLATFORM_FORCE": "soft",
                "YUELINK_UPDATE_FORCE_REASON": "security",
                "YUELINK_UPDATE_FORCE_PLATFORMS": "ios",
            },
            [
                "--platform-force", "hard",
                "--force-reason", "incompatible",
                "--force-platforms", "macos-universal",
            ],
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        manifest = json.loads(after)
        self.assertEqual(manifest["forceReason"], "incompatible")
        self.assertEqual(manifest["forcePlatforms"], ["macos-universal"])

    def test_cli_rejections_keep_bytes(self) -> None:
        signed = json.loads((ROOT / "tests/fixtures/update-manifest-v1.json").read_text())
        self.assertIn("sig", signed)
        cases = (
            ({"YUELINK_UPDATE_FORCE": "hard", "YUELINK_UPDATE_PLATFORM_FORCE": "hard",
              "YUELINK_UPDATE_FORCE_REASON": "security",
              "YUELINK_UPDATE_FORCE_PLATFORMS": "windows-amd64-setup"}, None),
            ({"YUELINK_UPDATE_ROLLOUT_PERCENT": "10"}, None),
            ({"YUELINK_UPDATE_PLATFORM_FORCE": "hard"}, None),
            ({}, signed),
        )
        for overrides, payload in cases:
            with self.subTest(overrides=overrides, signed=payload is not None):
                completed, before, after, _mode = self.run_cli(overrides, payload=payload)
                self.assertNotEqual(completed.returncode, 0, completed.stdout)
                self.assertEqual(before, after)

    def test_failed_replace_and_write_leave_the_original(self) -> None:
        payload = self.manifest()
        raw = json.dumps(payload).encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_bytes(raw)
            original_replace = APPLY.os.replace

            def fail_replace(*_args, **_kwargs):
                raise OSError("replace failed")

            APPLY.os.replace = fail_replace
            try:
                with self.assertRaises(OSError):
                    APPLY.process_manifest(
                        path,
                        verifier_path=VERIFIER,
                        force=None,
                        platform_force="hard",
                        reason="security",
                        platforms="windows-amd64-setup",
                    )
            finally:
                APPLY.os.replace = original_replace
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(list(Path(directory).iterdir()), [path])

            original_fdopen = APPLY.os.fdopen

            def fail_write(*_args, **_kwargs):
                raise OSError("write failed")

            APPLY.os.fdopen = fail_write
            try:
                with self.assertRaises(OSError):
                    APPLY.process_manifest(
                        path,
                        verifier_path=VERIFIER,
                        force=None,
                        platform_force="hard",
                        reason="security",
                        platforms="windows-amd64-setup",
                    )
            finally:
                APPLY.os.fdopen = original_fdopen
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
