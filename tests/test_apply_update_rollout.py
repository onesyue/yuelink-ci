"""The candidate generator attaches rollout keys only when asked."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/apply-update-rollout.py"


def load():
    spec = importlib.util.spec_from_file_location("apply_update_rollout", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


APPLY = load()


class ApplyUpdateRolloutTest(unittest.TestCase):
    def manifest(self) -> dict:
        raw = json.loads(
            (ROOT / "tests/fixtures/update-manifest-v1.json").read_text()
        )
        raw.pop("sig", None)
        return raw

    def test_unset_values_do_not_rewrite_the_candidate(self) -> None:
        original = {"schemaVersion": 1, "platforms": {"ios": {}}}
        self.assertFalse(
            APPLY.apply_rollout(
                original, salt=None, percent=None, force_platforms=None
            )
        )
        self.assertNotIn("rollout", original)
        self.assertNotIn("forcePlatforms", original)

    def test_percent_and_platforms_are_attached_and_checked(self) -> None:
        manifest = self.manifest()
        self.assertTrue(
            APPLY.apply_rollout(
                manifest,
                salt="cohort",
                percent="0",
                force_platforms=["ios"],
            )
        )
        APPLY.load_verifier(ROOT / "scripts/verify-update-manifest.py")._validate_rollout_extension(
            manifest
        )
        self.assertEqual(manifest["rollout"], {"salt": "cohort", "percent": 0})
        self.assertEqual(manifest["forcePlatforms"], ["ios"])
        with self.assertRaises(ValueError):
            APPLY.apply_rollout(
                self.manifest(), salt="cohort", percent=None, force_platforms=None
            )
        with self.assertRaises(ValueError):
            APPLY.apply_rollout(
                self.manifest(), salt="cohort", percent="101", force_platforms=None
            )
        with self.assertRaises(Exception):
            bad = self.manifest()
            APPLY.apply_rollout(
                bad, salt="cohort", percent="10", force_platforms=["missing"]
            )
            APPLY.load_verifier(
                ROOT / "scripts/verify-update-manifest.py"
            )._validate_rollout_extension(bad)

    def run_cli(self, overrides: dict[str, str]) -> tuple[subprocess.CompletedProcess, bytes, bytes]:
        env = {key: value for key, value in os.environ.items() if key not in {
            "YUELINK_UPDATE_ROLLOUT_SALT", "YUELINK_UPDATE_ROLLOUT_PERCENT",
            "YUELINK_UPDATE_FORCE_PLATFORMS",
        }}
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
        with mock.patch.dict(os.environ, {
            "YUELINK_UPDATE_ROLLOUT_SALT": "ambient",
            "YUELINK_UPDATE_ROLLOUT_PERCENT": "25",
            "YUELINK_UPDATE_FORCE_PLATFORMS": "ios",
        }):
            completed, before, after = self.run_cli({})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(before, after)

    def test_cli_applies_explicit_environment(self) -> None:
        completed, before, after = self.run_cli({
            "YUELINK_UPDATE_ROLLOUT_SALT": "cohort",
            "YUELINK_UPDATE_ROLLOUT_PERCENT": "10",
            "YUELINK_UPDATE_FORCE_PLATFORMS": "ios",
        })
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotEqual(before, after)
        manifest = json.loads(after)
        self.assertEqual(manifest["rollout"], {"salt": "cohort", "percent": 10})
        self.assertEqual(manifest["forcePlatforms"], ["ios"])

    def test_cli_invalid_environment_never_rewrites_candidate(self) -> None:
        for overrides in (
            {"YUELINK_UPDATE_ROLLOUT_SALT": "cohort"},
            {"YUELINK_UPDATE_ROLLOUT_SALT": "cohort", "YUELINK_UPDATE_ROLLOUT_PERCENT": "101"},
            {"YUELINK_UPDATE_FORCE_PLATFORMS": "missing"},
            {"YUELINK_UPDATE_FORCE_PLATFORMS": "ios,ios"},
        ):
            with self.subTest(overrides=overrides):
                completed, before, after = self.run_cli(overrides)
                self.assertNotEqual(completed.returncode, 0)
                self.assertEqual(before, after)


class ForcePolicyTest(unittest.TestCase):
    def manifest(self, version: str) -> dict:
        raw = json.loads(
            (ROOT / "tests/fixtures/update-manifest-v1.json").read_text()
        )
        raw.pop("sig", None)
        raw.pop("force", None)
        raw.pop("forceReason", None)
        raw["version"] = version
        return raw

    def write_policy(self, directory: str, value) -> Path:
        path = Path(directory) / "update-force.json"
        path.write_text(json.dumps(value))
        return path

    def test_committed_policy_is_valid(self) -> None:
        policy = APPLY.load_force_policy(ROOT / "policy/update-force.json")
        self.assertEqual(policy["version"], "1.3.176")
        self.assertEqual(policy["force"], "soft")
        self.assertEqual(policy["forceReason"], "security")
        manifest = self.manifest(policy["version"])
        self.assertTrue(APPLY.apply_force_policy(manifest, policy))
        self.assertEqual(manifest["force"], "soft")
        self.assertEqual(manifest["forceReason"], "security")
        self.assertNotIn("forcePlatforms", manifest)
        APPLY.load_verifier(
            ROOT / "scripts/verify-update-manifest.py"
        )._validate_rollout_extension(manifest)

    def test_missing_policy_changes_nothing(self) -> None:
        manifest = self.manifest("1.3.176")
        self.assertIsNone(APPLY.load_force_policy(Path("/nonexistent/x.json")))
        self.assertFalse(APPLY.apply_force_policy(manifest, None))
        self.assertNotIn("force", manifest)

    def test_policy_applies_only_to_its_version(self) -> None:
        policy = {"version": "1.3.176", "force": "soft", "forceReason": "security"}
        other = self.manifest("1.3.177")
        self.assertFalse(APPLY.apply_force_policy(other, policy))
        self.assertNotIn("force", other)
        same = self.manifest("1.3.176")
        self.assertTrue(APPLY.apply_force_policy(same, policy))
        self.assertEqual((same["force"], same["forceReason"]), ("soft", "security"))
        self.assertNotIn("forcePlatforms", same)
        APPLY.load_verifier(
            ROOT / "scripts/verify-update-manifest.py"
        )._validate_rollout_extension(same)

    def test_matching_global_hard_is_refused_before_mutation(self) -> None:
        policy = {"version": "1.3.176", "force": "hard", "forceReason": "security"}
        manifest = self.manifest("1.3.176")
        snapshot = json.loads(json.dumps(manifest))
        with self.assertRaisesRegex(
            ValueError, "global hard would force 1.3.174 including iOS"
        ):
            APPLY.apply_force_policy(manifest, policy)
        self.assertEqual(manifest, snapshot)
        later = self.manifest("1.3.177")
        later_snapshot = json.loads(json.dumps(later))
        self.assertFalse(APPLY.apply_force_policy(later, policy))
        self.assertEqual(later, later_snapshot)

    def test_well_formed_hard_policy_still_loads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            loaded = APPLY.load_force_policy(self.write_policy(directory, {
                "version": "1.3.176",
                "force": "hard",
                "forceReason": "incompatible",
            }))
        self.assertEqual(loaded["force"], "hard")

    def test_invalid_policy_is_rejected(self) -> None:
        bad_values = (
            [],
            {"version": "1.3.176", "force": "hard"},
            {"version": "1.3.176", "force": "hard", "forceReason": "重要修复"},
            {"version": "1.3.176", "force": "always", "forceReason": "security"},
            {"version": "v1.3.176", "force": "hard", "forceReason": "security"},
            {"version": "1.3.176", "force": "hard", "forceReason": "security", "x": 1},
        )
        with tempfile.TemporaryDirectory() as directory:
            for value in bad_values:
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):
                        APPLY.load_force_policy(self.write_policy(directory, value))

    def test_existing_force_keys_are_not_overwritten(self) -> None:
        manifest = self.manifest("1.3.176")
        manifest["force"] = "soft"
        with self.assertRaisesRegex(ValueError, "candidate already carries force keys"):
            APPLY.apply_force_policy(
                manifest,
                {"version": "1.3.176", "force": "soft", "forceReason": "incompatible"},
            )
        self.assertEqual(manifest["force"], "soft")
        self.assertNotIn("forceReason", manifest)

    def run_cli(self, version: str, policy) -> tuple[subprocess.CompletedProcess, bytes, bytes]:
        env = {key: value for key, value in os.environ.items() if not key.startswith(
            "YUELINK_UPDATE_"
        )}
        before_value = self.manifest(version)
        before = json.dumps(before_value).encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_bytes(before)
            policy_path = self.write_policy(directory, policy)
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(path),
                 "--force-policy", str(policy_path)],
                env=env, capture_output=True, text=True, check=False,
            )
            return completed, before, path.read_bytes()

    def test_cli_rejects_global_hard_and_writes_soft(self) -> None:
        hard = {"version": "1.3.176", "force": "hard", "forceReason": "security"}
        completed, before, after = self.run_cli("1.3.176", hard)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("global hard would force 1.3.174 including iOS", completed.stderr)
        self.assertEqual(before, after)
        completed, before, after = self.run_cli("1.3.177", hard)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(before, after)
        soft = {"version": "1.3.176", "force": "soft", "forceReason": "security"}
        completed, before, after = self.run_cli("1.3.176", soft)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        manifest = json.loads(after)
        self.assertEqual(manifest["force"], "soft")
        self.assertEqual(manifest["forceReason"], "security")
        self.assertNotIn("forcePlatforms", manifest)

    def test_cli_default_policy_writes_soft_for_its_version(self) -> None:
        env = {key: value for key, value in os.environ.items() if not key.startswith(
            "YUELINK_UPDATE_"
        )}
        before = json.dumps(self.manifest("1.3.176")).encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_bytes(before)
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(path)],
                env=env, capture_output=True, text=True, check=False,
            )
            after = path.read_bytes()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        manifest = json.loads(after)
        self.assertEqual(manifest["force"], "soft")
        self.assertEqual(manifest["forceReason"], "security")
        self.assertNotEqual(before, after)

    def test_cli_global_hard_does_not_persist_rollout(self) -> None:
        env = {key: value for key, value in os.environ.items() if not key.startswith(
            "YUELINK_UPDATE_"
        )}
        env["YUELINK_UPDATE_ROLLOUT_SALT"] = "cohort"
        env["YUELINK_UPDATE_ROLLOUT_PERCENT"] = "10"
        before = json.dumps(self.manifest("1.3.176")).encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_bytes(before)
            policy_path = self.write_policy(directory, {
                "version": "1.3.176", "force": "hard", "forceReason": "security",
            })
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(path),
                 "--force-policy", str(policy_path)],
                env=env, capture_output=True, text=True, check=False,
            )
            after = path.read_bytes()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("global hard would force 1.3.174 including iOS", completed.stderr)
        self.assertEqual(before, after)

    def test_cli_invalid_policy_never_rewrites_candidate(self) -> None:
        completed, before, after = self.run_cli(
            "1.3.176", {"version": "1.3.176", "force": "hard", "forceReason": "x"}
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(before, after)
