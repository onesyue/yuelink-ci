"""Pull requests rerun a dial platform only when its inputs changed."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import pr_platform_scope as scope  # noqa: E402

DIAL = ROOT / ".github" / "workflows" / "dial-gate.yml"
BUILD = ROOT / ".github" / "workflows" / "build.yml"


def _reader(path: str) -> bytes | None:
    file = ROOT / path
    return file.read_bytes() if file.is_file() else None


class PlatformScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = DIAL.read_text(encoding="utf-8")

    def test_non_pull_request_runs_every_platform(self) -> None:
        decision = scope.decide("push", self.workflow, _reader)
        self.assertEqual(decision["names"], list(scope.DIAL_PLATFORMS))
        self.assertEqual(decision["skipped"], [])
        self.assertIs(decision["ios"], True)
        self.assertIs(decision["auth"], True)

    def test_identical_head_and_base_skip_every_platform(self) -> None:
        decision = scope.decide(
            "pull_request", self.workflow, _reader, self.workflow, _reader
        )
        self.assertEqual(decision["names"], [])
        self.assertEqual(decision["skipped"], list(scope.DIAL_PLATFORMS))
        self.assertIs(decision["ios"], False)
        self.assertIs(decision["auth"], False)

    def test_android_row_change_runs_only_android(self) -> None:
        head = self.workflow.replace("sample_timeout: 12s", "sample_timeout: 13s", 1)
        self.assertNotEqual(head, self.workflow)
        decision = scope.decide("pull_request", head, _reader, self.workflow, _reader)
        self.assertEqual(decision["names"], ["android"])
        self.assertNotIn("android", decision["skipped"])
        self.assertIs(decision["ios"], False)
        self.assertIs(decision["auth"], False)

    def test_shared_baseline_change_reruns_dial_ios_and_auth(self) -> None:
        head = self.workflow.replace("timeout-minutes: 15\n", "timeout-minutes: 16\n", 1)
        self.assertNotEqual(head, self.workflow)
        decision = scope.decide("pull_request", head, _reader, self.workflow, _reader)
        self.assertEqual(decision["names"], list(scope.DIAL_PLATFORMS))
        self.assertIs(decision["ios"], True)
        self.assertIs(decision["auth"], True)

    def test_ios_job_change_runs_only_ios(self) -> None:
        needle = "flutter build ios --simulator --config-only --no-pub"
        head = self.workflow.replace(needle, needle + " --verbose", 1)
        self.assertNotEqual(head, self.workflow)
        decision = scope.decide("pull_request", head, _reader, self.workflow, _reader)
        self.assertEqual(decision["names"], [])
        self.assertIs(decision["ios"], True)
        self.assertIs(decision["auth"], False)

    def test_unreadable_base_runs_every_platform(self) -> None:
        base = self.workflow.replace("  platform_scope:\n", "  removed_scope:\n", 1)
        decision = scope.decide("pull_request", self.workflow, _reader, base, _reader)
        self.assertEqual(decision["names"], list(scope.DIAL_PLATFORMS))
        self.assertEqual(decision["skipped"], [])
        self.assertIs(decision["ios"], True)
        self.assertIs(decision["auth"], True)

    def test_live_workflow_parses_and_matches_itself(self) -> None:
        decision = scope.decide(
            "pull_request", self.workflow, _reader, self.workflow, _reader
        )
        self.assertEqual(decision["skipped"], list(scope.DIAL_PLATFORMS))
        build = BUILD.read_text(encoding="utf-8")
        self.assertIn("name: dial-platform-scope", build)
        self.assertIn('Path("dial-scope/scope.json")', build)
        self.assertIn("skipped platform also produced dial evidence", build)
        self.assertIn("for platform in required:", build)

    def test_main_rejects_a_non_hex_pull_request_base(self) -> None:
        code = scope.main(
            [
                "--event",
                "pull_request",
                "--base",
                "master",
                "--workflow",
                str(DIAL),
                "--json",
                str(ROOT / "scope.json"),
            ]
        )
        self.assertEqual(code, 1)
        self.assertFalse((ROOT / "scope.json").exists())


if __name__ == "__main__":
    unittest.main()
