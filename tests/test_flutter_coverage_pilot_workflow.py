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
PILOT = "d91bc95ffc8b057e7301a6eeced080f6b3da4a7c"
HISTORICAL_PILOT = "9965321062a98a273fb92d8cd00cdc004f165866"
HISTORICAL_SOURCE_SHA256 = "c38cc2f6139471734cd33da2b7a697656130531337c09d2f487ab031aa017ccf"
HISTORICAL_MANIFEST_SHA256 = "cbf0c7a57f6f2cc5f5f107df6c17c2c80605269819120fb6d9fcd4c40eb3bf3e"


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


# Exact schema1 manifest/grouping functions from immutable pilot996. The fixture
# reconstructs and hashes its complete original module before substituting only
# expensive CLI execution; a shallow policy checkout needs no historical fetch.
HISTORICAL_MANIFEST_AND_GROUPS = r'''
def manifest(path: Path) -> dict:
    data = json.loads(path.read_text())
    files = data["files"]
    digest = hashlib.sha256(("\n".join(files) + "\n").encode()).hexdigest()
    if (
        data["schema"] != 1
        or len(files) != 36
        or files != sorted(set(files))
        or digest != data["inventory_sha256"]
        or data["workers"] != 4
        or data["group_size"] != 8
        or data["timeout_seconds"] != 300
    ):
        raise ValueError("pilot inventory or execution contract changed")
    if any(
        not p.startswith("test/")
        or ".." in Path(p).parts
        or not p.endswith("_test.dart")
        for p in files
    ):
        raise ValueError("pilot path escaped the test corpus")
    expected_isolated = {
        "test/services/user_custom_rules_test.dart",
        "test/services/subscription_parser_test.dart",
        "test/core/storage/persisted_schema_test.dart",
        "test/core/storage/settings_service_schema_test.dart",
        "test/governance/mihomo_fork_ledger_test.dart",
    }
    if set(data["isolated"]) != expected_isolated or not expected_isolated.issubset(
        files
    ):
        raise ValueError("required compiler isolation boundary missing")
    for filename, digest in data["helper_sha256"].items():
        if filename not in {
            "run-flutter-coverage.py",
            "count-flutter-machine-tests.py",
        }:
            raise ValueError("unknown pilot helper")
        if (
            hashlib.sha256((ROOT / "scripts" / filename).read_bytes()).hexdigest()
            != digest
        ):
            raise ValueError("pilot helper differs from the frozen builder")
    if set(data["helper_sha256"]) != {
        "run-flutter-coverage.py",
        "count-flutter-machine-tests.py",
    }:
        raise ValueError("missing frozen builder helper")
    return data


def groups(data: dict, variant: str) -> list[list[str]]:
    early = [[p] for p in data["isolated"]]
    rest = [p for p in data["files"] if p not in data["isolated"]]
    size = 1 if variant == "A" else data["group_size"]
    return early + [rest[i : i + size] for i in range(0, len(rest), size)]


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
        manifest_text = (ROOT / "tests/fixtures/flutter-coverage-pilot-20261019.json").read_text()
        if getattr(self, "historical", False):
            original = (original[:original.index("def manifest(")] +
                        HISTORICAL_MANIFEST_AND_GROUPS.lstrip("\n") +
                        original[original.index("def relative("):])
            self.assertEqual(hashlib.sha256(original.encode()).hexdigest(), HISTORICAL_SOURCE_SHA256)
            historical = json.loads(manifest_text)
            historical["schema"] = 1
            del historical["in_place_isolated"]
            manifest_text = json.dumps(historical, indent=2) + "\n"
            self.assertEqual(hashlib.sha256(manifest_text.encode()).hexdigest(), HISTORICAL_MANIFEST_SHA256)
        main = '\nif __name__ == "__main__":\n    main()\n'
        self.assertIn(main, original)
        (self.tools / "scripts/flutter-coverage-pilot.py").write_text(original.replace(main, STUB))
        (self.tools / "tests/fixtures/flutter-coverage-pilot-20261019.json").write_text(manifest_text)
        self.env = dict(os.environ, GITHUB_WORKSPACE=str(self.workspace),
                        RUNNER_TEMP=str(self.root / "runner"), SOURCE_SHA=SOURCE,
                        BUILDER_BASE=BASE, PILOT_SHA=HISTORICAL_PILOT if getattr(self, "historical", False) else PILOT,
                        PILOT_CALLS=str(self.root / "calls"), PILOT_FIXTURE_FAULT="")

    def execute(self, text=None):
        return subprocess.run(["bash", "-c", shell(text or WORKFLOW.read_text(), EXECUTE)],
                              env=self.env, capture_output=True, text=True, check=False, timeout=20)

    def receipt(self):
        return Path(self.env["RUNNER_TEMP"]) / "flutter-coverage-pilot/comparison.json"

    def test_manual_single_native_job_has_frozen_inputs_and_read_only_authority(self):
        text = WORKFLOW.read_text()
        trigger = text.split("on:\n", 1)[1].split("\npermissions:", 1)[0].strip()
        self.assertTrue(trigger.startswith("workflow_dispatch:"))
        self.assertNotIn("schedule:", trigger)
        self.assertNotIn("push:", trigger)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertEqual(text.count("permissions:"), 1)
        self.assertIn("runs-on: ubuntu-24.04", text)
        self.assertIn("inputs.diagnostic_scope == 'group7' && 40 || 120", text)
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
        self.assertEqual(re.findall(r"secrets\.([A-Z0-9_]+)", text), ["SRC_DEPLOY_KEY"])
        self.assertIn("ref: ${{ env.PILOT_SHA }}", step(text, "Checkout frozen diagnostic implementation"))
        for action in re.findall(r"uses:\s*(\S+)", text):
            self.assertRegex(action, r"@[0-9a-f]{40}$")
        upload = step(text, "Retain successful or partial diagnostic evidence")
        self.assertIn("if: always()", upload)
        self.assertIn("if-no-files-found: error", upload)
        self.assertIn("path: ${{ runner.temp }}/flutter-coverage-pilot/", upload)

    def test_modes_pin_new_abba_pilot_and_exact_historical_group7_separately(self):
        text = WORKFLOW.read_text()
        expected = ("  PILOT_SHA: ${{ inputs.diagnostic_scope == 'abba' && '" + PILOT +
                    "' || '" + HISTORICAL_PILOT + "' }}")
        self.assertIn(expected, text)
        self.assertNotEqual(PILOT, HISTORICAL_PILOT)
        manifest = json.loads((self.tools / "tests/fixtures/flutter-coverage-pilot-20261019.json").read_text())
        self.assertEqual(manifest["schema"], 2)
        self.assertEqual(len(manifest["in_place_isolated"]), 2)

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
        command = shell(WORKFLOW.read_text(), "Bind source and reject retained checkout credentials")
        positive = subprocess.run(["bash", "-c", command], cwd=source, env=self.env,
                                  capture_output=True, text=True, check=False)
        self.assertEqual(positive.returncode, 0, positive.stderr)
        subprocess.run(["git", "config", "core.sshCommand", "ssh -i /fixture-key"], cwd=source, check=True)
        negative = subprocess.run(["bash", "-c", command], cwd=source, env=self.env,
                                  capture_output=True, text=True, check=False)
        self.assertNotEqual(negative.returncode, 0)
        self.assertIn("checkout credentials must be removed", negative.stdout)
        self.assertNotIn("fixture source verification reached", negative.stdout)

GROUP7 = "Run bounded group7 reproduction and conditional isolation control"


class Group7WorkflowTests(unittest.TestCase):
    historical = True

    def setUp(self):
        import importlib.util
        from collections import Counter
        PilotWorkflowTests.setUp(self)
        code = shell(WORKFLOW.read_text(), GROUP7).split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY\n", 1)[0]
        self.ns = {"__name__": "group7_consumer"}
        exec(compile(code, str(WORKFLOW), "exec"), self.ns)  # noqa: S102 — execute the reviewed workflow consumer
        spec = importlib.util.spec_from_file_location("group7_pilot", self.tools / "scripts/flutter-coverage-pilot.py")
        self.pilot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.pilot)
        self.data = self.pilot.manifest(self.pilot.DEFAULT_MANIFEST)
        self.files = self.pilot.groups(self.data, "B")[7]
        self.counter = self.pilot.module("group7_counter", ROOT / "scripts/count-flutter-machine-tests.py")
        self.worker = self.root / "worker"
        self.worker.mkdir()
        self.output = self.root / "group7"
        self.output.mkdir()
        self.env["DIAGNOSTIC_SCOPE"] = "group7"
        self.calls, self.fault, self.original = [], "", "matching"
        self.case_counts = [9, 3, 25, 9, 3, 9, 12, 6]
        cases = Counter()
        for i, file in enumerate(self.files):
            for number in range(self.case_counts[i]):
                cases[(file, f"case {number}", i == 3 and number < 2)] += 1
        reference = dict(self.ns["REFERENCE"])
        reference.update(cases_sha256=self.ns["case_hash"](cases),
                         prefix_cases_sha256=self.ns["case_hash"](Counter({
                             k: v for k, v in cases.items() if k[0] != self.files[7]})),
                         coverage_sha256=self.ns["canonical"]([
                             ["lib/fixture.dart", "DA", "1", False],
                             ["lib/fixture.dart", "DA", "1", True]]), coverage_rows=2)
        self.fixture_reference = reference

    def bind_fixture(self):
        # Only the expensive execution and prior corpus are synthetic, not the consumers.
        self.ns["REFERENCE"] = self.fixture_reference
        self.ns["REFERENCE_SHA256"] = self.ns["canonical"](self.fixture_reference)

    def fake_run(self, command, **kwargs):
        if command[0] != "/usr/bin/time":
            return subprocess.CompletedProcess(command, 0)
        self.calls.append(command)
        self.assertEqual(kwargs["timeout"], 350)
        self.assertEqual(kwargs["env"]["TZ"], "UTC")
        self.assertEqual(command[4:8], ["timeout", "--signal=TERM", "--kill-after=30s", "300"])
        for flag in ("--no-pub", "--coverage", "--machine", "--concurrency=1"):
            self.assertIn(flag, command)
        index = command.index("--coverage-path") + 1
        lcov, selected = Path(command[index]), command[index + 1:]
        original = lcov.stem == "original"
        failed = original and self.original != "success"
        worker = Path(kwargs["cwd"])
        self.assertFalse((worker / "build/test_cache").exists())
        events, identity, loading = [{"type": "start"}], 0, None
        for suite_id, file in enumerate(selected[:7] if failed else selected):
            events.append({"type": "suite", "suite": {"id": suite_id, "path": str(worker / file)}})
            loading = identity
            events += [{"type": "testStart", "test": {
                "id": identity, "suiteID": suite_id, "name": "loading " + str(worker / file)}},
                {"type": "testDone", "testID": identity, "result": "success", "hidden": True, "skipped": False}]
            identity += 1
            i = self.files.index(file)
            for number in range(self.case_counts[i]):
                name, skip = f"case {number}", i == 3 and number < 2
                if not original and self.fault == "case":
                    name += " drift"
                if not original and self.fault == "skip":
                    skip = not skip
                events += [{"type": "testStart", "test": {
                    "id": identity, "suiteID": suite_id, "name": name}},
                    {"type": "testDone", "testID": identity, "result": "success", "hidden": False, "skipped": skip}]
                identity += 1
            events += [{"type": "testStart", "test": {
                "id": identity, "suiteID": suite_id, "name": "(tearDownAll)"}},
                {"type": "testDone", "testID": identity, "result": "success", "hidden": True, "skipped": False}]
            identity += 1
        if failed:
            events.append({"type": "error", "testID": loading,
                           "error": "Different timeout" if self.original == "different" else "Shell subprocess crashed with segmentation fault.",
                           "isFailure": False, "stackTrace": "FlutterTesterTestDevice.finished"})
        elif self.fault != "terminal":
            events.append({"type": "done", "success": True})
        kwargs["stdout"].write("\n".join(json.dumps(e) for e in events) + "\n")
        if failed:
            kwargs["stdout"].write("unhandled error during finalization of test:\n")
        if not failed and self.fault != "missing_lcov":
            hit = 0 if self.fault == "coverage" else 1
            lcov.write_text(f"SF:lib/fixture.dart\nDA:1,{hit}\nend_of_record\n")
        if self.fault == "outer_timeout":
            raise subprocess.TimeoutExpired(command, 350)
        return subprocess.CompletedProcess(command, 124 if failed else (23 if self.fault == "control_exit" else 0))

    def invoke(self, main=False):
        from contextlib import contextmanager
        from unittest.mock import patch

        @contextmanager
        def worker(*_args):
            yield self.worker

        def output(command, **_kwargs):
            if command == ["git", "rev-parse", "HEAD"]:
                return SOURCE + "\n"
            if command[:2] == ["git", "ls-files"]:
                return "\n".join(self.files)
            if command == ["flutter", "--version", "--machine"]:
                return json.dumps(self.data["sdk"] if self.fault != "sdk" else {})
            raise AssertionError(command)

        self.bind_fixture()
        cache = self.worker / "build/test_cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "stale").write_text("compiler cache")
        with patch.dict(os.environ, self.env), patch.dict(self.ns, prepared_worker=worker), \
                patch.object(subprocess, "run", side_effect=self.fake_run), \
                patch.object(subprocess, "check_output", side_effect=output), \
                patch("platform.system", return_value="Linux"), patch("platform.machine", return_value="x86_64"), \
                patch("platform.platform", return_value="Linux-x64-fixture"):
            if main:
                return self.ns["main"]()
            return self.ns["experiment"](self.workspace / "source", self.output, self.data,
                                         self.files, self.pilot, self.counter, None)

    def test_historical_group7_keeps_exact_schema1_and_original_grouping(self):
        self.assertEqual(self.env["PILOT_SHA"], HISTORICAL_PILOT)
        self.assertEqual(self.data["schema"], 1)
        self.assertNotIn("in_place_isolated", self.data)
        self.assertEqual(len(self.pilot.groups(self.data, "B")), 9)
        self.assertEqual(self.files, self.ns["REFERENCE"]["files"])
        self.assertEqual(len(self.files), 8)

    def test_group7_dispatch_is_explicit_bounded_and_abba_is_preserved(self):
        text = WORKFLOW.read_text()
        self.assertIn("default: group7", text)
        self.assertIn("options: [group7, abba]", text)
        self.assertIn("if: inputs.diagnostic_scope == 'group7'", step(text, GROUP7))
        self.assertIn("timeout-minutes: 25", step(text, GROUP7))
        self.assertIn("if: inputs.diagnostic_scope == 'abba'", step(text, EXECUTE))
        self.assertIn("inputs.diagnostic_scope == 'group7' && 40 || 120", text)
        self.assertNotIn("actions: read", text)
        self.assertNotIn("--manifest", shell(text, GROUP7))

    def test_actual_prior_reference_binds_raw_reports_and_canonical_expectations(self):
        from unittest.mock import patch
        reference = self.ns["REFERENCE"]
        self.assertEqual(self.ns["canonical"](reference), self.ns["REFERENCE_SHA256"])
        self.assertEqual(reference["cases_sha256"], "60f97696b06dbea7c8fd401300525a68e5f3002074773cbde2ddfaf9c1054474")
        self.assertEqual(reference["coverage_sha256"], "bf27a06a11049742d6cd6005eff8891e843cef9de8700eb571eb10237ed6e862")
        self.assertEqual((reference["visible_cases"], reference["skipped_cases"], reference["coverage_rows"]), (76, 2, 17654))
        self.assertEqual(set(reference["raw_sha256"]), {
            *[f"AB/A/{i}.{suffix}" for i in range(21, 29) for suffix in ("jsonl", "info")],
            "execution.json", "AB/input.json", "AB/A/groups.json", "AB/B/groups.json"})
        with patch.dict(os.environ, self.env):
            self.assertEqual(self.ns["validate_reference"](self.data, self.pilot), self.files)
            reference["raw_sha256"]["AB/A/27.jsonl"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "binding changed"):
                self.ns["validate_reference"](self.data, self.pilot)

    def test_matching_failure_runs_only_original_prefix6_single7_single8(self):
        self.assertEqual(self.invoke(), "REPRODUCED_WITH_ISOLATION_CONTROL_PASS")
        self.assertEqual([cmd[cmd.index("--coverage-path") + 2:] for cmd in self.calls],
                         [self.files, self.files[:6], self.files[6:7], self.files[7:]])
        self.assertEqual(len(list(self.output.glob("*.timing.json"))), 4)

    def test_original_success_stops_without_isolation_or_retry(self):
        self.original = "success"
        self.assertEqual(self.invoke(), "NOT_REPRODUCED")
        self.assertEqual(len(self.calls), 1)

    def test_different_original_failure_stops_without_control(self):
        self.original = "different"
        with self.assertRaisesRegex(ValueError, "different or incomplete signature"):
            self.invoke()
        self.assertEqual(len(self.calls), 1)

    def test_control_case_skip_coverage_terminal_exit_and_missing_lcov_fail(self):
        for fault in ("case", "skip", "coverage", "terminal", "missing_lcov", "control_exit"):
            with self.subTest(fault=fault):
                self.fault = fault
                self.calls.clear()
                for f in self.output.iterdir():
                    f.unlink()
                with self.assertRaises((ValueError, FileNotFoundError)):
                    self.invoke()
                self.assertLessEqual(len(self.calls), 4)

    def test_outer_timeout_is_preserved_and_never_retried(self):
        self.fault = "outer_timeout"
        with self.assertRaises(subprocess.TimeoutExpired):
            self.invoke()
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(json.loads((self.output / "original.timing.json").read_text())["exit_code"])

    def test_missing_reference_and_changed_group_order_fail_closed(self):
        from unittest.mock import patch
        with patch.dict(os.environ, self.env):
            broken = dict(self.data, files=list(reversed(self.data["files"])))
            with self.assertRaisesRegex(ValueError, "group7 input differs"):
                self.ns["validate_reference"](broken, self.pilot)
            self.ns["REFERENCE"].pop("cases_sha256")
            with self.assertRaisesRegex(ValueError, "binding changed"):
                self.ns["validate_reference"](self.data, self.pilot)

    def test_wrong_finalization_boundary_cannot_enable_control(self):
        from unittest.mock import patch
        self.bind_fixture()
        with patch.object(subprocess, "run", side_effect=self.fake_run):
            result = self.ns["execute"](self.worker, self.output, "original", self.files)
        original = result["report"].read_text()
        for changed in (original.replace("(tearDownAll)", "ordinary"),
                        original + '{"type":"done","success":true}\n',
                        original.replace("FlutterTesterTestDevice.finished", "unrelated")):
            result["report"].write_text(changed)
            self.assertFalse(self.ns["matching_failure"](result, self.worker, self.files, self.pilot))

    def test_actual_main_nonrelease_receipt_and_sdk_rejection(self):
        self.invoke(main=True)
        receipt = Path(self.env["RUNNER_TEMP"]) / "flutter-coverage-pilot/group7/comparison.json"
        result = json.loads(receipt.read_text())
        self.assertEqual(result["status"], "REPRODUCED_WITH_ISOLATION_CONTROL_PASS")
        self.assertFalse(result["release_acceptance"])
        self.fault = "sdk"
        with self.assertRaisesRegex(ValueError, "SDK differs"):
            self.invoke(main=True)

    def test_actual_shell_reaches_main_and_rejects_unselected_mode(self):
        self.env["DIAGNOSTIC_SCOPE"] = "invalid"
        result = subprocess.run(["bash", "-c", shell(WORKFLOW.read_text(), GROUP7)],
                                env=self.env, capture_output=True, text=True, timeout=20, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires explicit native Linux x64", result.stderr)

    def test_real_worktree_cleanup_and_dirty_source_rejection(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        source = self.workspace / "source"
        subprocess.run(["git", "init", "-q"], cwd=source, check=True)
        (source / "tracked").write_text("clean")
        subprocess.run(["git", "add", "tracked"], cwd=source, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-qm", "fixture"], cwd=source, check=True)
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        real_run = subprocess.run
        offline = []

        def run(command, **kwargs):
            if command[0] == "flutter":
                offline.append(command)
                return subprocess.CompletedProcess(command, 0)
            return real_run(command, **kwargs)

        runner = SimpleNamespace(add_gitlinks=lambda *_args: None)
        with patch.object(subprocess, "run", side_effect=run), \
                self.assertRaises(subprocess.CalledProcessError), \
                self.ns["prepared_worker"](source, self.output, {"source_sha": sha}, runner) as worker:
            self.assertEqual((worker / "tracked").read_text(), "clean")
            (worker / "tracked").write_text("changed")
        self.assertEqual(offline, [["flutter", "pub", "get", "--offline", "--enforce-lockfile"]])
        listed = subprocess.check_output(["git", "worktree", "list", "--porcelain"], cwd=source, text=True)
        self.assertEqual(listed.count("worktree "), 1)
        self.assertEqual((source / "tracked").read_text(), "clean")

    def test_cache_symlink_fails_before_command(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "preserve").write_text("unchanged")
        (self.worker / "build").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "cache escaped"):
            self.ns["execute"](self.worker, self.output, "original", self.files)
        self.assertEqual((outside / "preserve").read_text(), "unchanged")

    def test_actual_main_failure_retains_explicit_nonrelease_receipt(self):
        self.fault = "control_exit"
        with self.assertRaisesRegex(ValueError, "command failed"):
            self.invoke(main=True)
        receipt = Path(self.env["RUNNER_TEMP"]) / "flutter-coverage-pilot/group7/comparison.json"
        self.assertEqual(json.loads(receipt.read_text())["status"], "DIAGNOSTIC_FAILED")
        self.assertFalse(json.loads(receipt.read_text())["release_acceptance"])


if __name__ == "__main__":
    unittest.main()
