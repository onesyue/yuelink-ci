"""Force policy on an unsigned candidate. Percentage rollout is rejected."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/apply-update-rollout.py"
VERIFIER = ROOT / "scripts/verify-update-manifest.py"
RETIRED = {
    "YUELINK_UPDATE_ROLLOUT_SALT",
    "YUELINK_UPDATE_ROLLOUT_PERCENT",
    "YUELINK_UPDATE_FORCE_PLATFORMS",
    "PROMOTE_PERCENT",
    "ROLLOUT_PERCENT",
    "YUELINK_UPDATE_FORCE",
    "YUELINK_UPDATE_FORCE_REASON",
}


def load():
    spec = importlib.util.spec_from_file_location("apply_update_rollout", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


APPLY = load()


class ApplyUpdateRolloutTest(unittest.TestCase):
    def manifest(self) -> dict:
        raw = json.loads((ROOT / "tests/fixtures/update-manifest-v1.json").read_text())
        raw.pop("sig", None)
        return raw

    def non_ios(self, manifest: dict) -> list[str]:
        return sorted(
            name for name in manifest["platforms"] if not str(name).startswith("ios")
        )

    def test_unset_values_do_not_rewrite_the_candidate(self) -> None:
        original = {"schemaVersion": 1, "platforms": {"ios": {}, "windows-amd64-setup": {}}}
        self.assertFalse(APPLY.apply_force(original, force=None, reason=None))
        self.assertNotIn("rollout", original)
        self.assertNotIn("force", original)
        self.assertNotIn("forcePlatforms", original)

    def test_three_readers_cover_legacy_current_and_ios(self) -> None:
        """Execute the three readers the release gate has to keep apart.

        1.3.174 reads only force and forceDeadline. The agreed new reader
        forces only when platformForce is hard, and never on iOS. yuelink#122
        does not parse platformForce. The publisher refuses to emit global
        hard, a deadline, or a rollout field.
        """
        soft = self.manifest()
        self.assertTrue(APPLY.apply_force(soft, force="soft", reason=None))
        self.assertEqual(soft["force"], "soft")
        self.assertNotIn("platformForce", soft)
        self.assertNotIn("forcePlatforms", soft)
        self.assertNotIn("rollout", soft)
        self.assertEqual(APPLY.legacy_client_tier(soft), "soft")
        self.assertEqual(APPLY.current_client_tier(soft, "ios"), "soft")
        self.assertEqual(APPLY.current_client_tier(soft, "windows-amd64-setup"), "soft")
        verifier = APPLY.load_verifier(VERIFIER)
        verifier._validate_rollout_extension(soft)

        hard = self.manifest()
        self.assertTrue(APPLY.apply_force(hard, force="hard", reason="security"))
        self.assertEqual(hard["force"], "soft")
        self.assertEqual(hard["platformForce"], "hard")
        self.assertEqual(hard["forceReason"], "security")
        self.assertEqual(hard["forcePlatforms"], self.non_ios(hard))
        self.assertNotIn("ios", hard["forcePlatforms"])
        self.assertNotIn("rollout", hard)
        self.assertNotIn("forceDeadline", hard)
        self.assertEqual(APPLY.legacy_client_tier(hard), "soft")
        self.assertEqual(APPLY.current_client_tier(hard, "ios"), "soft")
        self.assertEqual(APPLY.current_client_tier(hard, "windows-amd64-setup"), "hard")
        self.assertEqual(APPLY.current_client_tier(hard, "android-arm64-v8a"), "hard")
        self.assertEqual(APPLY.current_client_tier(hard, "linux-amd64-appimage"), "hard")
        self.assertEqual(APPLY.current_client_tier(hard, "macos-universal"), "hard")
        verifier._validate_rollout_extension(hard)

        historical = self.manifest()
        historical["force"] = "soft"
        historical["forcePlatforms"] = self.non_ios(historical)
        self.assertNotIn("platformForce", historical)
        self.assertEqual(APPLY.legacy_client_tier(historical), "soft")
        self.assertEqual(APPLY.current_client_tier(historical, "windows-amd64-setup"), "soft")
        self.assertEqual(APPLY.current_client_tier(historical, "ios"), "soft")
        verifier._validate_rollout_extension(historical)

        hole = self.manifest()
        hole["force"] = "hard"
        hole["forceReason"] = "security"
        hole["forcePlatforms"] = self.non_ios(hole)
        self.assertEqual(APPLY.legacy_client_tier(hole), "hard")
        self.assertEqual(APPLY.current_client_tier(hole, "ios"), "soft")
        self.assertEqual(APPLY.current_client_tier(hole, "windows-amd64-setup"), "soft")
        verifier._validate_rollout_extension(hole)
        with self.assertRaisesRegex(ValueError, "global hard would force 1.3.174 including iOS"):
            APPLY.apply_force(hole, force=None, reason=None)

        deadline = self.manifest()
        deadline["force"] = "soft"
        deadline["forceDeadline"] = "2026-12-01T00:00:00Z"
        deadline["forceReason"] = "security"
        self.assertEqual(APPLY.legacy_client_tier(deadline), "hard")
        verifier._validate_rollout_extension(deadline)
        with self.assertRaisesRegex(ValueError, "deadline"):
            APPLY.apply_force(deadline, force="soft", reason=None)

        with self.assertRaisesRegex(ValueError, "rollout percentage"):
            APPLY.apply_force(
                dict(self.manifest(), rollout={"percent": 10}), force="soft", reason=None
            )

    def test_publisher_rejects_partial_and_ios_force(self) -> None:
        with self.assertRaises(ValueError):
            APPLY.apply_force(self.manifest(), force="hard", reason=None)
        with self.assertRaises(ValueError):
            APPLY.apply_force(self.manifest(), force="soft", reason="security")
        with self.assertRaises(ValueError):
            APPLY.apply_force({"platforms": {"windows-amd64-setup": {}}}, force="hard", reason="security")
        with self.assertRaises(ValueError):
            APPLY.apply_force(self.manifest(), force="wide", reason=None)

    def run_cli(self, overrides: dict[str, str]) -> tuple[subprocess.CompletedProcess, bytes, bytes]:
        env = {key: value for key, value in os.environ.items() if key not in RETIRED}
        env.update(overrides)
        before = (ROOT / "tests/fixtures/update-manifest-v1.json").read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_bytes(before)
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(path)],
                env=env, capture_output=True, text=True, check=False,
            )
            return completed, before, path.read_bytes()

    def test_cli_leaves_bytes_alone_without_environment(self) -> None:
        completed, before, after = self.run_cli({})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(before, after)

    def test_cli_retired_percentage_never_rewrites_candidate(self) -> None:
        for overrides in (
            {"YUELINK_UPDATE_ROLLOUT_PERCENT": "10"},
            {"YUELINK_UPDATE_ROLLOUT_SALT": "cohort", "YUELINK_UPDATE_ROLLOUT_PERCENT": "10"},
            {"YUELINK_UPDATE_FORCE_PLATFORMS": "ios"},
            {"PROMOTE_PERCENT": "10", "YUELINK_UPDATE_FORCE": "hard", "YUELINK_UPDATE_FORCE_REASON": "security"},
            {"YUELINK_UPDATE_FORCE": "hard"},
            {"YUELINK_UPDATE_FORCE": "soft", "YUELINK_UPDATE_FORCE_REASON": "security"},
            {"YUELINK_UPDATE_FORCE": "wide"},
        ):
            with self.subTest(overrides=overrides):
                completed, before, after = self.run_cli(overrides)
                self.assertNotEqual(completed.returncode, 0, completed.stdout)
                self.assertEqual(before, after)

    def test_cli_hard_lists_every_non_ios_platform(self) -> None:
        completed, before, after = self.run_cli({
            "YUELINK_UPDATE_FORCE": "hard",
            "YUELINK_UPDATE_FORCE_REASON": "incompatible",
        })
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotEqual(before, after)
        manifest = json.loads(after)
        self.assertNotIn("rollout", manifest)
        self.assertNotIn("percent", manifest)
        self.assertEqual(manifest["force"], "soft")
        self.assertEqual(manifest["platformForce"], "hard")
        self.assertEqual(manifest["forceReason"], "incompatible")
        self.assertEqual(manifest["forcePlatforms"], self.non_ios(manifest))
        self.assertEqual(APPLY.legacy_client_tier(manifest), "soft")
        self.assertEqual(APPLY.current_client_tier(manifest, "ios"), "soft")
        self.assertEqual(APPLY.current_client_tier(manifest, "windows-amd64-setup"), "hard")

    def test_cli_rejects_global_hard_without_rewriting(self) -> None:
        env = {key: value for key, value in os.environ.items() if key not in RETIRED}
        original = json.loads((ROOT / "tests/fixtures/update-manifest-v1.json").read_text())
        original["force"] = "hard"
        original["forceReason"] = "security"
        raw = json.dumps(original).encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_bytes(raw)
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(path)],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(completed.returncode, 0, completed.stdout)
            self.assertIn("global hard", completed.stderr)
            self.assertEqual(path.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
