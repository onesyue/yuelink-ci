"""Exercise the real coverage orchestrator with bounded executable fixtures."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CoverageRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        tests = self.root / "test/services"
        tests.mkdir(parents=True)
        names = ["user_custom_rules", "subscription_parser"] + [f"case_{i}" for i in range(498)]
        for name in names:
            (tests / f"{name}_test.dart").write_text("void main() {}\n")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "test"], cwd=self.root, check=True)
        # Exercise the actual subprocess path, protocol parser and coverage merge
        # failure handling. Only the expensive external engines are replaced.
        self.executable("timeout", 'import os,sys\nos.execvp(sys.argv[4], sys.argv[4:])\n')
        self.executable("flutter", '''import json,os,sys
from pathlib import Path
args=sys.argv[1:]
assert args[:5]==['test','--no-pub','--coverage','--machine','--concurrency=2']
files=[a for a in args if a.startswith('test/')]
assert files
report=Path(args[args.index('--coverage-path')+1])
if not os.environ.get('MISSING_COVERAGE'): report.write_text('SF:lib/a.dart\\nDA:1,1\\nend_of_record\\n')
print(json.dumps({'type':'start'}))
count=len(files)*(1 if os.environ.get('LOW_COUNT') else 5)
for i in range(count): print(json.dumps({'type':'testStart','test':{'id':i}}))
if not os.environ.get('MISSING_DONE'): print(json.dumps({'type':'done','success':True}))
if os.environ.get('FLUTTER_FAILURE'): sys.exit(7)
''')
        self.executable("lcov", '''import os,sys
from pathlib import Path
assert sys.argv.count('--add-tracefile')==2
assert all(Path(sys.argv[i+1]).is_file() for i,a in enumerate(sys.argv) if a=='--add-tracefile')
if os.environ.get('MERGE_FAILURE'): sys.exit(9)
Path(sys.argv[-1]).write_text('SF:lib/a.dart\\nDA:1,2\\nend_of_record\\n')
''')

    def executable(self, name, body):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o755)

    def run_gate(self, **overrides):
        env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"], **overrides)
        return subprocess.run([sys.executable, str(ROOT / "scripts/run-flutter-coverage.py")],
                              cwd=self.root, env=env, text=True, capture_output=True, timeout=20)

    def test_all_files_execute_once_and_both_protocols_count(self):
        import json
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        groups = json.loads((self.root / 'coverage/source-gate/inventory.json').read_text())
        self.assertEqual([len(g) for g in groups], [2, 498])
        self.assertEqual(len(set(p for g in groups for p in g)), 500)
        self.assertIn('Flutter tests observed: 2500', result.stdout)
        self.assertTrue((self.root / 'coverage/lcov.info').is_file())

    def test_every_failed_engine_protocol_or_merge_blocks_acceptance(self):
        for failure in ['FLUTTER_FAILURE', 'MISSING_DONE', 'MISSING_COVERAGE', 'LOW_COUNT', 'MERGE_FAILURE']:
            with self.subTest(failure=failure):
                shutil.rmtree(self.root / 'coverage', ignore_errors=True)
                result = self.run_gate(**{failure: '1'})
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('Flutter tests observed:', result.stdout)

    def test_untracked_test_cannot_be_silently_omitted(self):
        (self.root / 'test/extra_test.dart').write_text('void main() {}')
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('test inventory differs', result.stderr)

    def test_missing_isolated_file_cannot_reduce_the_suite(self):
        path = self.root / 'test/services/user_custom_rules_test.dart'
        path.unlink()
        subprocess.run(['git', 'add', '-u'], cwd=self.root, check=True)
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('scan floor', result.stderr)

    def test_workflow_uses_exact_checked_out_runner_and_keeps_ratchet(self):
        workflow = (ROOT / '.github/workflows/source-attestation.yml').read_text()
        self.assertIn('ref: ${{ github.sha }}', workflow)
        self.assertIn('python3 .source-attestation-tools/scripts/run-flutter-coverage.py', workflow)
        self.assertIn('dart run tool/automation/coverage_ratchet.dart --check', workflow)

    def test_missing_isolated_case_fails_even_when_file_floor_is_met(self):
        path = self.root / 'test/services/user_custom_rules_test.dart'
        path.rename(path.with_name('replacement_test.dart'))
        subprocess.run(['git', 'add', '-A'], cwd=self.root, check=True)
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('isolated tests missing', result.stderr)

    def test_symlink_cannot_substitute_for_an_independent_test_file(self):
        path = self.root / 'test/services/case_0_test.dart'
        path.unlink()
        path.symlink_to('case_1_test.dart')
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('test paths are symlinks', result.stderr)


if __name__ == '__main__':
    unittest.main()
