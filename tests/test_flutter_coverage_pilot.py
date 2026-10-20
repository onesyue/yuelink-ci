"""Bounded pilot correctness controls; no Flutter engine or release execution."""

import copy
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
SPEC = importlib.util.spec_from_file_location(
    "pilot", ROOT / "scripts/flutter-coverage-pilot.py"
)
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)
COUNTER = pilot.module("counter", ROOT / "scripts/count-flutter-machine-tests.py")
RUNNER = pilot.module("runner", ROOT / "scripts/run-flutter-coverage.py")


def stream(files, mutation=None):
    rows = [{"type": "start"}]
    for index, path in enumerate(files):
        rows += [
            {"type": "suite", "suite": {"id": index, "path": path}},
            {
                "type": "testStart",
                "test": {"id": index, "suiteID": index, "name": "case"},
            },
            {
                "type": "testDone",
                "testID": index,
                "result": "success",
                "hidden": False,
                "skipped": False,
            },
        ]
    rows.append({"type": "done", "success": True})
    if mutation == "missing_done":
        rows.pop()
    if mutation == "failed":
        rows[3]["result"] = "failure"
    if mutation == "skipped":
        rows[3]["skipped"] = True
    if mutation == "hidden":
        rows[3]["hidden"] = True
    if mutation == "missing_terminal":
        rows.pop(3)
    if mutation == "duplicate_terminal":
        rows.insert(4, rows[3])
    return "\n".join(json.dumps(row) for row in rows) + "\n"


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Synthetic current-helper fixture only; the historical manifest remains frozen.
        self.data = json.loads(pilot.DEFAULT_MANIFEST.read_text())
        self.data["helper_sha256"] = {name: hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest()
                                      for name in self.data["helper_sha256"]}
        self.synthetic_manifest = self.root / "synthetic-current-helper-manifest.json"
        self.synthetic_manifest.write_text(json.dumps(self.data))
        self.data = pilot.manifest(self.synthetic_manifest)

    def test_real_cli_describes_exact_bounded_sample_without_execution(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/flutter-coverage-pilot.py"),
                "--describe", "--manifest", str(self.synthetic_manifest),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        description = json.loads(result.stdout)
        self.assertEqual(description["acceptance"], "NOT_EXECUTED; diagnostic only")
        self.assertEqual(len(description["A"]), 36)
        self.assertEqual(len(description["B"]), 9)
        for variant in ("A", "B"):
            self.assertEqual(
                sorted(p for group in description[variant] for p in group),
                self.data["files"],
            )
            for path in self.data["isolated"]:
                self.assertIn([path], description[variant])

    def test_historical_manifest_does_not_admit_changed_runner(self):
        with self.assertRaisesRegex(ValueError, "helper differs from the frozen builder"):
            pilot.manifest(pilot.DEFAULT_MANIFEST)
        result = subprocess.run([sys.executable, str(ROOT / "scripts/flutter-coverage-pilot.py"),
                                 "--describe"], capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("helper differs from the frozen builder", result.stderr)

    def test_manifest_mutations_refuse_lost_inventory_isolation_and_helpers(self):
        for mutation in ("file", "isolation", "helper", "workers", "group_size"):
            with self.subTest(mutation=mutation):
                data = copy.deepcopy(self.data)
                if mutation == "file":
                    data["files"].pop()
                elif mutation == "isolation":
                    data["isolated"].pop()
                elif mutation == "helper":
                    data["helper_sha256"].clear()
                else:
                    data[mutation] = 1
                path = self.root / "manifest.json"
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    pilot.manifest(path)

    def test_terminal_cases_required_and_skips_are_not_passes(self):
        report = self.root / "report.jsonl"
        files = ["test/case_test.dart"]
        report.write_text(stream(files))
        good = pilot.identities(report, self.root, files, COUNTER)
        self.assertEqual(good[(files[0], "case", False)], 1)
        report.write_text(stream(files, "skipped"))
        skipped = pilot.identities(report, self.root, files, COUNTER)
        with self.assertRaisesRegex(ValueError, "skip classifications"):
            pilot.compare({"cases": good}, {"cases": skipped})
        for mutation in (
            "missing_done",
            "failed",
            "hidden",
            "missing_terminal",
            "duplicate_terminal",
        ):
            report.write_text(stream(files, mutation))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                pilot.identities(report, self.root, files, COUNTER)
        report.write_text(stream(["test/other_test.dart"]))
        with self.assertRaises(ValueError):
            pilot.identities(report, self.root, files, COUNTER)

    def test_coverage_compares_hit_union_and_rejects_missing_escape_or_loss(self):
        report = self.root / "test.info"
        text = "SF:lib/a.dart\nDA:1,1\nBRDA:1,0,0,1\nFNDA:1,fn\nend_of_record\n"
        report.write_text(text)
        left = pilot.coverage(report, self.root)
        report.write_text(text.replace(",1\n", ",9\n").replace("FNDA:1", "FNDA:9"))
        self.assertEqual(left, pilot.coverage(report, self.root))
        report.write_text(text.replace("DA:1,1", "DA:1,0"))
        with self.assertRaisesRegex(ValueError, "coverage"):
            pilot.compare(
                {"cases": {}, "coverage": left},
                {"cases": {}, "coverage": pilot.coverage(report, self.root)},
            )
        report.write_text(text.replace("lib/a.dart", "/outside/a.dart"))
        with self.assertRaises(ValueError):
            pilot.coverage(report, self.root)
        report.unlink()
        with self.assertRaises(FileNotFoundError):
            pilot.coverage(report, self.root)
        with self.assertRaises(ValueError):
            pilot.compare(
                {"cases": {}, "coverage": set()}, {"cases": {}, "coverage": set()}
            )

    @unittest.skipUnless(
        sys.platform == "linux", "GNU time execution lens runs in Linux"
    )
    def test_real_worktrees_and_commands_compare_a_b_and_refuse_engine_faults(self):
        source = self.root / "source"
        source.mkdir()
        for path in self.data["files"]:
            target = source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("void main() {}\n")
        (source / "lib").mkdir()
        (source / "lib/a.dart").write_text("void f() {}\n")
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "add", "test", "lib"], cwd=source, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "-qm",
                "fixture",
            ],
            cwd=source,
            check=True,
        )
        self.data["source_sha"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source, text=True
        ).strip()
        binaries = self.root / "bin"
        binaries.mkdir()
        flutter = binaries / "flutter"
        flutter.write_text(
            f"#!{sys.executable}\n"
            + """import hashlib
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
if args==['--version','--machine']: print(os.environ['PILOT_SDK']); sys.exit(0)
if args[:2]==['pub','get']: sys.exit(0)
assert args[:5]==['test','--no-pub','--coverage','--machine','--concurrency=1']
files=[p for p in args if p.startswith('test/')]
assert 1<=len(files)<=8
cache=Path('build/test_cache')
assert not cache.exists()
cache.mkdir(parents=True)
(cache/'kernel').write_text('cold compiler required next group')
if os.environ.get('PILOT_FAULT')=='engine': sys.exit(7)
print(json.dumps({'type':'start'}))
for i,path in enumerate(files):
    print(json.dumps({'type':'suite','suite':{'id':i,'path':str(Path.cwd()/path)}}))
    print(json.dumps({'type':'testStart','test':{'id':i,'suiteID':i,'name':'case'}}))
    print(json.dumps({'type':'testDone','testID':i,'result':'success','hidden':False,'skipped':False}))
if os.environ.get('PILOT_FAULT')!='terminal': print(json.dumps({'type':'done','success':True}))
if os.environ.get('PILOT_FAULT')!='coverage':
    Path(args[args.index('--coverage-path')+1]).write_text('SF:'+str(Path.cwd()/'lib/a.dart')+'\\nDA:1,1\\nend_of_record\\n')
"""
        )
        flutter.chmod(0o755)
        config = self.root / "manifest.json"
        config.write_text(json.dumps(self.data))
        with patch.dict(
            os.environ,
            {
                "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                "PILOT_SDK": json.dumps(self.data["sdk"]),
            },
        ):
            command = [
                sys.executable,
                str(ROOT / "scripts/flutter-coverage-pilot.py"),
                "--manifest",
                str(config),
                "--source",
                str(source),
                "--output",
                str(self.root / "actual-cli"),
            ]
            executed = subprocess.run(
                command, capture_output=True, text=True, timeout=30
            )
            self.assertEqual(executed.returncode, 0, executed.stderr)
            receipt = json.loads((self.root / "actual-cli/comparison.json").read_text())
            self.assertEqual(receipt["visible_cases"], 36)
            self.assertFalse(receipt["release_acceptance"])
            with patch.dict(os.environ, {"PILOT_SDK": "{}"}):
                invalid = subprocess.run(
                    command, capture_output=True, text=True, timeout=10
                )
                self.assertNotEqual(invalid.returncode, 0)
                self.assertIn("SDK", invalid.stderr)
            for fault in ("engine", "terminal", "coverage"):
                with (
                    self.subTest(fault=fault),
                    patch.dict(os.environ, {"PILOT_FAULT": fault}),
                ):
                    with self.assertRaises(
                        (
                            ValueError,
                            FileNotFoundError,
                            subprocess.CalledProcessError,
                            RuntimeError,
                        )
                    ):
                        pilot.run_variant(
                            source, self.root / fault, self.data, "B", RUNNER, COUNTER
                        )
        worktrees = subprocess.check_output(
            ["git", "worktree", "list", "--porcelain"], cwd=source, text=True
        )
        self.assertEqual(worktrees.count("worktree "), 1)


class Group25Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = pilot.group25_manifest()

    def test_fixed_describe_and_mutations_do_not_execute(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/flutter-coverage-pilot.py"),
                                 "--group25", "--describe"], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), self.data)
        for key, value in (("workers", 4), ("timeout_seconds", 301), ("source_sha", "1" * 40),
                           ("builder_base", "2" * 40), ("files", self.data["files"][:-1]),
                           ("release_acceptance", True), ("helper_sha256", {}), ("sdk", {})):
            changed = copy.deepcopy(self.data)
            changed[key] = value
            path = self.root / "manifest.json"
            path.write_text(json.dumps(changed))
            with self.subTest(key=key), patch.object(pilot, "GROUP25_MANIFEST", path), self.assertRaises(ValueError):
                pilot.group25_manifest()

    def test_real_observer_retains_bytes_and_orders_arrival_before_timeout(self):
        timeout = shutil.which("timeout")
        self.assertIsNotNone(timeout, "actual GNU timeout is required by this finite consumer")
        output = self.root / "observe"
        output.mkdir()
        raw = '{"type":"error","time":66683}\n'
        command = [timeout, "--verbose", "--signal=TERM", "--kill-after=0.2s", "0.3",
                   sys.executable, "-u", "-c", "import time;print(" + repr(raw.rstrip()) + ");time.sleep(10)"]
        result = pilot.observe_command(command, self.root, output, 2)
        self.assertEqual(result["exit_code"], 124)
        self.assertEqual((output / "events.jsonl").read_bytes(), raw.encode())
        rows = [json.loads(line) for line in (output / "arrivals.jsonl").read_text().splitlines()]
        event = next(row for row in rows if row.get("event_type") == "error")
        signal_row = next(row for row in rows if "timeout_signal_announcement" in row)
        self.assertLess(event["monotonic_ns"], signal_row["monotonic_ns"])
        self.assertEqual(event["machine_time"], 66683)
        self.assertEqual(event["line_sha256"], hashlib.sha256(raw.encode()).hexdigest())
        after = self.root / "after-signal"
        after.mkdir()
        child = ("import signal,time,sys\n"
                 "def term(*args):\n time.sleep(.05)\n print(" + repr(raw.rstrip()) +
                 ",flush=True)\n sys.exit(0)\n"
                 "signal.signal(signal.SIGTERM,term)\ntime.sleep(10)\n")
        result = pilot.observe_command(
            [timeout, "--verbose", "--signal=TERM", "--kill-after=0.2s", "0.3",
             sys.executable, "-u", "-c", child], self.root, after, 2)
        self.assertEqual(result["exit_code"], 124)
        rows = [json.loads(line) for line in (after / "arrivals.jsonl").read_text().splitlines()]
        event = next(row for row in rows if row.get("event_type") == "error")
        signal_row = next(row for row in rows if "timeout_signal_announcement" in row)
        self.assertGreater(event["monotonic_ns"], signal_row["monotonic_ns"])
        self.assertEqual(event["machine_time"], 66683)

    def test_real_observer_records_outer_watchdog_and_partial_raw_line(self):
        output = self.root / "watchdog"
        output.mkdir()
        command = [sys.executable, "-u", "-c",
                   "import time,sys;sys.stdout.write('{partial');sys.stdout.flush();time.sleep(10)"]
        result = pilot.observe_command(command, self.root, output, 0.25)
        self.assertTrue(result["outer_watchdog"])
        self.assertNotEqual(result["exit_code"], 0)
        self.assertEqual((output / "events.jsonl").read_bytes(), b"{partial")
        self.assertEqual(result["partial_line_bytes"]["stdout"], 8)
        self.assertTrue((output / "execution.json").is_file())

    def test_cache_miss_wrong_arch_and_sdk_are_refused_without_download(self):
        installer = pilot.module("fixture_installer", ROOT / ".github/actions/setup-flutter/install_flutter_sdk.py")
        target = self.root / "yue-flutter/3.47.7/Linux-X64"
        env = {"RUNNER_OS": "Linux", "RUNNER_ARCH": "X64", "RUNNER_TOOL_CACHE": str(self.root)}
        with patch.dict(os.environ, env), patch.object(pilot.sys, "platform", "linux"), \
             patch.object(pilot.platform, "machine", return_value="x86_64"):
            with self.assertRaises(pilot.EnvironmentNotReady):
                pilot.group25_cache(self.data)
            output = self.root / "cache-miss-terminal"
            self.assertEqual(pilot.group25_execute(self.root, output, self.data), 1)
            terminal = json.loads((output / "diagnostic.json").read_text())
            self.assertEqual(terminal["status"], "ENVIRONMENT_NOT_READY")
            self.assertEqual(terminal["commands"], [])
            self.assertFalse(terminal["release_acceptance"])
            for name in ("bin/flutter", "bin/cache/flutter_tools.snapshot",
                         "bin/cache/dart-sdk/bin/dart", "bin/cache/artifacts/engine/linux-x64/flutter_tester"):
                path = target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture")
            archive = installer.RELEASES[("3.47.7", "Linux")]
            (target / installer.RECEIPT_NAME).write_text(json.dumps(installer.receipt("3.47.7", "Linux", "X64", archive[1])))
            (target / "bin/flutter").write_text(f"#!{sys.executable}\nimport os\nprint(os.environ['SDK_FIXTURE'])\n")
            (target / "bin/flutter").chmod(0o755)
            with patch.dict(os.environ, {"SDK_FIXTURE": json.dumps(self.data["sdk"])}):
                self.assertEqual(pilot.group25_cache(self.data), self.data["sdk"])
            with patch.dict(os.environ, {"SDK_FIXTURE": "{}"}), self.assertRaises(pilot.EnvironmentNotReady):
                pilot.group25_cache(self.data)
            with patch.object(pilot.platform, "machine", return_value="aarch64"), self.assertRaises(pilot.EnvironmentNotReady):
                pilot.group25_cache(self.data)
            (target / installer.RECEIPT_NAME).write_text("{}")
            with self.assertRaises(pilot.EnvironmentNotReady):
                pilot.group25_cache(self.data)

    def fixture_source(self):
        source = self.root / "source"
        source.mkdir()
        for name in [*self.data["files"], "lib/fixture.dart", *self.data["runtime_files"]]:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "add", "."], cwd=source, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
                        "commit", "-qm", "fixture"], cwd=source, check=True)
        self.data["source_sha"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        self.data["lib_tree"] = subprocess.check_output(["git", "rev-parse", "HEAD:lib"], cwd=source, text=True).strip()
        for key in ("file_sha256", "runtime_files"):
            self.data[key] = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in self.data[key]}
        for label, files in (("group", self.data["files"]), ("withdrawal", [self.data["files"][4]])):
            self.data["expected"][label] = {
                "visible_cases": len(files), "visible_skips": 0,
                "cases_sha256": RUNNER.digest(sorted([name, "case", False, 1] for name in files)),
                "coverage_sha256": RUNNER.digest([["lib/fixture.dart", "DA", "1", False],
                                                 ["lib/fixture.dart", "DA", "1", True]]),
                "coverage_rows": 2,
            }
        binary = self.root / "bin"
        binary.mkdir()
        engine = binary / "flutter"
        engine.write_text(f"#!{sys.executable}\n" + r'''
import json,os,signal,sys,time
from pathlib import Path
args=sys.argv[1:];fault=os.environ.get("GROUP25_FAULT","")
with Path(os.environ["GROUP25_CALLS"]).open("a") as log:
    log.write(json.dumps({"args":args,"cwd":str(Path.cwd())})+"\n")
if args==["pub","get","--offline","--enforce-lockfile"]:
    raise SystemExit(17 if fault=="pub" else 0)
assert args[:5]==["test","--no-pub","--coverage","--machine","--concurrency=1"]
files=[arg for arg in args if arg.startswith("test/")]
assert not Path("build/test_cache").exists()
Path("build/test_cache").mkdir(parents=True)
if fault=="engine" and len(files)>1:raise SystemExit(7)
if fault=="cancel":
    os.kill(int(os.environ["GROUP25_OWNER_PID"]),signal.SIGTERM)
    time.sleep(10)
print(json.dumps({"type":"start"}))
for i,name in enumerate(files):
    print(json.dumps({"type":"suite","suite":{"id":i,"path":str(Path.cwd()/name)}}))
    print(json.dumps({"type":"testStart","test":{"id":i,"suiteID":i,"name":"changed" if fault=="case" and len(files)>1 else "case"}}))
    print(json.dumps({"type":"testDone","testID":i,"result":"success","hidden":False,"skipped":fault=="skip" and len(files)>1}))
if fault!="terminal" or len(files)==1:print(json.dumps({"type":"done","success":True}))
if fault=="duplicate" and len(files)>1:print(json.dumps({"type":"done","success":True}))
if fault=="malformed" and len(files)>1:print("{broken")
if fault!="coverage" or len(files)==1:
    Path(args[args.index("--coverage-path")+1]).write_text("SF:"+str(Path.cwd()/"lib/fixture.dart")+"\nDA:1,1\nend_of_record\n")
''')
        engine.chmod(0o755)
        git = binary / "git"
        git.write_text(f"#!{sys.executable}\n" +
                       "import os,sys\n"
                       "if os.environ.get('GROUP25_FAULT')=='cleanup' and "
                       "sys.argv[1:3]==['worktree','remove']:raise SystemExit(29)\n"
                       f"os.execv({shutil.which('git')!r}, ['git', *sys.argv[1:]])\n")
        git.chmod(0o755)
        return source, binary

    def test_actual_git_two_commands_and_failed_group_never_becomes_singleton_pass(self):
        source, binary = self.fixture_source()
        calls = self.root / "calls"
        original_module = pilot.module
        def modules(name, path):
            return RUNNER if name == "group25_runner" else original_module(name, path)
        # Only engine/SDK and capacity are controlled; Git, timeout, parser and cleanup are real.
        with patch.object(pilot, "group25_cache", return_value=self.data["sdk"]), \
             patch.object(pilot, "module", side_effect=modules), \
             patch.object(RUNNER, "resource_snapshot", return_value={
                 "effective_cpus": 1, "available_memory_bytes": 4 * 1024**3,
                 "capacity_status": "VISIBLE_BOUNDS"}), \
             patch.dict(os.environ, {"PATH": str(binary) + os.pathsep + os.environ["PATH"],
                                     "GROUP25_CALLS": str(calls), "GROUP25_OWNER_PID": str(os.getpid())}):
            for fault in ("", "engine", "terminal", "coverage", "skip", "case", "duplicate",
                          "malformed", "pub", "cancel", "cleanup"):
                with self.subTest(fault=fault), patch.dict(os.environ, {"GROUP25_FAULT": fault}):
                    calls.write_text("")
                    output = self.root / ("out-" + (fault or "pass"))
                    rc = pilot.group25_execute(source, output, self.data)
                    receipt = json.loads((output / "diagnostic.json").read_text())
                    self.assertEqual(rc == 0, not fault, receipt)
                    self.assertFalse(receipt["release_acceptance"])
                    self.assertEqual(receipt["cleanup_complete"], fault != "cleanup")
                    if fault == "pub":
                        self.assertEqual(receipt["status"], "ENVIRONMENT_NOT_READY")
                        self.assertEqual(receipt["commands"], [])
                    elif fault == "cancel":
                        self.assertEqual(receipt["status"], "CANCELED")
                        self.assertEqual(len(receipt["commands"]), 1)
                        self.assertEqual(receipt["commands"][0]["status"], "CANCELED")
                        self.assertFalse((output / "withdrawal").exists())
                        run = json.loads((output / "group/execution.json").read_text())
                        self.assertIn("KeyboardInterrupt", run["error"])
                    else:
                        self.assertEqual(len(receipt["commands"]), 2)
                        self.assertEqual(receipt["commands"][1]["status"], "PASSED")
                        self.assertEqual(receipt["commands"][0]["status"] == "PASSED",
                                         fault in ("", "cleanup"))
                        tests = [json.loads(line) for line in calls.read_text().splitlines()
                                 if json.loads(line)["args"][0] == "test"]
                        self.assertEqual(len(tests), 2)
                        self.assertNotEqual(tests[0]["cwd"], tests[1]["cwd"])
                        for label in ("group", "withdrawal"):
                            run = json.loads((output / label / "execution.json").read_text())
                            self.assertIn("300", run["command"])
                            self.assertTrue((output / label / "arrivals.jsonl").is_file())
                            context = json.loads((output / label / "context.json").read_text())
                            self.assertIn("resources_before", context)
                            self.assertIn("resources_after", context)
                            self.assertEqual(context["cleanup_complete"], fault != "cleanup")
                    if fault == "cleanup":
                        self.assertEqual(len(receipt["cleanup_errors"]), 2)
                        with patch.dict(os.environ, {"GROUP25_FAULT": ""}):
                            subprocess.run(["git", "worktree", "prune"], cwd=source, check=True)
                    worktrees = subprocess.check_output(["git", "worktree", "list", "--porcelain"],
                                                         cwd=source, text=True)
                    self.assertEqual(worktrees.count("worktree "), 1)
            for field, value in (("source_sha", "1" * 40), ("lib_tree", "2" * 40),
                                 ("file_sha256", dict.fromkeys(self.data["files"], "3" * 64))):
                with self.subTest(refusal=field):
                    calls.write_text("")
                    data = copy.deepcopy(self.data)
                    data[field] = value
                    output = self.root / ("refuse-" + field)
                    self.assertEqual(pilot.group25_execute(source, output, data), 1)
                    receipt = json.loads((output / "diagnostic.json").read_text())
                    self.assertEqual(receipt["status"], "DIAGNOSTIC_PAIR_FAILED")
                    self.assertEqual(receipt["commands"], [])
                    self.assertEqual(calls.read_text(), "")


if __name__ == "__main__":
    unittest.main()
