"""Pull requests rerun a dial platform only when its inputs changed."""

from __future__ import annotations

import re
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
        def head_reader(path: str) -> bytes | None:
            data = _reader(path)
            if path == scope.MATRIX_FILE and data is not None:
                return data.replace(b"sample_timeout: 12s", b"sample_timeout: 13s", 1)
            return data

        self.assertNotEqual(head_reader(scope.MATRIX_FILE), _reader(scope.MATRIX_FILE))
        decision = scope.decide(
            "pull_request", self.workflow, head_reader, self.workflow, _reader
        )
        self.assertEqual(decision["names"], ["android"])
        self.assertEqual([row["name"] for row in decision["rows"]], ["android"])
        self.assertIs(decision["ios"], False)
        self.assertIs(decision["auth"], False)

    def test_shared_baseline_change_reruns_dial_ios_and_auth(self) -> None:
        head = self.workflow.replace("timeout-minutes: 15\n", "timeout-minutes: 16\n", 1)
        self.assertNotEqual(head, self.workflow)
        decision = scope.decide("pull_request", head, _reader, self.workflow, _reader)
        self.assertEqual(decision["names"], list(scope.DIAL_PLATFORMS))
        self.assertIs(decision["ios"], True)
        self.assertIs(decision["auth"], True)

    def test_workflow_settings_rerun_dial_ios_and_auth(self) -> None:
        for pin in ("GO_VERSION", "FLUTTER_VERSION"):
            with self.subTest(pin=pin):
                head, changes = re.subn(
                    rf"(?m)^  {pin}: '[^']+'$", f"  {pin}: '1.2.3'", self.workflow
                )
                self.assertEqual(changes, 1)
                decision = scope.decide(
                    "pull_request", head, _reader, self.workflow, _reader
                )
                self.assertEqual(decision["names"], list(scope.DIAL_PLATFORMS))
                self.assertIs(decision["ios"], True)
                self.assertIs(decision["auth"], True)

    def test_shared_defaults_after_jobs_rerun_dial_ios_and_auth(self) -> None:
        defaults = "\ndefaults:\n  run:\n    shell: "
        base = self.workflow + defaults + "bash\n"
        head = self.workflow + defaults + "sh\n"
        decision = scope.decide("pull_request", head, _reader, base, _reader)
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
