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
