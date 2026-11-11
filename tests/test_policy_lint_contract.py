"""policy-ci lints the workflows and shell scripts it ships (2026-09-28).

Before this, yuelink-ci's only lint was whatever the author ran locally:
release.sh line 362 carried SC1078 (an embedded pair of ASCII double quotes
closed the echo string, silently dropping the quotes from the operator hint)
and nothing would have turned red. yueto-ci has run a pinned actionlint for a
while; this repo now runs the same pinned binary plus ShellCheck on *.sh.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_CI = ROOT / ".github" / "workflows" / "policy-ci.yml"
SHELLCHECK = shutil.which("shellcheck")
LINT_BYPASS = re.compile(r"""(?<!\S)['"]?--?ignore(?:['"]?(?:[=\s]|$))|SHELLCHECK_OPTS|shellcheck disable""")


def shell_scripts() -> list[Path]:
    return sorted(list(ROOT.glob("*.sh")) + list((ROOT / "scripts").glob("*.sh")))


class PolicyLintContractTests(unittest.TestCase):
    def test_policy_ci_runs_pinned_actionlint_and_shellcheck(self) -> None:
        text = POLICY_CI.read_text(encoding="utf-8")
        for fragment in (
            "ACTIONLINT_VERSION: '1.7.12'",
            "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8",
            "sha256sum --check --strict",
            "--proto '=https' --tlsv1.2",
            '"$destination/actionlint" .github/workflows/*.yml',
            "command -v shellcheck",
            "shellcheck --severity=warning ./*.sh scripts/*.sh",
            "timeout-minutes:",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)
        # Lint config must not be able to switch checks off.
        # Match the lint bypass flag, not npm's safer --ignore-scripts option.
        self.assertIsNone(LINT_BYPASS.search(text))

    def test_lint_bypass_flag_negative_controls(self) -> None:
        text = POLICY_CI.read_text(encoding="utf-8")
        marker = '"$destination/actionlint" .github/workflows/*.yml'
        self.assertIn(marker, text)
        self.assertIn('npm ci --ignore-scripts', text)
        for flag in ('-ignore .*', '--ignore .*', '-ignore=.*', '--ignore=.*',
                     "'-ignore' '.*'", "'--ignore=.*'", '"-ignore" ".*"', '"--ignore=.*"'):
            with self.subTest(flag=flag):
                mutated = text.replace(marker, '"$destination/actionlint" ' + flag + ' .github/workflows/*.yml', 1)
                self.assertIsNotNone(LINT_BYPASS.search(mutated))

    def test_scan_surface_is_not_empty(self) -> None:
        names = {p.name for p in shell_scripts()}
        self.assertIn("release.sh", names)
        self.assertIn("sync-build.sh", names)
        self.assertGreaterEqual(len(names), 4)

    @unittest.skipIf(SHELLCHECK is None, "shellcheck not installed locally; policy-ci requires it")
    def test_shell_scripts_have_no_shellcheck_warnings(self) -> None:
        proc = subprocess.run(
            [SHELLCHECK, "--severity=warning", "--format=gcc", *map(str, shell_scripts())],
            cwd=ROOT, capture_output=True, text=True, timeout=300,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    @unittest.skipIf(SHELLCHECK is None, "shellcheck not installed locally; policy-ci requires it")
    def test_embedded_quote_regression_is_visible_to_the_lint(self) -> None:
        release = (ROOT / "release.sh").read_text(encoding="utf-8")
        marker = "「Sign immutable release archive」"
        self.assertIn(marker, release)
        mutated = release.replace(marker, '"Sign immutable release archive', 1)
        proc = subprocess.run(
            [SHELLCHECK, "--severity=warning", "--format=gcc", "-"],
            input=mutated, cwd=ROOT, capture_output=True, text=True, timeout=300,
        )
        self.assertIn("SC1072", proc.stdout)


if __name__ == "__main__":
    unittest.main()
