"""Bounded pilot correctness controls; no Flutter engine or release execution."""

import copy
import importlib.util
import json
import os
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
        self.data = pilot.manifest(pilot.DEFAULT_MANIFEST)

    def test_real_cli_describes_exact_bounded_sample_without_execution(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/flutter-coverage-pilot.py"),
                "--describe",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        description = json.loads(result.stdout)
        self.assertEqual(description["acceptance"], "NOT_EXECUTED; diagnostic only")
        self.assertEqual(len(description["A"]), 36)
        self.assertEqual(len(description["B"]), 11)
        for variant in ("A", "B"):
            self.assertEqual(
                sorted(p for group in description[variant] for p in group),
                self.data["files"],
            )
            for path in self.data["isolated"]:
                self.assertIn([path], description[variant])

    def test_only_original_group7_splits_in_place_and_worker_history_is_explicit(self):
        early = [[path] for path in self.data["isolated"]]
        rest = [path for path in self.data["files"] if path not in self.data["isolated"]]
        original = early + [rest[i:i + 8] for i in range(0, len(rest), 8)]
        expected = original[:7] + [original[7][:6], original[7][6:7], original[7][7:]] + original[8:]
        self.assertEqual(pilot.groups(self.data, "B"), expected)
        self.assertEqual(pilot.groups(self.data, "A"), early + [[path] for path in rest])
        self.assertEqual([path for batch in expected for path in batch],
                         [path for batch in original for path in batch])
        self.assertEqual(len({path for batch in expected for path in batch}), 36)
        self.assertEqual([number % self.data["workers"] for number in (7, 8, 9, 10)],
                         [3, 0, 1, 2])
        for path in self.data["in_place_isolated"]:
            self.assertEqual([batch for batch in expected if path in batch], [[path]])

    def test_schema1_and_missing_changed_or_duplicate_in_place_boundaries_fail_closed(self):
        for fault in ("schema1", "missing", "wrong", "duplicate", "reordered"):
            with self.subTest(fault=fault):
                data = copy.deepcopy(self.data)
                if fault == "schema1":
                    data["schema"] = 1
                elif fault == "missing":
                    del data["in_place_isolated"]
                elif fault == "wrong":
                    data["in_place_isolated"][0] = self.data["files"][0]
                elif fault == "duplicate":
                    data["in_place_isolated"].append(data["in_place_isolated"][0])
                else:
                    data["in_place_isolated"].reverse()
                path = self.root / "bad-boundaries.json"
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    pilot.manifest(path)

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
            + """import json,os,sys
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
                command, capture_output=True, text=True, timeout=30, check=False
            )
            self.assertEqual(executed.returncode, 0, executed.stderr)
            receipt = json.loads((self.root / "actual-cli/comparison.json").read_text())
            self.assertEqual(receipt["visible_cases"], 36)
            self.assertFalse(receipt["release_acceptance"])
            with patch.dict(os.environ, {"PILOT_SDK": "{}"}):
                invalid = subprocess.run(
                    command, capture_output=True, text=True, timeout=10, check=False
                )
                self.assertNotEqual(invalid.returncode, 0)
                self.assertIn("SDK", invalid.stderr)
            for fault in ("engine", "terminal", "coverage"):
                with (
                    self.subTest(fault=fault),
                    patch.dict(os.environ, {"PILOT_FAULT": fault}),
                    self.assertRaises(
                        (
                            ValueError,
                            FileNotFoundError,
                            subprocess.CalledProcessError,
                            RuntimeError,
                        )
                    ),
                ):
                    pilot.run_variant(
                        source, self.root / fault, self.data, "B", RUNNER, COUNTER
                    )
        worktrees = subprocess.check_output(
            ["git", "worktree", "list", "--porcelain"], cwd=source, text=True
        )
        self.assertEqual(worktrees.count("worktree "), 1)


if __name__ == "__main__":
    unittest.main()
