"""The candidate generator attaches rollout keys only when asked."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import unittest
from pathlib import Path

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

    def test_cli_leaves_bytes_alone_without_environment(self, tmp_path=None) -> None:
        path = ROOT / "tests/fixtures/update-manifest-v1.json"
        completed = subprocess.run(
            ["python3", str(SCRIPT), "--manifest", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("rollout", json.loads(path.read_text()))
