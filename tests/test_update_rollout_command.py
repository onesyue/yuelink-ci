"""The promotion shell admits force policy and does not touch storage."""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/promote-update-rollout.sh"
WORKFLOW = ROOT / ".github/workflows/promote-update-rollout.yml"


class RolloutCommandTests(unittest.TestCase):
    def execute(self, changes=None):
        env = {
            "PATH": os.environ["PATH"],
            "HOME": os.environ.get("HOME", "/tmp"),
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/master",
            "PROMOTE_VERSION": "1.3.175",
            "PROMOTE_BASELINE_VERSION": "1.3.174",
            "PROMOTE_FORCE": "soft",
            "PROMOTE_FORCE_REASON": "",
        }
        env.update(changes or {})
        return subprocess.run(
            ["bash", str(SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_soft_admission_does_not_warn_about_legacy_clients(self) -> None:
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Admitted soft root policy", result.stdout)
        self.assertIn("iOS stays a prompt", result.stdout)
        self.assertNotIn("reads only force", result.stdout)
        self.assertNotIn("percent", result.stdout)

    def test_hard_admission_records_the_legacy_global_hole(self) -> None:
        result = self.execute({
            "PROMOTE_FORCE": "hard",
            "PROMOTE_FORCE_REASON": "security",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Admitted hard root policy", result.stdout)
        self.assertIn("iOS stays a prompt for current clients", result.stdout)
        self.assertIn("1.3.174 reads only force", result.stdout)
        self.assertIn("including iOS", result.stdout)

    def test_invalid_policy_never_succeeds(self) -> None:
        for changes in (
            {"GITHUB_REF": "refs/tags/v1.3.175"},
            {"GITHUB_EVENT_NAME": "push"},
            {"PROMOTE_VERSION": "master"},
            {"PROMOTE_BASELINE_VERSION": ""},
            {"PROMOTE_FORCE": "wide"},
            {"PROMOTE_FORCE": "hard", "PROMOTE_FORCE_REASON": ""},
            {"PROMOTE_FORCE": "hard", "PROMOTE_FORCE_REASON": "marketing"},
            {"PROMOTE_FORCE_REASON": "security"},
            {"PROMOTE_PERCENT": "10"},
            {"ROLLOUT_PERCENT": "10"},
            {"YUELINK_UPDATE_ROLLOUT_PERCENT": "0"},
        ):
            with self.subTest(changes=changes):
                result = self.execute(changes)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn("Admitted", result.stdout)

    def test_manual_workflow_has_no_signing_storage_or_percentage(self) -> None:
        text = WORKFLOW.read_text()
        shell = SCRIPT.read_text()
        for forbidden in (
            "pull_request:",
            "push:",
            "UPDATE_MANIFEST",
            "secrets.",
            "id-token:",
            "packages:",
            "flutter",
            "promote_in_pipeline",
            "promote-root",
            "wrangler",
            "network-ip",
            "aws ",
            "environment:",
            "percent",
            "ROLLOUT_PERCENT",
        ):
            self.assertNotIn(forbidden, text, forbidden)
        for forbidden in (
            "promote_in_pipeline",
            "promote-root",
            "wrangler",
            "network-ip",
            "aws ",
        ):
            self.assertNotIn(forbidden, shell, forbidden)
        self.assertIn("percentage rollout is not accepted", shell)
        self.assertIn("github.event_name == 'workflow_dispatch'", text)
        self.assertIn("github.ref == 'refs/heads/master'", text)
        self.assertIn("contents: read", text)
        self.assertEqual(text.count("        required: true"), 3)
        self.assertIn("bash scripts/promote-update-rollout.sh", text)
        self.assertNotIn("${{", shell)
        run = text.split("run:", 1)[1]
        self.assertNotIn("${{", run)


if __name__ == "__main__":
    unittest.main()
