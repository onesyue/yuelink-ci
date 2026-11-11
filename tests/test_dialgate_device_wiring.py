"""Check exact-source offline wrapper tests and parse-only Windows admission."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('device_generator', ROOT / 'scripts/generate-dialgate-verify.py')
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
WORKFLOW = ROOT / '.github/workflows/dialgate-verify.yml'
PARSE_NAME = '      - name: Parse device wrapper syntax without execution\n'


class DeviceWiringTests(unittest.TestCase):
    def test_linux_exact_command_directory_and_failure_propagation(self) -> None:
        workflow = WORKFLOW.read_text()
        job = builder.job(workflow, 'linux_client_tests')
        marker = '- name: Test device wrappers with offline mocks\n'
        self.assertEqual(job.count(marker), 1)
        self.assertIn('        working-directory: candidate\n', job)
        self.assertIn('ref: ${{ needs.dial_source_binding.outputs.candidate_commit }}', job)
        self.assertLess(job.index('Confirm client test source identity'), job.index(marker))
        script = textwrap.dedent(job.split(marker, 1)[1].split('      - name:', 1)[0].split('run: |\n', 1)[1])
        self.assertIn('${{ runner.temp }}/linux-device-wrapper-tests.log', job)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); candidate = root / 'candidate'; candidate.mkdir()
            fake = root / 'python3'
            # Use the current interpreter directly: the stub must not recurse
            # through the fake python3 placed first on PATH.
            fake.write_text('#!' + sys.executable + '\nimport json,os,pathlib,sys\n'
                            "pathlib.Path(os.environ['CALLS']).write_text(json.dumps({'args':sys.argv[1:], 'cwd':os.getcwd()}))\n"
                            "print('offline mock receipt')\nraise SystemExit(int(os.environ['FAKE_EXIT']))\n")
            fake.chmod(0o755)
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                   'RUNNER_TEMP': str(root), 'CALLS': str(root / 'calls')}
            for code in (0, 17):
                result = subprocess.run(['bash', '-c', script], cwd=candidate,
                                        env={**env, 'FAKE_EXIT': str(code)}, capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stderr)
                call = json.loads((root / 'calls').read_text())
                self.assertEqual(call['args'], ['scripts/ci/test_dialgate_device_wrappers.py'])
                self.assertEqual(Path(call['cwd']), candidate.resolve())
                self.assertEqual((root / 'linux-device-wrapper-tests.log').read_text(), 'offline mock receipt\n')
                if code:
                    bad = subprocess.run(['bash', '-c', script.replace('set -euo pipefail', 'set -eu')],
                                         cwd=candidate, env={**env, 'FAKE_EXIT': str(code)}, capture_output=True)
                    self.assertEqual(bad.returncode, 0, 'negative control must expose swallowed test failure')

    def assert_parse_only(self, workflow: str) -> None:
        job = builder.job(workflow, 'dial_gate')
        self.assertEqual(job.count(PARSE_NAME), 1)
        step = job.split(PARSE_NAME, 1)[1].split('      - ', 1)[0]
        expected = """        if: matrix.platform == 'windows'
        shell: pwsh
        run: |
          $ErrorActionPreference = 'Stop'
          $path = (Resolve-Path -LiteralPath 'candidate/core/dialgate/device/windows.ps1').Path
          $tokens = $null
          $parseErrors = $null
          $null = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$parseErrors)
          if ($parseErrors.Count -ne 0) { throw 'Device wrapper PowerShell syntax is invalid' }

"""
        self.assertEqual(step, expected)
        self.assertLess(job.index('          path: candidate\n'), job.index(PARSE_NAME))
        self.assertIn('ref: ${{ needs.dial_source_binding.outputs.candidate_commit }}', job)
        canonical = builder.job((ROOT / '.github/workflows/build.yml').read_text(), 'dial_gate')
        stripped = job.replace(PARSE_NAME + expected, '', 1)
        # Only one new step is allowed; the five-platform matrix stays canonical.
        self.assertEqual(re.findall(r'^      - (?:name:|uses:).*$', stripped, re.M),
                         re.findall(r'^      - (?:name:|uses:).*$', canonical, re.M))
        self.assertEqual(stripped.split('    strategy:\n', 1)[1].split('    steps:\n', 1)[0],
                         canonical.split('    strategy:\n', 1)[1].split('    steps:\n', 1)[0])

    def test_windows_is_only_a_static_parser_after_candidate_checkout(self) -> None:
        self.assert_parse_only(WORKFLOW.read_text())

    def test_windows_missing_duplicate_or_execution_mutations_are_rejected(self) -> None:
        workflow = WORKFLOW.read_text()
        for mutated in (
            workflow.replace(PARSE_NAME, '', 1),
            workflow.replace(PARSE_NAME, PARSE_NAME + PARSE_NAME, 1),
            workflow.replace("        if: matrix.platform == 'windows'\n", '', 1),
            workflow.replace('        shell: pwsh\n', '        shell: bash\n', 1),
            workflow.replace('$null = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$parseErrors)', '& $path', 1),
            workflow.replace("          $tokens = $null", "          . $path\n          $tokens = $null", 1),
        ):
            with self.assertRaises(AssertionError):
                self.assert_parse_only(mutated)

    def test_generator_rejects_missing_or_duplicate_checkout_boundary(self) -> None:
        source = (ROOT / '.github/workflows/build.yml').read_text()
        anchor = '      - name: Checkout dial-gate baseline v1.3.174\n'
        for replacement in ('', anchor + anchor):
            with self.assertRaisesRegex(ValueError, 'checkout boundary changed'):
                builder.generate(source.replace(anchor, replacement, 1))


if __name__ == '__main__':
    unittest.main()
