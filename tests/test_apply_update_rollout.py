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
        """Execute the three manifest readers the release gate has to keep apart.

        Removing the iOS exclusion, or teaching the legacy reader to honor
        forcePlatforms, fails this test. 1.3.174 reads only force.
        """
        soft = self.manifest()
        self.assertTrue(APPLY.apply_force(soft, force="soft", reason=None))
        self.assertEqual(APPLY.legacy_client_tier(soft), "soft")
        self.assertEqual(APPLY.current_client_tier(soft, "ios"), "soft")
        self.assertEqual(APPLY.current_client_tier(soft, "windows-amd64-setup"), "soft")
        self.assertNotIn("forcePlatforms", soft)
        self.assertNotIn("rollout", soft)
        verifier = APPLY.load_verifier(VERIFIER)
        verifier._validate_rollout_extension(soft)

        hard = self.manifest()
        self.assertTrue(APPLY.apply_force(hard, force="hard", reason="security"))
        self.assertEqual(hard["forcePlatforms"], self.non_ios(hard))
        self.assertNotIn("ios", hard["forcePlatforms"])
        self.assertNotIn("rollout", hard)
        self.assertEqual(APPLY.legacy_client_tier(hard), "hard")
        self.assertEqual(APPLY.current_client_tier(hard, "ios"), "soft")
        self.assertEqual(APPLY.current_client_tier(hard, "android-arm64-v8a"), "hard")
        self.assertEqual(APPLY.current_client_tier(hard, "linux-amd64-appimage"), "hard")
        verifier._validate_rollout_extension(hard)

        poisoned = self.manifest()
        poisoned["force"] = "hard"
        poisoned["forceReason"] = "security"
        poisoned["forcePlatforms"] = ["ios", "windows-amd64-setup"]
        with self.assertRaises(Exception):
            verifier._validate_rollout_extension(poisoned)
        global_hard = self.manifest()
        global_hard["force"] = "hard"
        global_hard["forceReason"] = "security"
        with self.assertRaises(Exception):
            verifier._validate_rollout_extension(global_hard)
        with self.assertRaises(ValueError):
            APPLY.apply_force(dict(self.manifest(), rollout={"percent": 10}), force="soft", reason=None)

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
        self.assertEqual(manifest["force"], "hard")
        self.assertEqual(manifest["forceReason"], "incompatible")
        self.assertEqual(manifest["forcePlatforms"], self.non_ios(manifest))
        self.assertEqual(APPLY.legacy_client_tier(manifest), "hard")
        self.assertEqual(APPLY.current_client_tier(manifest, "ios"), "soft")


if __name__ == "__main__":
    unittest.main()
