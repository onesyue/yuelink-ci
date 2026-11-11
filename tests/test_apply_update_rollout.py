"""Deprecated force alias. Soft and unset delegate; hard is not rewritten."""

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
RETIRED = {
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
    spec = importlib.util.spec_from_file_location("apply_update_rollout", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


APPLY = load()


class ApplyUpdateRolloutAliasTest(unittest.TestCase):
    def manifest(self) -> dict:
        raw = json.loads((ROOT / "tests/fixtures/update-manifest-v1.json").read_text())
        raw.pop("sig", None)
        return raw

    def test_unset_values_do_not_rewrite_the_candidate(self) -> None:
        original = {"schemaVersion": 1, "platforms": {"ios": {}, "windows-amd64-setup": {}}}
        self.assertFalse(APPLY.apply_force(original, force=None, reason=None))
        self.assertNotIn("force", original)
        self.assertNotIn("platformForce", original)

    def test_soft_clears_a_platform_extension(self) -> None:
        soft = self.manifest()
        soft["platformForce"] = "hard"
        soft["forcePlatforms"] = ["windows-amd64-setup"]
        soft["forceReason"] = "security"
        self.assertTrue(APPLY.apply_force(soft, force="soft", reason=None))
        self.assertEqual(soft["force"], "soft")
        self.assertNotIn("platformForce", soft)
        self.assertNotIn("forcePlatforms", soft)
        self.assertEqual(APPLY.legacy_client_tier(soft), "soft")
        self.assertEqual(APPLY.current_client_tier(soft, "windows-amd64-setup"), "soft")

    def test_operator_hard_is_rejected_without_translation(self) -> None:
        original = self.manifest()
        before = json.dumps(original, sort_keys=True)
        with self.assertRaisesRegex(ValueError, "operator force=hard is retired"):
            APPLY.apply_force(original, force="hard", reason="security")
        self.assertEqual(json.dumps(original, sort_keys=True), before)
        self.assertNotIn("platformForce", original)

    def test_historical_shapes_stay_rejected_or_soft(self) -> None:
        historical = self.manifest()
        historical["force"] = "soft"
        historical["forcePlatforms"] = ["windows-amd64-setup"]
        self.assertEqual(APPLY.current_client_tier(historical, "windows-amd64-setup"), "soft")
        self.assertEqual(APPLY.legacy_client_tier(historical), "soft")

        hole = self.manifest()
        hole["force"] = "hard"
        hole["forceReason"] = "security"
        self.assertEqual(APPLY.legacy_client_tier(hole), "hard")
        self.assertEqual(APPLY.current_client_tier(hole, "windows-amd64-setup"), "soft")
        with self.assertRaisesRegex(ValueError, "global hard"):
            APPLY.apply_force(hole, force=None, reason=None)

        deadline = self.manifest()
        deadline["forceDeadline"] = "2026-12-01T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "deadline"):
            APPLY.apply_force(deadline, force="soft", reason=None)
        with self.assertRaisesRegex(ValueError, "rollout percentage"):
            APPLY.apply_force(dict(self.manifest(), rollout={"percent": 10}), force="soft", reason=None)
        with self.assertRaises(ValueError):
            APPLY.apply_force(self.manifest(), force="soft", reason="security")
        with self.assertRaises(ValueError):
            APPLY.apply_force(self.manifest(), force="wide", reason=None)

    def run_cli(self, overrides: dict[str, str], *, signed: bool = False) -> tuple[subprocess.CompletedProcess, bytes, bytes]:
        env = {key: value for key, value in os.environ.items() if key not in RETIRED}
        env.update(overrides)
        payload = json.loads((ROOT / "tests/fixtures/update-manifest-v1.json").read_text())
        if not signed:
            payload.pop("sig", None)
        before = json.dumps(payload).encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_bytes(before)
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(path)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            return completed, before, path.read_bytes()

    def test_cli_leaves_unsigned_bytes_alone_without_environment(self) -> None:
        completed, before, after = self.run_cli({})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(before, after)

    def test_cli_rejects_signed_input_without_rewriting(self) -> None:
        completed, before, after = self.run_cli({}, signed=True)
        self.assertNotEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("signed", completed.stderr)
        self.assertEqual(before, after)

    def test_cli_retired_inputs_never_rewrite_the_candidate(self) -> None:
        for overrides in (
            {"YUELINK_UPDATE_ROLLOUT_PERCENT": "10"},
            {"YUELINK_UPDATE_ROLLOUT_SALT": "cohort", "YUELINK_UPDATE_ROLLOUT_PERCENT": "10"},
            {"YUELINK_UPDATE_FORCE_PLATFORMS": "windows-amd64-setup"},
            {"YUELINK_UPDATE_PLATFORM_FORCE": "hard"},
            {"PROMOTE_PERCENT": "10", "YUELINK_UPDATE_FORCE": "hard", "YUELINK_UPDATE_FORCE_REASON": "security"},
            {"YUELINK_UPDATE_FORCE": "hard"},
            {"YUELINK_UPDATE_FORCE": "hard", "YUELINK_UPDATE_FORCE_REASON": "incompatible"},
            {"YUELINK_UPDATE_FORCE": "soft", "YUELINK_UPDATE_FORCE_REASON": "security"},
            {"YUELINK_UPDATE_FORCE": "wide"},
        ):
            with self.subTest(overrides=overrides):
                completed, before, after = self.run_cli(overrides)
                self.assertNotEqual(completed.returncode, 0, completed.stdout)
                self.assertEqual(before, after)

    def test_cli_operator_hard_names_the_replacement_helper(self) -> None:
        completed, before, after = self.run_cli({
            "YUELINK_UPDATE_FORCE": "hard",
            "YUELINK_UPDATE_FORCE_REASON": "security",
        })
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("apply-update-force.py", completed.stderr)
        self.assertEqual(before, after)

    def test_cli_rejects_global_hard_without_rewriting(self) -> None:
        env = {key: value for key, value in os.environ.items() if key not in RETIRED}
        original = self.manifest()
        original["force"] = "hard"
        original["forceReason"] = "security"
        raw = json.dumps(original).encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_bytes(raw)
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(path)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0, completed.stdout)
            self.assertIn("global hard", completed.stderr)
            self.assertEqual(path.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
