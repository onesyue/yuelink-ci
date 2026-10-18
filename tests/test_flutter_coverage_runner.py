"""Exercise the real coverage orchestrator with bounded executable fixtures."""
from __future__ import annotations

import json
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
        names = [
            "services/user_custom_rules", "services/subscription_parser",
            "core/storage/persisted_schema", "core/storage/settings_service_schema",
            *[f"services/case_{i}" for i in range(496)],
        ]
        for name in names:
            path = self.root / "test" / (name + "_test.dart")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("void main() {}\n")
        (self.root / "lib").mkdir()
        (self.root / "lib/a.dart").write_text("void fixture() {}\n")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "test", "lib"], cwd=self.root, check=True)
        subprocess.run([
            "git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
            "commit", "-qm", "fixture",
        ], cwd=self.root, check=True)
        # Git worktrees, command execution, protocol parsing, source/cache
        # isolation and failure handling are real. Only expensive engines fake.
        self.executable("timeout", 'import os,sys\nos.execvp(sys.argv[4], sys.argv[4:])\n')
        self.executable("flutter", '''import json,os,sys
from pathlib import Path
args=sys.argv[1:]
if args[:2]==['pub','get']:
    assert args==['pub','get','--offline','--enforce-lockfile']
    sys.exit(8 if os.environ.get('PUBGET_FAILURE') else 0)
assert args[:5]==['test','--no-pub','--coverage','--machine','--concurrency=1']
files=[a for a in args if a.startswith('test/')]
assert len(files)==1
cache=Path('build/test_cache')
assert not cache.exists(), 'prior compiler kernel was reused'
cache.parent.mkdir(exist_ok=True)
if os.environ.get('CACHE_ESCAPE'):
    cache.symlink_to(os.environ['SENTINEL'])
else:
    cache.mkdir()
    (cache/'kernel').write_text('previous compilation')
report=Path(args[args.index('--coverage-path')+1])
if os.environ.get('TRACKED_MUTATION'):
    Path('lib/a.dart').write_text('changed source')
if not os.environ.get('MISSING_COVERAGE'):
    report.write_text('SF:'+('/outside/a.dart' if os.environ.get('COVERAGE_ESCAPE') else str(Path.cwd()/'lib/a.dart'))+'\\nDA:1,1\\nend_of_record\\n')
if os.environ.get('ALL_EMPTY_COVERAGE') or (os.environ.get('EMPTY_COVERAGE') and report.stem=='7'):
    report.write_text('')
print(json.dumps({'type':'start'}))
count=1 if os.environ.get('LOW_COUNT') else 5
for i in range(count):
    print(json.dumps({'type':'testStart','test':{'id':i,'name':files[0]},'worker':str(Path.cwd())}))
if not os.environ.get('MISSING_DONE'): print(json.dumps({'type':'done','success':True}))
if os.environ.get('FLUTTER_FAILURE') or os.environ.get('FAIL_GROUP')==report.stem: sys.exit(7)
''')
        self.executable("lcov", '''import os,sys
from pathlib import Path
expected=499 if os.environ.get('EMPTY_COVERAGE') else 500
assert sys.argv.count('--add-tracefile')==expected
reports=[Path(sys.argv[i+1]) for i,a in enumerate(sys.argv) if a=='--add-tracefile']
assert len(set(reports))==expected and all(p.is_file() for p in reports)
assert all(p.read_text().startswith('SF:lib/a.dart\\n') for p in reports)
if os.environ.get('MERGE_FAILURE'): sys.exit(9)
Path(sys.argv[-1]).write_text('SF:lib/a.dart\\nDA:1,500\\nend_of_record\\n')
''')

    def executable(self, name, body):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o755)

    def run_gate(self, **overrides):
        env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"], **overrides)
        return subprocess.run([sys.executable, str(ROOT / "scripts/run-flutter-coverage.py")],
                              cwd=self.root, env=env, text=True, capture_output=True, timeout=40)

    def test_complete_corpus_executes_once_with_independent_sources_and_kernels(self):
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = self.root / "coverage/source-gate"
        groups = json.loads((evidence / "inventory.json").read_text())
        self.assertEqual([len(group) for group in groups], [1] * 500)
        actual = []
        workers = set()
        for report in evidence.glob("*.jsonl"):
            rows = [json.loads(line) for line in report.read_text().splitlines()]
            first = next(row for row in rows if row["type"] == "testStart")
            actual.append(first["test"]["name"])
            workers.add(first["worker"])
        self.assertEqual(sorted(actual), sorted(group[0] for group in groups))
        self.assertEqual(len(set(actual)), 500)
        self.assertEqual(len(workers), 4)
        self.assertNotIn(str(self.root), workers)
        self.assertIn("Flutter tests observed: 2500", result.stdout)
        self.assertTrue((self.root / "coverage/lcov.info").is_file())
        for worker in workers:
            self.assertFalse(Path(worker).exists())

    def test_every_failed_engine_protocol_dependency_or_merge_blocks_acceptance(self):
        for failure in ["FLUTTER_FAILURE", "MISSING_DONE", "MISSING_COVERAGE",
                        "LOW_COUNT", "MERGE_FAILURE", "PUBGET_FAILURE",
                        "COVERAGE_ESCAPE", "TRACKED_MUTATION", "ALL_EMPTY_COVERAGE"]:
            with self.subTest(failure=failure):
                shutil.rmtree(self.root / "coverage", ignore_errors=True)
                result = self.run_gate(**{failure: "1"})
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Flutter tests observed:", result.stdout)

    def test_source_only_contract_keeps_empty_report_and_merges_all_executed_lines(self):
        result = self.run_gate(EMPTY_COVERAGE="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = self.root / "coverage/source-gate"
        self.assertEqual((evidence / "7.info").read_text(), "")
        self.assertEqual(len(list(evidence.glob("*.jsonl"))), 500)
        self.assertTrue((self.root / "coverage/lcov.info").is_file())

    def test_late_failure_preserves_prior_evidence_and_never_merges(self):
        result = self.run_gate(FAIL_GROUP="8")
        self.assertNotEqual(result.returncode, 0)
        evidence = self.root / "coverage/source-gate"
        self.assertTrue((evidence / "4.info").is_file())
        self.assertTrue((evidence / "8.jsonl").is_file())
        self.assertFalse((evidence / "499.jsonl").exists())
        self.assertFalse((self.root / "coverage/lcov.info").exists())

    def test_cache_symlink_refuses_before_touching_its_target(self):
        sentinel = self.root / "outside"
        sentinel.mkdir()
        (sentinel / "keep").write_text("preserve")
        result = self.run_gate(CACHE_ESCAPE="1", SENTINEL=str(sentinel))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cache escaped", result.stderr)
        self.assertEqual((sentinel / "keep").read_text(), "preserve")
        self.assertFalse((self.root / "coverage/lcov.info").exists())

    def test_macos_file_is_assigned_to_required_native_job_without_counting_as_linux_pass(self):
        name = "test/core/storage/secure_storage_macos_keychain_test.dart"
        (self.root / name).write_text("@TestOn('mac-os')\nlibrary;\nvoid main() {}\n")
        subprocess.run(["git", "add", name], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
                        "commit", "-qm", "native macOS assignment"], cwd=self.root, check=True)
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = self.root / "coverage/source-gate"
        self.assertEqual(len(json.loads((evidence / "inventory.json").read_text())), 501)
        self.assertEqual(json.loads((evidence / "platform-assignments.json").read_text())[name], "macos_integration")
        self.assertEqual(len(list(evidence.glob("*.jsonl"))), 500)
        self.assertIn("Flutter tests observed: 2500", result.stdout)
        workflow = (ROOT / ".github/workflows/source-attestation.yml").read_text()
        native = workflow.split("  macos_integration:", 1)[1].split("  windows_durability:", 1)[0]
        self.assertIn("flutter test --no-pub --machine " + name, native)
        self.assertIn("macos-keychain.jsonl --minimum 4", native)
        self.assertIn("set -euo pipefail", native)

    def test_unassigned_platform_annotation_refuses(self):
        (self.root / "test/services/case_0_test.dart").write_text("@TestOn('windows')\nvoid main() {}\n")
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unassigned platform-specific test", result.stderr)

    def test_untracked_test_cannot_be_silently_omitted(self):
        (self.root / "test/extra_test.dart").write_text("void main() {}")
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("test inventory differs", result.stderr)

    def test_missing_required_case_cannot_reduce_the_suite(self):
        (self.root / "test/services/user_custom_rules_test.dart").unlink()
        subprocess.run(["git", "add", "-u"], cwd=self.root, check=True)
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("scan floor", result.stderr)

    def test_workflow_uses_exact_checked_out_runner_and_keeps_ratchet(self):
        workflow = (ROOT / ".github/workflows/source-attestation.yml").read_text()
        self.assertIn("ref: $" + "{{ github.sha }}", workflow)
        self.assertIn("python3 .source-attestation-tools/scripts/run-flutter-coverage.py", workflow)
        self.assertIn("dart run tool/automation/coverage_ratchet.dart --check", workflow)

    def test_missing_required_case_fails_even_when_file_floor_is_met(self):
        path = self.root / "test/services/user_custom_rules_test.dart"
        path.rename(path.with_name("replacement_test.dart"))
        subprocess.run(["git", "add", "-A", "test"], cwd=self.root, check=True)
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required crash cases missing", result.stderr)

    def test_symlink_cannot_substitute_for_an_independent_test_file(self):
        path = self.root / "test/services/case_0_test.dart"
        path.unlink()
        path.symlink_to("case_1_test.dart")
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("test paths are symlinks", result.stderr)


if __name__ == "__main__":
    unittest.main()
