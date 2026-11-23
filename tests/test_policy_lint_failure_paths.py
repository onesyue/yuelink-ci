"""Run the Policy CI lint step against local archives and injected failures."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/policy-ci.yml"

FAKE_COMMANDS = """
curl() {
  echo download >> "$CALLS"
  if [ "$FAILURE" = download ]; then return 28; fi
  while [ "$1" != --output ]; do shift; done
  cp "$FIXTURE_ARCHIVE" "$2"
}
sha256sum() {
  "$CHECKSUM_TOOL" "$@"
}
"""

FAKE_ACTIONLINT = b"""#!/bin/sh
if [ "$1" = -version ]; then echo 1.7.12; exit 0; fi
echo actionlint >> "$CALLS"
if [ "$FAILURE" = actionlint ]; then exit 41; fi
"""


class PolicyLintFailurePathTests(unittest.TestCase):
    def run_lint_step(self, failure: str) -> tuple[int, list[str], bool]:
        step = WORKFLOW.read_text().split("      - name: Install pinned actionlint\n", 1)[1]
        block = step.split("        run: |\n", 1)[1].split("\n\n", 1)[0]
        with tempfile.TemporaryDirectory(prefix="policy lint ") as directory:
            root = Path(directory)
            archive = root / "fixture.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                member = tarfile.TarInfo("other" if failure == "extraction" else "actionlint")
                member.mode = 0o755
                member.size = len(FAKE_ACTIONLINT)
                bundle.addfile(member, io.BytesIO(FAKE_ACTIONLINT))

            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            # Run the installed tools through the real policy entry. Suppress
            # only recursive discovery inside this isolated test fixture.
            bin_dir = root / "bin"
            bin_dir.mkdir()
            python = bin_dir / "python3"
            python.write_text('#!/bin/sh\nif [ "$1" = -m ] && [ "$2" = unittest ]; then exit 0; fi\n'
                              'exec "$TEST_PYTHON" "$@"\n')
            python.chmod(0o755)
            shellcheck = bin_dir / "shellcheck"
            shellcheck.write_text('#!/bin/sh\necho shellcheck >> "$CALLS"\n'
                                  'if [ "$FAILURE" = shellcheck ]; then exit 42; fi\n')
            shellcheck.chmod(0o755)
            calls = root / "calls"
            env = {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "TEST_PYTHON": sys.executable,
                "GITHUB_PATH": str(root / "github-path"),
                "GITHUB_ACTIONS": "true",
                "RUNNER_TEMP": directory,
                "ACTIONLINT_VERSION": "0.0.0-fixture",
                "ACTIONLINT_ARCHIVE_SHA256": "0" * 64 if failure == "checksum" else digest,
                "CHECKSUM_TOOL": shutil.which("gsha256sum") or shutil.which("sha256sum") or "sha256sum",
                "FIXTURE_ARCHIVE": str(archive),
                "CALLS": str(calls),
                "FAILURE": failure,
            }
            result = subprocess.run(
                ["bash", "-c", FAKE_COMMANDS + textwrap.dedent(block)
                 + '\nexport PATH="$(cat "$GITHUB_PATH"):$PATH"\nscripts/check-policy.sh ci\n'],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=10,
            )
            installed = (root / "actionlint-bin/actionlint").exists()
            return result.returncode, calls.read_text().splitlines(), installed

    def test_bad_downloads_never_reach_the_linters(self) -> None:
        for failure, status in (("download", 28), ("checksum", 1), ("extraction", None)):
            with self.subTest(failure=failure):
                rc, calls, installed = self.run_lint_step(failure)
                self.assertNotEqual(rc, 0)
                if status is not None:
                    self.assertEqual(rc, status)
                self.assertEqual(calls, ["download"])
                self.assertFalse(installed)

    def test_either_linter_failure_fails_the_step(self) -> None:
        self.assertEqual(self.run_lint_step("actionlint"), (41, ["download", "actionlint"], True))
        self.assertEqual(
            self.run_lint_step("shellcheck"), (42, ["download", "actionlint", "shellcheck"], True),
        )

    def test_verified_archive_runs_both_linters(self) -> None:
        self.assertEqual(
            self.run_lint_step("none"), (0, ["download", "actionlint", "shellcheck"], True),
        )
