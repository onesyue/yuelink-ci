"""Exercise the real coverage orchestrator with bounded executable fixtures."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("coverage_runner", ROOT / "scripts/run-flutter-coverage.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)



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
            "governance/mihomo_fork_ledger",
            "core/storage/subscription_reset_operation_store", "core/storage/tun_datapath_accel_latch",
            *[f"services/case_{i}" for i in range(493)],
        ]
        for name in names:
            path = self.root / "test" / (name + "_test.dart")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("void main() {}\n")
        (self.root / "lib").mkdir()
        (self.root / "lib/a.dart").write_text("void fixture() {}\n")
        (self.root / "pubspec.lock").write_text("fixture lock\n")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "test", "lib", "pubspec.lock"], cwd=self.root, check=True)
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
if args==['--version','--machine']:
    print(json.dumps({k:('wrong' if os.environ.get('WRONG_SDK') else 'fixture') for k in ['frameworkVersion','frameworkRevision','engineRevision','dartSdkVersion']})); sys.exit(0)
if args[:2]==['pub','get']:
    assert args==['pub','get','--offline','--enforce-lockfile']
    sys.exit(8 if os.environ.get('PUBGET_FAILURE') else 0)
assert args[:5]==['test','--no-pub','--coverage','--machine','--concurrency=1']
files=[a for a in args if a.startswith('test/')]
assert 1<=len(files)<=8
if os.environ.get('SUBMODULE_REQUIRED'):
    assert Path('core/mihomo/go.mod').read_text()=='module fixture\\n'
    assert Path('core/mihomo/dependency/leaf.txt').read_text()=='pinned dependency\\n'
    if os.environ.get('SUBMODULE_MUTATION'):
        Path('core/mihomo/go.mod').write_text('mutated')
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
if os.environ.get('COVERAGE_LOSS') and report.is_file(): report.write_text(report.read_text().replace('DA:1,1','DA:2,1'))
if os.environ.get('ALL_EMPTY_COVERAGE') or (os.environ.get('EMPTY_COVERAGE') and report.stem=='7'):
    report.write_text('')
print(json.dumps({'type':'start'}))
count=1 if os.environ.get('LOW_COUNT') else 5
if os.environ.get('OMIT_SECOND') and len(files)>1: files.pop(1)
if os.environ.get('OMIT_FILE'): files=[]
if os.environ.get('UNEXPECTED_FILE'): files[0]='test/unexpected_test.dart'
for suite,path in enumerate(files):
    record={'type':'suite','suite':{'id':suite,'path':str(Path.cwd()/path)},'worker':str(Path.cwd())}
    print(json.dumps(record))
    if os.environ.get('DUPLICATE_SUITE'): print(json.dumps(record))
    for case in range(count):
        i=suite*10+case
        record={'type':'testStart','test':{'id':i,'suiteID':suite,'name':f'case {case}'}}
        print(json.dumps(record))
        if os.environ.get('DUPLICATE_START'): print(json.dumps(record))
        record={'type':'testDone','testID':i,'result':'failure' if os.environ.get('FAILED_TERMINAL') else 'success','hidden':bool(os.environ.get('HIDE_CASE')),'skipped':bool(os.environ.get('CHANGED_SKIP')) or bool(os.environ.get('SKIP_ONE') and report.stem=='0' and case==0)}
        if not os.environ.get('MISSING_TERMINAL'): print(json.dumps(record))
        if os.environ.get('DUPLICATE_TERMINAL'): print(json.dumps(record))
if not os.environ.get('MISSING_DONE'): print(json.dumps({'type':'done','success':True}))
if os.environ.get('CANCEL_ENGINE'): os.kill(os.getpid(),15)
if os.environ.get('FLUTTER_FAILURE') or os.environ.get('FAIL_GROUP')==report.stem: sys.exit(7)
''')
        self.executable("lcov", '''import os,sys
from pathlib import Path
expected=int(os.environ['FIXTURE_GROUPS']) - bool(os.environ.get('EMPTY_COVERAGE'))
assert sys.argv.count('--add-tracefile')==expected
reports=[Path(sys.argv[i+1]) for i,a in enumerate(sys.argv) if a=='--add-tracefile']
assert len(set(reports))==expected and all(p.is_file() for p in reports)
assert all(p.read_text().startswith('SF:lib/a.dart\\n') for p in reports)
if os.environ.get('MERGE_FAILURE'): sys.exit(9)
Path(sys.argv[-1]).write_text('SF:lib/a.dart\\nDA:1,500\\nend_of_record\\n')
''')

        self.tools = self.root / "tools"
        (self.tools / "scripts").mkdir(parents=True)
        (self.tools / "tests/fixtures").mkdir(parents=True)
        for name in ("run-flutter-coverage.py", "count-flutter-machine-tests.py"):
            shutil.copyfile(ROOT / "scripts" / name, self.tools / "scripts" / name)
        self.plan_path = self.tools / "tests/fixtures/flutter-coverage-groups-20261020.json"
        self.refresh_plan()
        # Only the expensive engine and resource capacity are fixture controls.
        # The runner, Git worktrees, plan/protocol/coverage checks execute unchanged.
        self.entry = self.tools / "fixture-entry.py"
        self.entry.write_text("""import importlib.util,json,os
from pathlib import Path
p=Path(__file__).parent/'scripts/run-flutter-coverage.py'
s=importlib.util.spec_from_file_location('runner',p)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
m.resource_snapshot=lambda:json.loads(os.environ['FIXTURE_RESOURCES'])
m.GROUPED_PLAN_SHA256=os.environ['FIXTURE_GROUPED_PLAN_SHA256']
m.main()
""")

    def refresh_plan(self):
        inventory = RUNNER.partition(self.root)
        groups, batch = [], []
        for (path,) in inventory:
            if path == RUNNER.MACOS_TEST:
                continue
            if path in RUNNER.ISOLATED:
                if batch:
                    groups.append(batch)
                    batch = []
                groups.append([path])
            else:
                batch.append(path)
                if len(batch) == 8:
                    groups.append(batch)
                    batch = []
        if batch:
            groups.append(batch)
        rows = [[path, f"case {i}", False, 1] for (path,) in inventory
                if path != RUNNER.MACOS_TEST for i in range(5)]
        self.plan = {"schema": 1, "files": {path: hashlib.sha256((self.root / path).read_bytes()).hexdigest()
                                          for (path,) in inventory},
                     "groups": groups, "isolated": sorted(RUNNER.ISOLATED), "macos_test": RUNNER.MACOS_TEST,
                     "runtime_files": {"pubspec.lock": hashlib.sha256((self.root / "pubspec.lock").read_bytes()).hexdigest()}, "lib_tree": subprocess.check_output(
                         ["git", "rev-parse", "HEAD:lib"], cwd=self.root, text=True).strip(),
                     "sdk": {key: "fixture" for key in ("frameworkVersion", "frameworkRevision", "engineRevision", "dartSdkVersion")},
                     "baseline_union": {"cases_sha256": RUNNER.digest(sorted(rows)),
                                        "coverage_sha256": RUNNER.digest([
                                            ("lib/a.dart", "DA", "1", False), ("lib/a.dart", "DA", "1", True)]),
                                        "visible_cases": len(rows), "visible_skips": 0, "coverage_rows": 2}}
        self.plan_path.write_text(json.dumps(self.plan))
        self.grouped_digest = hashlib.sha256(self.plan_path.read_bytes()).hexdigest()

    def executable(self, name, body):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o755)

    def run_gate(self, **overrides):
        env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                   FIXTURE_GROUPS=str(len(self.plan["groups"])),
                   FIXTURE_GROUPED_PLAN_SHA256=self.grouped_digest,
                   FIXTURE_RESOURCES=json.dumps({"effective_cpus": 4, "available_memory_bytes": 16 * RUNNER.GIB, "capacity_status": "VISIBLE_BOUNDS", "missing_interfaces": []}))
        env.pop("YUELINK_COVERAGE_MAX_WORKERS", None)
        env.update(overrides)
        return subprocess.run([sys.executable, str(self.entry)],
                              cwd=self.root, env=env, text=True, capture_output=True, timeout=40, check=False)

    def reviewed_filename_corpus(self):
        # Use every formal filename and group with source-bound fixture bytes.
        # Only the expensive engine, coverage payload and capacity are synthetic.
        reviewed = json.loads(RUNNER.PLAN.read_text())
        shutil.rmtree(self.root / "test")
        for name in reviewed["files"]:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(("@TestOn('mac-os')\nlibrary;\n" if name == RUNNER.MACOS_TEST else "")
                            + "void main() {}\n")
        subprocess.run(["git", "add", "--", "test"], cwd=self.root, check=True)
        self.commit_fixture(self.root, "complete reviewed filename corpus")
        self.refresh_plan()
        self.plan["groups"] = reviewed["groups"]
        self.plan_path.write_text(json.dumps(self.plan))
        self.grouped_digest = hashlib.sha256(self.plan_path.read_bytes()).hexdigest()
        return reviewed

    def test_complete_corpus_executes_once_with_independent_sources_and_kernels(self):
        reviewed = self.reviewed_filename_corpus()
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = self.root / "coverage/source-gate"
        groups = json.loads((evidence / "inventory.json").read_text())
        self.assertEqual([len(group) for group in groups], [1] * 509)
        actual = []
        workers = set()
        for report in evidence.glob("*.jsonl"):
            rows = [json.loads(line) for line in report.read_text().splitlines()]
            for row in rows:
                if row["type"] == "suite":
                    actual.append(str(Path(row["suite"]["path"]).relative_to(row["worker"])))
                    workers.add(row["worker"])
        self.assertEqual(sorted(actual), sorted(group[0] for group in groups
                                                if group[0] != RUNNER.MACOS_TEST))
        self.assertEqual(len(set(actual)), 508)
        self.assertEqual(len(workers), 4)
        receipt = json.loads((evidence / "execution.json").read_text())
        self.assertEqual(receipt["status"], "COMPLETE")
        self.assertEqual(receipt["groups"], self.plan["groups"])
        self.assertEqual(len(receipt["groups"]), 109)
        for index, assigned in enumerate(reviewed["groups"]):
            command = json.loads((evidence / f"{index}.execution.json").read_text())
            self.assertEqual([arg for arg in command["command"] if arg.startswith("test/")], assigned)
            self.assertEqual(command["exit_code"], 0)
            self.assertTrue((evidence / f"{index}.info").is_file())
        self.assertIsNone(receipt["aggregate_owned_job_peak_bytes"])
        assignments = json.loads((evidence / "platform-assignments.json").read_text())
        self.assertEqual(set(assignments), set(actual) | {RUNNER.MACOS_TEST})
        self.assertEqual(assignments[RUNNER.MACOS_TEST], "macos_integration")
        self.assertNotIn(str(self.root), workers)
        self.assertIn("Flutter tests observed: 2540", result.stdout)
        self.assertTrue((self.root / "coverage/lcov.info").is_file())
        for worker in workers:
            self.assertFalse(Path(worker).exists())

    def materialize_singletons(self):
        inventory = RUNNER.partition(self.root)
        self.singleton_path = self.tools / "tests/fixtures/flutter-coverage-singletons-20261022-v148.json"
        self.singleton = {
            "schema": 1, "execution_mode": "singletons",
            "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip(),
            "feature_ancestor": self.feature_ancestor,
            "source_repository": "onesyue/yuelink",
            "files": {path: hashlib.sha256((self.root / path).read_bytes()).hexdigest()
                      for (path,) in inventory},
            "lib_tree": subprocess.check_output(["git", "rev-parse", "HEAD:lib"], cwd=self.root, text=True).strip(),
            "runtime_files": dict(self.plan["runtime_files"]), "sdk": dict(self.plan["sdk"]),
            "macos_test": RUNNER.MACOS_TEST,
            "groups": [group for group in inventory if group != [RUNNER.MACOS_TEST]],
        }
        self.singleton_path.write_text(json.dumps(self.singleton))

    def admitted_singleton_corpus(self):
        self.reviewed_filename_corpus()
        new_test = self.root / "test/modules/auth_login_single_submission_test.dart"
        new_test.write_text("void main() { /* newly admitted login contract */ }\n")
        (self.root / "lib/a.dart").write_text("void changedLogin() {}\n")
        subprocess.run(["git", "add", "test", "lib"], cwd=self.root, check=True)
        self.commit_fixture(self.root, "admitted feature source")
        self.feature_ancestor = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()
        (self.root / "pubspec.yaml").write_text("version: 2.0.0+2\n")
        subprocess.run(["git", "add", "pubspec.yaml"], cwd=self.root, check=True)
        self.commit_fixture(self.root, "final version metadata")
        self.materialize_singletons()

    def run_singletons(self, **overrides):
        return self.run_gate(**({"FIXTURE_GROUPS": str(len(self.singleton["groups"]))} | overrides))

    def predict(self):
        with patch.object(RUNNER, "PLAN", self.plan_path), \
             patch.object(RUNNER, "GROUPED_PLAN_SHA256", self.grouped_digest):
            return RUNNER.execution_plan(self.root, RUNNER.partition(self.root))

    def run_reuse(self, **overrides):
        _, groups, mode, _ = self.predict()
        self.assertEqual(mode, "reviewed_group_reuse")
        return self.run_gate(**({"FIXTURE_GROUPS": str(len(groups))} | overrides))

    def test_admitted_final_source_reuses_exact_groups_and_isolates_the_new_file(self):
        self.admitted_singleton_corpus()
        self.assertNotEqual(self.singleton["source_sha"], self.feature_ancestor)
        # The v148-style singleton document still matches this tree. It must
        # not force one compiler process per unchanged reviewed group.
        _, predicted, mode, _ = self.predict()
        self.assertEqual(mode, "reviewed_group_reuse")
        result = self.run_reuse()
        self.assertEqual(result.returncode, 0, result.stderr)
        out = self.root / "coverage/source-gate"
        receipt = json.loads((out / "execution.json").read_text())
        self.assertEqual(receipt["execution_mode"], "reviewed_group_reuse")
        self.assertEqual(receipt["union_comparison"], RUNNER.UNION_COMPARISON["reviewed_group_reuse"])
        self.assertEqual(receipt["plan_sha256"], self.grouped_digest)
        self.assertEqual(receipt["source_sha"], self.singleton["source_sha"])
        self.assertEqual(receipt["status"], "COMPLETE")
        self.assertTrue(receipt["tests_complete"] and receipt["cleanup_complete"])
        self.assertEqual(receipt["cleanup_errors"], [])
        self.assertEqual(receipt["reused_exact_groups"], 109)
        self.assertEqual(receipt["subset_groups"], 0)
        self.assertEqual(receipt["singleton_files"], 1)
        self.assertEqual(len(receipt["groups"]), 110)
        self.assertEqual(predicted, receipt["groups"])
        expected = receipt["groups"]
        self.assertTrue(all(command in self.plan["groups"] or len(command) == 1 for command in expected))
        self.assertIn(["test/modules/auth_login_single_submission_test.dart"], expected)
        executed, workers = [], set()
        for index, group in enumerate(expected):
            command = json.loads((out / f"{index}.execution.json").read_text())
            self.assertEqual(command["files"], group)
            self.assertEqual([arg for arg in command["command"] if arg.startswith("test/")], group)
            self.assertEqual(command["exit_code"], 0)
            rows = [json.loads(line) for line in (out / f"{index}.jsonl").read_text().splitlines()]
            for row in rows:
                if row["type"] == "suite":
                    executed.append(str(Path(row["suite"]["path"]).relative_to(row["worker"])))
                    workers.add(row["worker"])
        self.assertEqual(sorted(executed), sorted(path for group in expected for path in group))
        self.assertEqual(sorted(executed), sorted(group[0] for group in self.singleton["groups"]))
        self.assertEqual(len(set(executed)), 509)
        self.assertIn("test/modules/auth_login_single_submission_test.dart", executed)
        assignments = json.loads((out / "platform-assignments.json").read_text())
        self.assertEqual(len(assignments), 510)
        self.assertEqual(assignments[RUNNER.MACOS_TEST], "macos_integration")
        self.assertEqual(receipt["union"]["visible_cases"], 2545)
        self.assertEqual(receipt["union"]["visible_skips"], 0)
        self.assertTrue((self.root / "coverage/lcov.info").is_file())
        self.assertEqual(len(workers), 4)
        self.assertTrue(all(not Path(worker).exists() for worker in workers))
        self.assertEqual(hashlib.sha256(self.plan_path.read_bytes()).hexdigest(), self.grouped_digest)

    def test_corrupt_singleton_sidecar_cannot_select_execution(self):
        self.admitted_singleton_corpus()
        self.singleton_path.write_text("{")
        result = self.run_reuse()
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads((self.root / "coverage/source-gate/execution.json").read_text())
        self.assertEqual(receipt["execution_mode"], "reviewed_group_reuse")
        self.assertEqual(receipt["reused_exact_groups"], 109)
        self.assertEqual(receipt["singleton_files"], 1)

    def test_lock_mismatch_and_broken_grouped_control_refuse_before_commands(self):
        self.admitted_singleton_corpus()
        self.plan_path.write_text("{}")
        shutil.rmtree(self.root / "coverage", ignore_errors=True)
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reviewed grouping control digest mismatch", result.stderr)
        self.assertFalse(list((self.root / "coverage/source-gate").glob("*.jsonl")))
        self.plan_path.write_text(json.dumps(self.plan))
        (self.root / "pubspec.lock").write_text("unbound lock\n")
        subprocess.run(["git", "add", "pubspec.lock"], cwd=self.root, check=True)
        self.commit_fixture(self.root, "lock no longer matches the reviewed groups")
        shutil.rmtree(self.root / "coverage", ignore_errors=True)
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("singleton source/inventory/runtime plan mismatch", result.stderr)
        self.assertFalse(list((self.root / "coverage/source-gate").glob("*.jsonl")))

    def test_version_metadata_keeps_the_full_reused_corpus(self):
        self.admitted_singleton_corpus()
        before = [path for group in self.predict()[1] for path in group]
        (self.root / "pubspec.yaml").write_text("version: 2.0.1+3\n")
        subprocess.run(["git", "add", "pubspec.yaml"], cwd=self.root, check=True)
        self.commit_fixture(self.root, "later version metadata")
        result = self.run_reuse()
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads((self.root / "coverage/source-gate/execution.json").read_text())
        self.assertEqual([path for group in receipt["groups"] for path in group], before)
        # Reach an intentionally failed first engine command after explicit
        # rematerialization. This is not another full-corpus pass claim.
        result = self.run_singletons(FAIL_GROUP="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.root / "coverage/source-gate/0.jsonl").is_file())

    def test_singleton_actual_source_drift_never_selects_a_reduced_corpus(self):
        self.admitted_singleton_corpus()
        source = self.singleton["source_sha"]
        test = self.root / "test/modules/auth_login_single_submission_test.dart"
        for fault in ("untracked", "required", "symlink", "annotation", "dirty_lib", "lib", "test", "lock"):
            with self.subTest(fault=fault):
                if fault == "untracked": (test.parent / "untracked_test.dart").write_text("void main() {}")
                elif fault == "required": (self.root / sorted(RUNNER.REQUIRED_TESTS)[0]).unlink()
                elif fault == "symlink":
                    test.unlink()
                    test.symlink_to(self.root / sorted(RUNNER.REQUIRED_TESTS)[0])
                elif fault == "annotation": test.write_text("@TestOn('windows')\nlibrary;\nvoid main() {}")
                elif fault in ("dirty_lib", "lib"): (self.root / "lib/a.dart").write_text("void unboundRuntime() {}")
                elif fault == "test": test.write_text("void main() { /* unbound change */ }")
                elif fault == "lock": (self.root / "pubspec.lock").write_text("unbound lock")
                if fault in ("lib", "test", "lock"):
                    subprocess.run(["git", "add", "test", "lib", "pubspec.lock"], cwd=self.root, check=True)
                    self.commit_fixture(self.root, "unbound source drift")
                shutil.rmtree(self.root / "coverage", ignore_errors=True)
                if fault in ("lib", "test"):
                    result = self.run_reuse()
                    self.assertEqual(result.returncode, 0, result.stderr)
                    receipt = json.loads((self.root / "coverage/source-gate/execution.json").read_text())
                    flat = [path for group in receipt["groups"] for path in group]
                    linux = [path for (path,) in RUNNER.partition(self.root) if path != RUNNER.MACOS_TEST]
                    self.assertEqual(sorted(flat), sorted(linux))
                    self.assertEqual(len(flat), len(linux))
                else:
                    result = self.run_gate()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(list((self.root / "coverage/source-gate").glob("*.jsonl")))
                subprocess.run(["git", "reset", "--hard", source], cwd=self.root, check=True, capture_output=True)
                (test.parent / "untracked_test.dart").unlink(missing_ok=True)

    def test_singleton_engine_protocol_merge_and_sdk_failures_cannot_pass(self):
        self.admitted_singleton_corpus()
        for fault in ("FLUTTER_FAILURE", "MISSING_DONE", "MISSING_COVERAGE", "LOW_COUNT", "MERGE_FAILURE",
                      "PUBGET_FAILURE", "COVERAGE_ESCAPE", "TRACKED_MUTATION", "ALL_EMPTY_COVERAGE",
                      "CANCEL_ENGINE", "OMIT_FILE", "UNEXPECTED_FILE", "DUPLICATE_SUITE", "DUPLICATE_START",
                      "MISSING_TERMINAL", "DUPLICATE_TERMINAL", "FAILED_TERMINAL", "HIDE_CASE", "WRONG_SDK"):
            with self.subTest(fault=fault):
                shutil.rmtree(self.root / "coverage", ignore_errors=True)
                result = self.run_singletons(**{fault: "1"})
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn("Flutter tests observed:", result.stdout)
                out = self.root / "coverage/source-gate"
                receipt = json.loads((out / "execution.json").read_text())
                self.assertNotEqual(receipt["status"], "COMPLETE")
                self.assertFalse((self.root / "coverage/lcov.info").exists())
                if fault == "WRONG_SDK": self.assertFalse(list(out.glob("*.jsonl")))

    def test_singletons_record_actual_union_and_skips_without_old_baseline_relabeling(self):
        self.admitted_singleton_corpus()
        result = self.run_reuse(SKIP_ONE="1", COVERAGE_LOSS="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = self.root / "coverage/source-gate"
        receipt = json.loads((out / "execution.json").read_text())
        self.assertEqual(receipt["union"]["visible_skips"], 1)
        self.assertNotEqual(receipt["union"], self.plan["baseline_union"])
        self.assertEqual(receipt["union_comparison"], RUNNER.UNION_COMPARISON["reviewed_group_reuse"])
        rows = json.loads((out / "case-union.json").read_text())
        self.assertEqual(sum(row[-1] for row in rows if row[-2]), 1)
        self.assertEqual(sum(row[-1] for row in rows if not row[-2]), 2544)
        self.assertEqual(hashlib.sha256(self.plan_path.read_bytes()).hexdigest(), self.grouped_digest)

    def test_singleton_cleanup_and_gitlink_failures_preserve_primary(self):
        self.admitted_singleton_corpus()
        self.add_recursive_native_fixture()
        self.materialize_singletons()
        result = self.run_reuse(SUBMODULE_REQUIRED="1", SUBMODULE_MUTATION="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / "core/mihomo/go.mod").read_text(), "module fixture\n")
        shutil.rmtree(self.root / "coverage", ignore_errors=True)
        real_git = shutil.which("git")
        self.executable("git", f"""import os,sys
if sys.argv[1:3]==['worktree','remove']: sys.exit(19)
os.execv({real_git!r},[{real_git!r},*sys.argv[1:]])
""")
        result = self.run_reuse(SUBMODULE_REQUIRED="1")
        self.assertNotEqual(result.returncode, 0)
        receipt = json.loads((self.root / "coverage/source-gate/execution.json").read_text())
        self.assertEqual(receipt["status"], "INCOMPLETE")
        self.assertTrue(receipt["tests_complete"])
        self.assertFalse(receipt["cleanup_complete"])
        self.assertTrue(receipt["cleanup_errors"])

    def rejoin_contributors(self, plan):
        self.assertEqual(len(plan["groups"]), 109)
        boundaries = (
            (14, [3, 1, 4], "test/core/kernel/desktop_service_readiness_test.dart"),
            (35, [7, 1], "test/infrastructure/account_subscription_fallback_test.dart"),
            (49, [7, 1], "test/modules/force_upgrade_dialog_test.dart"),
            (60, [3, 1, 4], "test/modules/updater/update_dialog_handoff_test.dart"),
        )
        for start, sizes, contributor in reversed(boundaries):
            groups = plan["groups"][start:start + len(sizes)]
            self.assertEqual([len(group) for group in groups], sizes)
            self.assertEqual(groups[1], [contributor])
            plan["groups"][start:start + len(sizes)] = [[path for group in groups for path in group]]
        return plan

    def test_reviewed_singleton_splits_preserve_all_other_plan_inputs(self):
        plan = self.rejoin_contributors(json.loads(RUNNER.PLAN.read_text()))
        # Recover exact103: all99 other groups and every non-group field remain.
        self.assertEqual(
            hashlib.sha256((json.dumps(plan, indent=2) + "\n").encode()).hexdigest(),
            "876d05655fe79d103bc3d5f47f076bebd06c2114b12b56fe67c4eed8dbe68d19",
        )
        # Rejoin only failed64 and unstarted68/72/76 from the original77 groups.
        # Exact byte recovery also preserves every other group and input field.
        for start, width in reversed(((64, 8), (75, 8), (86, 8), (97, 6))):
            singletons = plan["groups"][start:start + width]
            self.assertEqual([len(group) for group in singletons], [1] * width)
            plan["groups"][start:start + width] = [[group[0] for group in singletons]]
        self.assertEqual(
            hashlib.sha256((json.dumps(plan, indent=2) + "\n").encode()).hexdigest(),
            "32d606bff6219109538421ab9d1f66ba937dd054b16d1984a61b6b33a8132646",
        )
        original_group = json.loads(
            (ROOT / "tests/fixtures/flutter-coverage-group25-20261020.json").read_text()
        )["files"]
        self.assertEqual(len(plan["groups"]), 77)
        self.assertEqual(plan["groups"][25:33], [[path] for path in original_group])
        # Rejoin only this boundary and recover the exact prior reviewed plan:
        # every file/blob, other group, native assignment and union stays fixed.
        plan["groups"][25:33] = [original_group]
        self.assertEqual(
            hashlib.sha256((json.dumps(plan, indent=2) + "\n").encode()).hexdigest(),
            "c7ec33a96ff89f22c8bad3a55b200231324b27bd7d4e010205a45aa21208252e",
        )

    def test_changed_member_stays_single_and_neighbors_remain_a_subset(self):
        self.reviewed_filename_corpus()
        original = [list(group) for group in json.loads(self.plan_path.read_text())["groups"]]
        group = next(item for item in original
                     if len(item) > 1 and all(path not in RUNNER.ISOLATED for path in item))
        changed = group[0]
        path = self.root / changed
        path.write_text(path.read_text() + "void changedMember() {}\n")
        subprocess.run(["git", "add", "--", changed], cwd=self.root, check=True)
        self.commit_fixture(self.root, "one reviewed member changed")
        _, commands, mode, _ = self.predict()
        self.assertEqual(mode, "reviewed_group_reuse")
        self.assertIn(group[1:], commands)
        self.assertIn([changed], commands)
        self.assertNotIn(group, commands)
        self.assertTrue(all(changed not in command or command == [changed] for command in commands))
        for item in original:
            if item != group:
                self.assertIn(item, commands)
        flat = [item for command in commands for item in command]
        linux = [item for (item,) in RUNNER.partition(self.root) if item != RUNNER.MACOS_TEST]
        self.assertEqual(sorted(flat), sorted(linux))
        self.assertEqual(len(flat), len(set(flat)))

    def test_contributor_isolation_contract_and_actual_order_refusal(self):
        self.reviewed_filename_corpus()
        original = json.loads(self.plan_path.read_text())
        inventory = RUNNER.partition(self.root)
        with patch.object(RUNNER, "PLAN", self.plan_path):
            # Regrouping remains structurally valid to the unchanged runner,
            # but cannot satisfy the new reviewed contributor isolation contract.
            regrouped = json.loads(json.dumps(original))
            regrouped["groups"][14:16] = [[path for group in regrouped["groups"][14:16] for path in group]]
            self.plan_path.write_text(json.dumps(regrouped))
            _, groups = RUNNER.grouped_plan(self.root, inventory)
            self.assertEqual(len(groups), 108)
            with self.assertRaises(AssertionError):
                self.rejoin_contributors(regrouped)
            reordered = json.loads(json.dumps(original))
            reordered["groups"][14], reordered["groups"][15] = reordered["groups"][15], reordered["groups"][14]
            self.plan_path.write_text(json.dumps(reordered))
            with self.assertRaisesRegex(ValueError, "grouping inventory/blob/isolation plan mismatch"):
                RUNNER.grouped_plan(self.root, inventory)

    def test_every_failed_engine_protocol_dependency_or_merge_blocks_acceptance(self):
        for failure in ["FLUTTER_FAILURE", "MISSING_DONE", "MISSING_COVERAGE",
                        "LOW_COUNT", "MERGE_FAILURE", "PUBGET_FAILURE",
                        "COVERAGE_ESCAPE", "TRACKED_MUTATION", "ALL_EMPTY_COVERAGE", "COVERAGE_LOSS", "CANCEL_ENGINE"]:
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
        self.assertEqual(len(list(evidence.glob("*.jsonl"))), len(self.plan["groups"]))
        self.assertTrue((self.root / "coverage/lcov.info").is_file())

    def test_late_failure_preserves_prior_evidence_and_never_merges(self):
        result = self.run_gate(FAIL_GROUP="8")
        self.assertNotEqual(result.returncode, 0)
        evidence = self.root / "coverage/source-gate"
        self.assertTrue((evidence / "4.info").is_file())
        self.assertTrue((evidence / "8.jsonl").is_file())
        self.assertFalse((evidence / f"{len(self.plan['groups']) - 1}.jsonl").exists())
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
        self.refresh_plan()
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = self.root / "coverage/source-gate"
        self.assertEqual(len(json.loads((evidence / "inventory.json").read_text())), 501)
        self.assertEqual(json.loads((evidence / "platform-assignments.json").read_text())[name], "macos_integration")
        self.assertEqual(len(list(evidence.glob("*.jsonl"))), len(self.plan["groups"]))
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

    def commit_fixture(self, repo, message):
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
                        "commit", "-qm", message], cwd=repo, check=True)

    def add_recursive_native_fixture(self):
        leaf = self.root / "leaf-source"
        parent = self.root / "native-source"
        for repo, name, value in [(leaf, "leaf.txt", "pinned dependency\n"),
                                  (parent, "go.mod", "module fixture\n")]:
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            (repo / name).write_text(value)
            subprocess.run(["git", "add", name], cwd=repo, check=True)
            self.commit_fixture(repo, "native source")
        subprocess.run(["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                        str(leaf), "dependency"], cwd=parent, check=True)
        self.commit_fixture(parent, "nested pinned dependency")
        subprocess.run(["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                        str(parent), "core/mihomo"], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "protocol.file.allow=always", "submodule", "update",
                        "--init", "--recursive", "--quiet"], cwd=self.root, check=True)
        self.commit_fixture(self.root, "pinned native Gitlink")

    def test_recursive_gitlinks_execute_from_owned_exact_worktrees(self):
        self.add_recursive_native_fixture()
        result = self.run_gate(SUBMODULE_REQUIRED="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        for repo in [self.root / "core/mihomo", self.root / "core/mihomo/dependency"]:
            listing = subprocess.check_output(["git", "worktree", "list", "--porcelain"], cwd=repo, text=True)
            self.assertEqual(listing.count("worktree "), 1, listing)
        self.assertEqual((self.root / "core/mihomo/go.mod").read_text(), "module fixture\n")

    def test_wrong_primary_gitlink_pin_refuses_before_any_test(self):
        self.add_recursive_native_fixture()
        subprocess.run(["git", "checkout", "--detach", "HEAD~1"], cwd=self.root / "core/mihomo", check=True, capture_output=True)
        result = self.run_gate(SUBMODULE_REQUIRED="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Gitlink does not match", result.stderr)
        self.assertFalse(list((self.root / "coverage/source-gate").glob("*.jsonl")))

    def test_mutated_dependency_cannot_pass_or_modify_primary_source(self):
        self.add_recursive_native_fixture()
        result = self.run_gate(SUBMODULE_REQUIRED="1", SUBMODULE_MUTATION="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / "core/mihomo/go.mod").read_text(), "module fixture\n")
        self.assertFalse((self.root / "coverage/lcov.info").exists())

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

    def test_actual_tracked_add_rename_and_edit_require_reviewed_plan_refresh(self):
        baseline = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()
        path = self.root / "test/services/case_0_test.dart"
        for change in ("add", "rename", "edit"):
            with self.subTest(change=change):
                if change == "add":
                    (path.parent / "added_test.dart").write_text("void main() {}")
                elif change == "rename":
                    path.rename(path.with_name("renamed_test.dart"))
                else:
                    path.write_text("void main() { /* changed */ }\n")
                subprocess.run(["git", "add", "-A", "test"], cwd=self.root, check=True)
                self.commit_fixture(self.root, change)
                shutil.rmtree(self.root / "coverage", ignore_errors=True)
                result = self.run_gate()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("plan mismatch", result.stderr)
                self.assertFalse(list((self.root / "coverage/source-gate").glob("*.jsonl")))
                # This repository is an owned temporary test fixture only.
                subprocess.run(["git", "reset", "--hard", baseline], cwd=self.root, check=True, capture_output=True)

    def test_cleanup_failure_cannot_emit_complete_after_successful_tests(self):
        real_git = shutil.which("git")
        self.executable("git", f"""import os,sys
if sys.argv[1:3]==['worktree','remove']: sys.exit(19)
os.execv({real_git!r},[{real_git!r},*sys.argv[1:]])
""")
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        receipt = json.loads((self.root / "coverage/source-gate/execution.json").read_text())
        self.assertEqual(receipt["status"], "INCOMPLETE")
        self.assertFalse(receipt["cleanup_complete"])
        self.assertEqual(len(receipt["cleanup_errors"]), 4)
        self.assertNotIn("Flutter tests observed:", result.stdout)

    def test_group_file_case_skip_and_terminal_changes_refuse_real_commands(self):
        for fault in ("OMIT_SECOND", "UNEXPECTED_FILE", "DUPLICATE_SUITE", "DUPLICATE_START",
                      "MISSING_TERMINAL", "DUPLICATE_TERMINAL", "FAILED_TERMINAL", "HIDE_CASE", "CHANGED_SKIP"):
            with self.subTest(fault=fault):
                shutil.rmtree(self.root / "coverage", ignore_errors=True)
                result = self.run_gate(**{fault: "1"})
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse((self.root / "coverage/lcov.info").exists())
                receipt = json.loads((self.root / "coverage/source-gate/execution.json").read_text())
                self.assertNotEqual(receipt["status"], "COMPLETE")

    def test_reviewed_plan_rejects_changed_blob_membership_and_isolation(self):
        original = json.loads(self.plan_path.read_text())
        for fault in ("blob", "missing", "duplicate", "isolated", "order", "lib", "lock", "sdk"):
            with self.subTest(fault=fault):
                data = json.loads(json.dumps(original))
                if fault == "blob":
                    data["files"][next(iter(data["files"]))] = "0" * 64
                elif fault == "missing":
                    data["groups"].pop()
                elif fault == "duplicate":
                    data["groups"].append(data["groups"][0])
                elif fault == "isolated":
                    data["groups"][0].extend(data["groups"].pop(1))
                elif fault == "order":
                    data["groups"].reverse()
                elif fault == "lib":
                    data["lib_tree"] = "0" * 40
                else:
                    data["runtime_files" if fault == "lock" else "sdk"] = {}
                self.plan_path.write_text(json.dumps(data))
                shutil.rmtree(self.root / "coverage", ignore_errors=True)
                result = self.run_gate()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(list((self.root / "coverage/source-gate").glob("*.jsonl")))

    def test_resource_default_unknown_capacity_limit_and_refusal_reach_entry(self):
        for cpu, memory, limit, workers in ((4, 16 * RUNNER.GIB, None, 4),
                                           (2, 16 * RUNNER.GIB, "3", 2),
                                           (8, 6 * RUNNER.GIB, None, 2),
                                           (None, None, None, 1), (4, 16 * RUNNER.GIB, "1", 1)):
            with self.subTest(cpu=cpu, memory=memory, limit=limit):
                shutil.rmtree(self.root / "coverage", ignore_errors=True)
                env = {"FIXTURE_RESOURCES": json.dumps({"effective_cpus": cpu, "available_memory_bytes": memory, "capacity_status": "UNKNOWN" if cpu is None or memory is None else "VISIBLE_BOUNDS", "missing_interfaces": ["fixture CPU/memory"] if cpu is None or memory is None else []})}
                if limit is not None:
                    env["YUELINK_COVERAGE_MAX_WORKERS"] = limit
                result = self.run_gate(**env)
                self.assertEqual(result.returncode, 0, result.stderr)
                receipt = json.loads((self.root / "coverage/source-gate/execution.json").read_text())
                self.assertEqual(receipt["workers"], workers)
                if memory is None:
                    self.assertEqual(receipt["resources_before"]["capacity_status"], "UNKNOWN")
                    self.assertEqual(receipt["worker_selection"], "CONSERVATIVE_UNKNOWN")
                    self.assertTrue(receipt["resources_before"]["missing_interfaces"])
                    self.assertIn("NOT_EVALUATED", receipt["resource_acceptance"])
        for env in ({"YUELINK_COVERAGE_MAX_WORKERS": "0"}, {"YUELINK_COVERAGE_MAX_WORKERS": "auto"},
                    {"FIXTURE_RESOURCES": json.dumps({"effective_cpus": 4, "available_memory_bytes": RUNNER.GIB, "capacity_status": "UNKNOWN", "missing_interfaces": ["fixture cgroup"]})}):
            shutil.rmtree(self.root / "coverage", ignore_errors=True)
            result = self.run_gate(**env)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(list((self.root / "coverage/source-gate").glob("*.jsonl")))

    def test_visible_linux_and_macos_capacity_interfaces_do_not_require_new_env(self):
        values = {"cpu.max": "200000 100000", "memory.max": str(8 * RUNNER.GIB),
                  "memory.current": str(RUNNER.GIB), "memory.peak": "12345", "memory.events": "oom 0",
                  "meminfo": "MemAvailable: 16777216 kB", "cgroup": "0::/",
                  "cgroup.controllers": "cpu memory"}
        with patch.object(RUNNER, "read_text", side_effect=lambda p: values.get(p.name)), \
                patch.object(RUNNER.os, "cpu_count", return_value=8), \
                patch.object(RUNNER.platform, "system", return_value="Linux"):
            result = RUNNER.resource_snapshot()
            self.assertEqual(result["effective_cpus"], 2)
            self.assertEqual(result["available_memory_bytes"], 7 * RUNNER.GIB)
            self.assertEqual(result["cgroup"][0]["peak_bytes"], "12345")
        with patch.object(RUNNER, "read_text", return_value=None), \
                patch.object(RUNNER.platform, "system", return_value="Darwin"), \
                patch.object(RUNNER.subprocess, "check_output", return_value=(
                    "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
                    "Pages free: 100000.\nPages inactive: 200000.\n")):
            result = RUNNER.resource_snapshot()
            self.assertEqual(result["available_memory_bytes"], 300000 * 16384)
            self.assertIsNone(result["cgroup"][0]["peak_bytes"])

    def test_real_cgroup_root_absence_does_not_hide_missing_child_or_namespace_limits(self):
        root = "/sys/fs/cgroup"
        values = {"/proc/self/cgroup": "0::/job",
                  "/proc/meminfo": "MemAvailable: 16777216 kB",
                  f"{root}/cgroup.controllers": "cpu memory",
                  f"{root}/job/cgroup.controllers": "",
                  f"{root}/job/cpu.max": "400000 100000",
                  f"{root}/job/memory.max": str(12 * RUNNER.GIB),
                  f"{root}/job/memory.current": str(RUNNER.GIB)}
        with patch.object(RUNNER, "read_text", side_effect=lambda p: values.get(str(p))), \
                patch.object(RUNNER.os, "cpu_count", return_value=8), \
                patch.object(RUNNER.os, "sched_getaffinity", return_value=set(range(8)), create=True), \
                patch.object(RUNNER.platform, "system", return_value="Linux"), \
                patch.object(RUNNER.os, "stat", side_effect=FileNotFoundError) as stat:
            result = RUNNER.resource_snapshot()
            stat.assert_called_once_with(Path(root) / "cgroup.events")
            self.assertEqual(result["capacity_status"], "VISIBLE_BOUNDS")
            self.assertTrue(result["cgroup"][-1]["root_limits_not_applicable"])
            self.assertEqual(RUNNER.worker_count(result), 4)
            # The same root absence cannot excuse a missing child limit.
            del values[f"{root}/job/cpu.max"]
            result = RUNNER.resource_snapshot()
            self.assertEqual(result["capacity_status"], "UNKNOWN")
            self.assertEqual(RUNNER.worker_count(result), 1)
            values[f"{root}/job/memory.max"] = str(2 * RUNNER.GIB)
            with self.assertRaisesRegex(ValueError, "insufficient visible memory"):
                RUNNER.worker_count(RUNNER.resource_snapshot())
            # Direct membership of the genuine host root remains valid.
            values["/proc/self/cgroup"] = "0::/"
            result = RUNNER.resource_snapshot()
            self.assertEqual(result["capacity_status"], "VISIBLE_BOUNDS")
            self.assertEqual(RUNNER.worker_count(result), 4)
            # A namespace root has cgroup.events, even if its limits cannot be read.
            stat.side_effect = None
            self.assertEqual(RUNNER.resource_snapshot()["capacity_status"], "UNKNOWN")
            stat.side_effect = PermissionError
            self.assertEqual(RUNNER.resource_snapshot()["capacity_status"], "UNKNOWN")
            # Readable namespace-root limits must still constrain and refuse.
            values[f"{root}/cpu.max"] = "200000 100000"
            values[f"{root}/memory.max"] = str(2 * RUNNER.GIB)
            values[f"{root}/memory.current"] = str(RUNNER.GIB)
            result = RUNNER.resource_snapshot()
            self.assertEqual(result["effective_cpus"], 2)
            self.assertEqual(result["available_memory_bytes"], RUNNER.GIB)
            with self.assertRaisesRegex(ValueError, "insufficient visible memory"):
                RUNNER.worker_count(result)


if __name__ == "__main__":
    unittest.main()
