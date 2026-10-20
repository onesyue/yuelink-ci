"""Execute the workflow shell and real report comparators without Flutter/network."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/flutter-coverage-pilot.yml"
EXECUTE = "Run one AB pair then one BA pair and compare all four variants"
SOURCE = "473af71077c61db9e42170b304bce1fc446c9077"
BASE = "113c864b869d6b206a06d6ed5fe138a6f657bd69"
PILOT = "9965321062a98a273fb92d8cd00cdc004f165866"


def step(text, name):
    start = text.index("      - name: " + name + "\n")
    end = text.find("\n      - name:", start + 1)
    return text[start:] if end < 0 else text[start:end]


def shell(text, name):
    block = step(text, name).split("        run: |\n", 1)[1]
    return "\n".join(line.removeprefix("          ")
                     for line in block.splitlines()) + "\n"


STUB = r'''
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--order", choices=("AB", "BA"), required=True)
    args = parser.parse_args()
    fault = os.environ.get("PILOT_FIXTURE_FAULT", "")
    with Path(os.environ["PILOT_CALLS"]).open("a") as calls:
        calls.write(json.dumps(vars(args)) + "\n")
    if Path(args.source) != Path(os.environ["GITHUB_WORKSPACE"]) / "source":
        raise SystemExit(24)
    if fault == "fail_AB" and args.order == "AB":
        raise SystemExit(23)
    data = manifest(DEFAULT_MANIFEST)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    identity = {"manifest": data, "order": args.order, "sdk": data["sdk"],
                "platform": "Linux-x64-fixture", "cpu_count": 4,
                "cpu_max": "400000 100000", "memory_max": "6442450944"}
    if args.order == "BA" and fault == "resources":
        identity["cpu_max"] = "200000 100000"
    (output / "input.json").write_text(json.dumps(identity))
    for variant in args.order:
        directory = output / variant
        directory.mkdir()
        batches = groups(data, variant)
        (directory / "groups.json").write_text(json.dumps(batches))
        for index, files in enumerate(batches):
            rows = [{"type": "start"}]
            for number, file in enumerate(files):
                name = "case " + file
                if args.order == "BA" and fault == "case":
                    name += " drift"
                rows += [
                    {"type": "suite", "suite": {"id": number,
                     "path": f"/tmp/flutter-pilot-{args.order}-{variant}/source-0/{file}"}},
                    {"type": "testStart", "test": {"id": number, "suiteID": number,
                     "name": name}},
                    {"type": "testDone", "testID": number, "result": "success",
                     "hidden": False, "skipped": args.order == "BA" and fault == "skip"},
                ]
            if not (args.order == "BA" and fault == "terminal"):
                rows.append({"type": "done", "success": True})
            (directory / f"{index}.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n")
            hits = 0 if args.order == "BA" and fault == "coverage" else 1
            if not (args.order == "BA" and fault == "missing_lcov"):
                (directory / f"{index}.info").write_text(
                    f"SF:lib/fixture.dart\nDA:1,{hits}\nend_of_record\n")
    # A deliberately optimistic pair receipt must not bypass cross-pair parsing.
    (output / "comparison.json").write_text(json.dumps({
        "status": "DIAGNOSTIC_PAIR_PASSED", "release_acceptance": False,
        "files": 36, "visible_cases": 36,
    }))
'''


class PilotWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.tools = self.workspace / ".pilot-tools"
        (self.tools / "scripts").mkdir(parents=True)
        (self.tools / "tests/fixtures").mkdir(parents=True)
        (self.workspace / "source").mkdir()
        for name in ("run-flutter-coverage.py", "count-flutter-machine-tests.py"):
            shutil.copyfile(ROOT / "scripts" / name, self.tools / "scripts" / name)
        original = (ROOT / "scripts/flutter-coverage-pilot.py").read_text()
        main = '\nif __name__ == "__main__":\n    main()\n'
        self.assertIn(main, original)
        (self.tools / "scripts/flutter-coverage-pilot.py").write_text(original.replace(main, STUB))
        # This is the executable fixture's helper binding, not the historical receipt.
        manifest = json.loads((ROOT / "tests/fixtures/flutter-coverage-pilot-20261019.json").read_text())
        manifest["helper_sha256"] = {name: hashlib.sha256((self.tools / "scripts" / name).read_bytes()).hexdigest()
                                     for name in manifest["helper_sha256"]}
        (self.tools / "tests/fixtures/flutter-coverage-pilot-20261019.json").write_text(json.dumps(manifest))
        self.env = dict(os.environ, GITHUB_WORKSPACE=str(self.workspace),
                        RUNNER_TEMP=str(self.root / "runner"), SOURCE_SHA=SOURCE,
                        BUILDER_BASE=BASE, PILOT_SHA=PILOT,
                        PILOT_CALLS=str(self.root / "calls"), PILOT_FIXTURE_FAULT="")

    def execute(self, text=None):
        return subprocess.run(["bash", "-c", shell(text or WORKFLOW.read_text(), EXECUTE)],
                              env=self.env, capture_output=True, text=True, check=False, timeout=20)

    def receipt(self):
        return Path(self.env["RUNNER_TEMP"]) / "flutter-coverage-pilot/comparison.json"

    def test_manual_single_native_job_has_frozen_inputs_and_read_only_authority(self):
        text = WORKFLOW.read_text()
        trigger = text.split("on:\n", 1)[1].split("\npermissions:", 1)[0].strip()
        self.assertIn("workflow_dispatch:", trigger)
        self.assertIn("default: abba", trigger)
        self.assertIn("- group25", trigger)
        self.assertIn("if: inputs.experiment == 'abba' || inputs.experiment == ''", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertEqual(text.count("permissions:"), 1)
        self.assertIn("runs-on: ubuntu-24.04", text)
        self.assertIn("timeout-minutes: 120", text)
        self.assertIn("timeout-minutes: 105", step(text, EXECUTE))
        self.assertIn("cancel-in-progress: false", text)
        self.assertNotIn("strategy:", text)
        self.assertNotIn("continue-on-error", text)
        self.assertNotIn("workflow_call", text)
        for value in (SOURCE, BASE, PILOT, "FLUTTER_VERSION: '3.47.7'"):
            self.assertIn(value, text)
        checkout = step(text, "Checkout exact private source with full history")
        for value in ("ref: ${{ env.SOURCE_SHA }}", "ssh-key: ${{ secrets.SRC_DEPLOY_KEY }}",
                      "ssh-known-hosts: github.com ssh-ed25519 ", "ssh-strict: true",
                      "fetch-depth: 0", "submodules: recursive", "persist-credentials: false"):
            self.assertIn(value, checkout)
        self.assertEqual(set(re.findall(r"secrets\.([A-Z0-9_]+)", text)), {"SRC_DEPLOY_KEY"})
        self.assertIn("ref: ${{ env.PILOT_SHA }}", step(text, "Checkout frozen diagnostic implementation"))
        for action in re.findall(r"uses:\s*(\S+)", text):
            self.assertRegex(action, r"@[0-9a-f]{40}$")
        upload = step(text, "Retain successful or partial diagnostic evidence")
        self.assertIn("if: always()", upload)
        self.assertIn("if-no-files-found: error", upload)
        self.assertIn("path: ${{ runner.temp }}/flutter-coverage-pilot/", upload)

    def test_group25_mode_has_fixed_inputs_cache_only_and_no_release_authority(self):
        text = WORKFLOW.read_text().split("\n  group25:\n", 1)[1]
        for required in ("if: inputs.experiment == 'group25'", "timeout-minutes: 25",
                         "SOURCE_SHA: 2af5cd844480a097f8159c0f2562b009f6c39467",
                         "ref: ${{ github.sha }}", "persist-credentials: false",
                         "YUELINK_COVERAGE_MAX_WORKERS: '1'",
                         "yue-flutter-sdk-v1-Linux-X64-3.47.7",
                         "if: always()", "timeout-minutes: 15"):
            self.assertIn(required, text)
        for forbidden in ("setup-flutter@", "apt-get", "flutter pub get", "--order",
                          "workflow run", "git push", "source-attestation", "continue-on-error"):
            self.assertNotIn(forbidden, text)
        self.assertIn("--group25 --source", text)
        self.assertIn("path: ${{ runner.temp }}/flutter-group25/", text)

    def test_real_group25_workflow_argv_identity_and_exit_are_preserved(self):
        tools = self.workspace / ".group25-tools"
        (tools / "scripts").mkdir(parents=True)
        script = tools / "scripts/flutter-coverage-pilot.py"
        script.write_text("import os,sys,json\nfrom pathlib import Path\n"
                          "Path(os.environ['PILOT_CALLS']).write_text(json.dumps(sys.argv[1:]))\n"
                          "raise SystemExit(int(os.environ['FIXTURE_RC']))\n")
        subprocess.run(["git", "init", "-q", str(tools)], check=True)
        subprocess.run(["git", "add", "."], cwd=tools, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
                        "commit", "-qm", "fixture"], cwd=tools, check=True)
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tools, text=True).strip()
        command = ["bash", "-c", shell(WORKFLOW.read_text(), "Run fixed group25 and withdrawal diagnostics once")]
        for code in (0, 23):
            result = subprocess.run(command, env=self.env | {"GITHUB_SHA": sha, "FIXTURE_RC": str(code)},
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, code, result.stderr)
            self.assertEqual(json.loads(Path(self.env["PILOT_CALLS"]).read_text()),
                             ["--group25", "--source", str(self.workspace / "source"),
                              "--output", str(Path(self.env["RUNNER_TEMP"]) / "flutter-group25/result")])
        Path(self.env["PILOT_CALLS"]).unlink()
        result = subprocess.run(command, env=self.env | {"GITHUB_SHA": "1" * 40, "FIXTURE_RC": "0"},
                                capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(Path(self.env["PILOT_CALLS"]).exists())

    def test_setup_failure_terminal_is_written_but_existing_failure_is_not_overwritten(self):
        command = ["bash", "-c", shell(WORKFLOW.read_text(), "Record group25 setup failure if no terminal exists")]
        result = subprocess.run(command, env=self.env | {"GITHUB_SHA": "1" * 40},
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        path = Path(self.env["RUNNER_TEMP"]) / "flutter-group25/result/diagnostic.json"
        receipt = json.loads(path.read_text())
        self.assertEqual(receipt["status"], "ENVIRONMENT_NOT_READY")
        self.assertFalse(receipt["release_acceptance"])
        path.write_text('{"status":"DIAGNOSTIC_PAIR_FAILED","release_acceptance":false}\n')
        before = path.read_bytes()
        subprocess.run(command, env=self.env | {"GITHUB_SHA": "1" * 40}, check=True)
        self.assertEqual(path.read_bytes(), before)
        path.unlink()
        partial = path.parent / "group/events.jsonl"
        partial.parent.mkdir()
        partial.write_text('{"type":"start"}\n')
        subprocess.run(command, env=self.env | {"GITHUB_SHA": "1" * 40}, check=True)
        receipt = json.loads(path.read_text())
        self.assertEqual(receipt["status"], "INCOMPLETE")
        self.assertEqual(receipt["commands"], [
            {"label": "group", "status": "UNKNOWN", "dispatch": "NOT_PROVED_BY_FINALIZER"}])
        self.assertFalse(receipt["release_acceptance"])
        self.assertEqual(partial.read_text(), '{"type":"start"}\n')

    def test_real_workflow_shell_invokes_exact_AB_then_BA_and_parses_all_reports(self):
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = [json.loads(line) for line in Path(self.env["PILOT_CALLS"]).read_text().splitlines()]
        self.assertEqual([call["order"] for call in calls], ["AB", "BA"])
        self.assertEqual([Path(call["output"]).name for call in calls], ["AB", "BA"])
        receipt = json.loads(self.receipt().read_text())
        self.assertEqual(receipt["status"], "DIAGNOSTIC_ABBA_PASSED")
        self.assertEqual(receipt["visible_cases"], 36)
        self.assertFalse(receipt["release_acceptance"])

    def test_first_pair_command_failure_stops_before_second_pair(self):
        self.env["PILOT_FIXTURE_FAULT"] = "fail_AB"
        result = self.execute()
        self.assertEqual(result.returncode, 23)
        self.assertEqual(len(Path(self.env["PILOT_CALLS"]).read_text().splitlines()), 1)
        self.assertFalse(self.receipt().exists())

    def test_missing_second_invocation_cannot_emit_success(self):
        text = WORKFLOW.read_text()
        command = ('          python3 "$GITHUB_WORKSPACE/.pilot-tools/scripts/flutter-coverage-pilot.py" \\\n'
                   '            --source "$GITHUB_WORKSPACE/source" \\\n'
                   '            --output "$RUNNER_TEMP/flutter-coverage-pilot/BA" --order BA\n')
        self.assertIn(command, text)
        result = self.execute(text.replace(command, ""))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.receipt().exists())

    def test_wrong_source_invocation_fails(self):
        text = WORKFLOW.read_text().replace('--source "$GITHUB_WORKSPACE/source"',
                                            '--source "$GITHUB_WORKSPACE/wrong-source"')
        result = self.execute(text)
        self.assertEqual(result.returncode, 24)
        self.assertFalse(self.receipt().exists())

    def test_cross_pair_case_skip_coverage_terminal_and_resource_changes_fail(self):
        for fault in ("case", "skip", "coverage", "terminal", "missing_lcov", "resources"):
            with self.subTest(fault=fault):
                shutil.rmtree(Path(self.env["RUNNER_TEMP"]), ignore_errors=True)
                self.env["PILOT_FIXTURE_FAULT"] = fault
                result = self.execute()
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(self.receipt().exists())

    def test_actual_git_credential_guard_refuses_retained_ssh_command(self):
        source = self.workspace / "source"
        for args in (["init", "-q"], ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "commit", "--allow-empty", "-qm", "fixture"]):
            subprocess.run(["git", *args], cwd=source, check=True, capture_output=True)
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        subprocess.run(["git", "update-ref", "refs/remotes/origin/master", sha], cwd=source, check=True)
        verify = source / "core/mihomo/third_party/verify_quic_source.py"
        verify.parent.mkdir(parents=True)
        verify.write_text("print('fixture source verification reached')\n")
        self.env["SOURCE_SHA"] = sha
        for name in ("Bind source and reject retained checkout credentials",
                     "Bind group25 source and refuse retained credentials",
                     "Bind contributors source and refuse retained credentials"):
            with self.subTest(guard=name):
                command = shell(WORKFLOW.read_text(), name)
                positive = subprocess.run(["bash", "-c", command], cwd=source, env=self.env,
                                          capture_output=True, text=True, check=False)
                self.assertEqual(positive.returncode, 0, positive.stderr)
                for key in ("core.sshCommand", "http.https://github.com/.extraheader"):
                    subprocess.run(["git", "config", key, "fixture-credential"], cwd=source, check=True)
                    negative = subprocess.run(["bash", "-c", command], cwd=source, env=self.env,
                                              capture_output=True, text=True, check=False)
                    self.assertNotEqual(negative.returncode, 0)
                    self.assertIn("checkout credentials must be removed", negative.stdout)
                    self.assertNotIn("fixture source verification reached", negative.stdout)
                    subprocess.run(["git", "config", "--unset", key], cwd=source, check=True)


    def test_real_contributors_workflow_argv_identity_and_exit_are_preserved(self):
        tools = self.workspace / ".contributors-tools"
        (tools / "scripts").mkdir(parents=True)
        script = tools / "scripts/flutter-coverage-pilot.py"
        script.write_text("import os,sys,json\nfrom pathlib import Path\n"
                          "Path(os.environ['PILOT_CALLS']).write_text(json.dumps(sys.argv[1:]))\n"
                          "raise SystemExit(int(os.environ['FIXTURE_RC']))\n")
        subprocess.run(["git", "init", "-q", str(tools)], check=True)
        subprocess.run(["git", "add", "."], cwd=tools, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
                        "commit", "-qm", "fixture"], cwd=tools, check=True)
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tools, text=True).strip()
        command = ["bash", "-c", shell(WORKFLOW.read_text(), "Run four fixed contributor singletons once")]
        for code in (0, 23):
            result = subprocess.run(command, env=self.env | {"GITHUB_SHA": sha, "FIXTURE_RC": str(code)},
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, code, result.stderr)
            self.assertEqual(json.loads(Path(self.env["PILOT_CALLS"]).read_text()),
                             ["--contributors", "--source", str(self.workspace / "source"),
                              "--output", str(Path(self.env["RUNNER_TEMP"]) / "flutter-contributors/result")])
        Path(self.env["PILOT_CALLS"]).unlink()
        result = subprocess.run(command, env=self.env | {"GITHUB_SHA": "1" * 40, "FIXTURE_RC": "0"},
                                capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(Path(self.env["PILOT_CALLS"]).exists())

    def test_contributors_manual_closed_job_uses_only_verified_cache(self):
        text = WORKFLOW.read_text()
        trigger = text.split("on:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(re.findall(r"^  ([a-z_]+):", trigger, re.MULTILINE), ["workflow_dispatch"])
        self.assertIn("- contributors", trigger)
        job = text.split("\n  contributors:\n", 1)[1]
        for item in ("if: inputs.experiment == 'contributors'", "timeout-minutes: 45",
                     "timeout-minutes: 40", "SOURCE_SHA: b2d8d1dcef0b1e3c13000a536ae4bab5870354c6",
                     "YUELINK_COVERAGE_MAX_WORKERS: '1'", "yue-flutter-sdk-v1-Linux-X64-3.47.7",
                     "ref: ${{ github.sha }}", "persist-credentials: false", "if-no-files-found: error"):
            self.assertIn(item, job)
        for item in ("apt-get", "setup-flutter@", "flutter pub get", "--order", "--manifest",
                     "git push", "workflow run", "source-attestation", "continue-on-error"):
            self.assertNotIn(item, job)
        self.assertEqual(job.count("--contributors --source"), 1)
        self.assertIn("if: always()", step(text, "Retain complete or partial contributors evidence"))

    def test_contributors_missing_invocation_or_partial_cannot_be_complete(self):
        command = ["bash", "-c", shell(WORKFLOW.read_text(), "Record contributors setup failure if no terminal exists")]
        env = self.env | {"GITHUB_SHA": "1" * 40}
        result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        path = Path(env["RUNNER_TEMP"]) / "flutter-contributors/result/diagnostic.json"
        receipt = json.loads(path.read_text())
        self.assertEqual(receipt["status"], "ENVIRONMENT_NOT_READY")
        self.assertFalse(receipt["diagnostic_complete"])
        self.assertIsNone(receipt["coverage_equal"])
        self.assertFalse(receipt["release_acceptance"])
        path.write_text('{"status":"DIAGNOSTIC_FAILED","release_acceptance":false}\n')
        before = path.read_bytes()
        subprocess.run(command, env=env, check=True)
        self.assertEqual(path.read_bytes(), before)
        path.unlink()
        partial = path.parent / "2/events.jsonl"
        partial.parent.mkdir()
        partial.write_text('{"type":"start"}\n')
        subprocess.run(command, env=env, check=True)
        receipt = json.loads(path.read_text())
        self.assertEqual(receipt["status"], "INCOMPLETE")
        self.assertEqual(receipt["commands"], [{"label": "2", "status": "UNKNOWN", "dispatch": "NOT_PROVED_BY_FINALIZER"}])
        self.assertFalse(receipt["diagnostic_complete"])
        self.assertIsNone(receipt["coverage_equal"])
        self.assertFalse(receipt["release_acceptance"])


if __name__ == "__main__":
    unittest.main()
