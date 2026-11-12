"""The native attestation must reach the source-owned completeness validator."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NativeAttestationCompletionTests(unittest.TestCase):
    def execute(self, *, engine_status=0, validator_status=0):
        source = (ROOT / ".github/workflows/source-attestation.yml").read_text()
        step = source.split("      - name: Run macOS-only keychain unit tests\n", 1)[1]
        step = step.split("      - name:", 1)[0]
        script = textwrap.dedent(step.split("        run: |\n", 1)[1])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binaries = root / "bin"
            binaries.mkdir()
            programs = {
                "flutter": "import os,sys\nprint('machine fixture')\nsys.exit(int(os.environ['ENGINE_STATUS']))\n",
                "python3": "import json,os,sys\nfrom pathlib import Path\nPath('validator-args.json').write_text(json.dumps(sys.argv[1:]))\nsys.exit(int(os.environ['VALIDATOR_STATUS']))\n",
            }
            for name, program in programs.items():
                path = binaries / name
                path.write_text(f"#!{sys.executable}\n" + program)
                path.chmod(0o755)
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=root,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                    "RUNNER_TEMP": str(root),
                    "ENGINE_STATUS": str(engine_status),
                    "VALIDATOR_STATUS": str(validator_status),
                },
                check=False,
            )
            path = root / "validator-args.json"
            args = json.loads(path.read_text()) if path.exists() else None
            if args is not None:
                self.assertEqual(args[0], "scripts/ci/verify_native_flutter_report.py")
                self.assertEqual(
                    Path(args[1]).resolve(), (root / "macos-keychain.jsonl").resolve()
                )
                self.assertEqual(args[2], "--source")
                self.assertEqual(Path(args[3]).resolve(), root.resolve())
                self.assertEqual(
                    args[4:], ["--tools", ".source-attestation-tools/scripts"]
                )
            return result, args

    def test_complete_report_reaches_validator(self):
        result, args = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNotNone(args)

    def test_incomplete_native_evidence_cannot_pass_attestation(self):
        result, args = self.execute(validator_status=7)
        self.assertEqual(result.returncode, 7)
        self.assertIsNotNone(args)

    def test_engine_failure_is_not_hidden_by_tee(self):
        result, args = self.execute(engine_status=9)
        self.assertEqual(result.returncode, 9)
        self.assertIsNone(args)
