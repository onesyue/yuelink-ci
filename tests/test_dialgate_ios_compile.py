"""Offline orchestration assurance; no Xcode, signing or product build is run."""

from __future__ import annotations

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


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ios = load("ios_compile_test", "dialgate-ios-compile.py")
generator = load("ios_generator_test", "generate-dialgate-verify.py")
fixture = load("ios_fixture_test", "dialgate-ios-fixture.py")


class IOSCompileTests(unittest.TestCase):
    def test_pinned_sdk_patch_changes_only_four_signing_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            sdk = Path(directory)
            sources = {
                ios.IOS_TARGET: "before\nawait _signFramework(environment, frameworkBinary, buildMode);\n"
                "middle\nawait _signFramework(environment, frameworkBinary, buildMode);\n"
                "await _signFramework(environment, outputFile, BuildMode.debug);\nafter\n",
                ios.NATIVE_TARGET: "link();\nawait codesignDylib(codesignIdentity, buildMode, frameworkDir);\nreturn installedFiles;\n",
            }
            hashes = {}
            for name, text in sources.items():
                p = sdk / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
                hashes[name] = ios.sha(p.read_bytes())
            with patch.object(ios, "SDK_HASHES", hashes):
                result = ios.patch_sdk_sources(sdk)
                self.assertEqual(set(result), set(sources))
                self.assertEqual(
                    sum(
                        (sdk / n).read_text().count("signing deliberately omitted")
                        for n in sources
                    ),
                    4,
                )
                self.assertIn("link();", (sdk / ios.NATIVE_TARGET).read_text())
                self.assertIn(
                    "return installedFiles;", (sdk / ios.NATIVE_TARGET).read_text()
                )
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    ios.patch_sdk_sources(sdk)
            # Even an updated hash cannot hide a duplicated/missing callsite.
            for count in (0, 2):
                (sdk / ios.IOS_TARGET).write_text(sources[ios.IOS_TARGET])
                (sdk / ios.NATIVE_TARGET).write_text(
                    "await codesignDylib(codesignIdentity, buildMode, frameworkDir);\n"
                    * count
                )
                altered = {n: ios.sha((sdk / n).read_bytes()) for n in sources}
                with (
                    patch.object(ios, "SDK_HASHES", altered),
                    self.assertRaisesRegex(ValueError, "count changed"),
                ):
                    ios.patch_sdk_sources(sdk)

    def test_sdk_copy_dereferences_cache_links_and_leaves_original_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original"
            original.mkdir()
            external = root / "shared-snapshot"
            external.write_bytes(b"original snapshot")
            (original / ".git").mkdir()
            cache = original / "bin/cache"
            cache.mkdir(parents=True)
            (cache / "flutter_tools.snapshot").symlink_to(external)
            (cache / "flutter_tools.stamp").write_text("old stamp")
            with (
                patch.object(
                    ios,
                    "git",
                    side_effect=[ios.FLUTTER_COMMIT, "", str(root / "isolated")],
                ),
                patch.object(ios, "patch_sdk_sources", return_value={}),
            ):
                ios.prepare_sdk(original, root / "isolated")
            self.assertEqual(external.read_bytes(), b"original snapshot")
            self.assertTrue((cache / "flutter_tools.snapshot").is_symlink())
            self.assertFalse(
                (root / "isolated/bin/cache/flutter_tools.snapshot").exists()
            )
            self.assertFalse((root / "isolated/bin/cache/flutter_tools.stamp").exists())
            with self.assertRaises(ValueError):
                ios.prepare_sdk(original, original)

    def test_signer_guard_rejects_adhoc_signing_without_success_stub(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            violation = root / "violation"
            ios.install_signing_guard(root / "bin", violation)
            for args in (
                ["--force", "--sign", "-", "artifact"],
                ["-s", "-", "artifact"],
                ["--verify", "--deep", "artifact"],
            ):
                result = subprocess.run(
                    [str(root / "bin/codesign"), *args],
                    env={**os.environ, "IOS_SIGNING_VIOLATION": str(violation)},
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 97)
            self.assertEqual(len(violation.read_text().splitlines()), 3)

    def test_compile_command_runs_build_only_and_propagates_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool = root / "xcodebuild"
            tool.write_text(
                "#!" + sys.executable + "\nimport json,os,pathlib,sys\n"
                'pathlib.Path(os.environ["ARGS"]).write_text(json.dumps(sys.argv[1:]))\n'
                'print("private simulated diagnostic")\nsys.exit(65)\n'
            )
            tool.chmod(0o755)
            env = {
                **os.environ,
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "ARGS": str(root / "args"),
            }
            code = ios.run(
                ios.compile_command(root / "derived", root / "result"),
                root,
                env,
                root / "raw",
            )
            self.assertEqual(code, 65)
            args = json.loads((root / "args").read_text())
            self.assertEqual(args[-1], "build")
            self.assertIn("generic/platform=iOS", args)
            for flag in ios.SIGNING_SETTINGS:
                self.assertIn(flag, args)
            for forbidden in (
                "archive",
                "-exportArchive",
                "install",
                "-allowProvisioningUpdates",
            ):
                self.assertNotIn(forbidden, args)
            self.assertIn("private simulated diagnostic", (root / "raw").read_text())

    def test_resolved_settings_require_both_no_flags_and_empty_identities(self):
        settings = dict(x.split("=", 1) for x in ios.SIGNING_SETTINGS)
        good = [{"target": "Runner", "buildSettings": settings}]
        self.assertEqual(ios.signing_disabled(json.dumps(good), "Runner"), settings)
        for rows in (
            [],
            good + good,
            [{"target": "PacketTunnel", "buildSettings": settings}],
            [
                {
                    "target": "Runner",
                    "buildSettings": {**settings, "CODE_SIGN_IDENTITY": "-"},
                }
            ],
        ):
            with self.assertRaises(ValueError):
                ios.signing_disabled(json.dumps(rows), "Runner")

    def test_actual_compiler_evidence_is_not_inferred_from_files_or_settings(self):
        text = "SwiftCompile normal arm64 /tmp/AppDelegate.swift (in target 'Runner' from project 'Runner')\n"
        text += "SwiftEmitModule normal arm64 (in target 'PacketTunnel' from project 'Runner')\n"
        text += "CODE_SIGNING_ALLOWED = NO\n"
        good = ios.compile_evidence(text)
        self.assertEqual(good["compiled_targets"], ["Runner", "PacketTunnel"])
        self.assertFalse(good["signing_operation"])
        self.assertEqual(
            ios.compile_evidence("BUILD SUCCEEDED\nRunner.app PacketTunnel.appex")[
                "compiled_targets"
            ],
            [],
        )
        for operation in (
            "CodeSign /tmp/App.framework",
            "/usr/bin/codesign --force --sign - /tmp/App",
        ):
            self.assertTrue(ios.compile_evidence(text + operation)["signing_operation"])
        missing = "/tmp/AppDelegate.swift:1001:72: error: missing argument for parameter 'extensionProfile' in call\n"
        self.assertTrue(ios.compile_evidence(missing)["expected_negative_diagnostic"])
        self.assertFalse(
            ios.compile_evidence("SDK missing; extensionProfile")[
                "expected_negative_diagnostic"
            ]
        )

    def test_fixture_removes_one_real_required_argument_and_rejects_drift(self):
        original = ("prefix\n" + ios.ARGUMENT + "\nsuffix").encode()
        with patch.object(ios, "DELEGATE_HASH", ios.sha(original)):
            changed = ios.mutate_fixture(original)
            self.assertEqual(
                changed, ("prefix\n" + ios.REPLACEMENT + "\nsuffix").encode()
            )
            for bad in (original + b"\n", changed, original + original):
                with self.assertRaises(ValueError):
                    ios.mutate_fixture(bad)

    def test_real_git_identity_rejects_dirty_source_and_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "pubspec.lock").write_text("fixture")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
            head = ios.git(root, "rev-parse", "HEAD")
            self.assertEqual(
                ios.source_identity(root, head)["locks"],
                {"pubspec.lock": ios.sha(b"fixture")},
            )
            (root / "unexpected.dart").write_text("fixture")
            with self.assertRaisesRegex(ValueError, "dirty"):
                ios.source_identity(root, head)
            (root / "unexpected.dart").unlink()
            subprocess.run(
                ["git", "-C", str(root), "config", "core.sshCommand", "fixture-only"],
                check=True,
            )
            with self.assertRaisesRegex(ValueError, "credentials"):
                ios.source_identity(root, head)

    def test_native_summary_executes_and_keeps_negative_red(self):
        workflow = (ROOT / ".github/workflows/dialgate-verify.yml").read_text()
        summary = generator.job(workflow, "native_compile_summary")
        script = summary.split("        run: |\n", 1)[1]
        script = "\n".join(line[10:] for line in script.splitlines())
        with tempfile.TemporaryDirectory() as directory:
            for mode, android, ios_result, passed in (
                ("positive", "success", "success", True),
                ("positive", "skipped", "success", False),
                ("negative", "skipped", "failure", False),
                ("negative", "skipped", "cancelled", False),
                ("negative", "skipped", "skipped", False),
                ("unknown", "skipped", "success", False),
            ):
                env = dict(
                    os.environ,
                    MODE=mode,
                    ANDROID=android,
                    IOS=ios_result,
                    BINDING="success",
                    GITHUB_STEP_SUMMARY=str(Path(directory) / "summary"),
                )
                result = subprocess.run(
                    ["bash", "-c", script], env=env, capture_output=True, check=False
                )
                self.assertEqual(result.returncode == 0, passed)

    def test_ios_workflow_is_separate_required_compile_and_safe_upload(self):
        workflow = (ROOT / ".github/workflows/dialgate-verify.yml").read_text()
        job = generator.job(workflow, "ios_swift_compile")
        self.assertIn("runs-on: macos-26", job)
        self.assertIn("contents: read", job)
        self.assertIn("submodules: recursive", job)
        self.assertIn("fetch-depth: 2", job)
        self.assertIn('--expected-source "$EXPECTED_CANDIDATE"', job)
        self.assertIn('--expected-builder "$EXPECTED_BUILDER"', job)
        self.assertIn("ios-compile-receipt/receipt.json", job)
        for forbidden in (
            "continue-on-error",
            "id-token:",
            "actions: write",
            ".xcresult",
            "*.app",
            "build/ios",
            "release.sh",
        ):
            self.assertNotIn(forbidden, job)
        summary = generator.job(workflow, "native_compile_summary")
        self.assertIn("ios_swift_compile", summary)
        self.assertIn('test "$IOS" = success', summary)
        self.assertIn('test "$ANDROID" = success', summary)
        self.assertNotIn(
            "ios_swift_compile", generator.job(workflow, "dial_gate_summary")
        )
        policy = json.loads((ROOT / "policy/dialgate-source.json").read_text())
        self.assertEqual(
            (policy["sourcePr"], policy["sourceCommit"]), (131, ios.POSITIVE_SOURCE)
        )

    def test_negative_and_unrelated_failure_can_never_turn_required_check_green(self):
        evidence = {
            "compiled_targets": ["Runner", "PacketTunnel"],
            "signing_operation": False,
            "expected_negative_diagnostic": True,
        }
        self.assertEqual(
            ios.compilation_outcome(65, evidence, "negative", False),
            ("expected_swift_failure", 65),
        )
        self.assertEqual(
            ios.compilation_outcome(0, evidence, "negative", True),
            ("negative_control_not_proven", 1),
        )
        self.assertEqual(
            ios.compilation_outcome(
                65,
                {**evidence, "expected_negative_diagnostic": False},
                "negative",
                False,
            ),
            ("negative_control_not_proven", 65),
        )
        self.assertEqual(
            ios.compilation_outcome(0, evidence, "positive", True),
            ("passed_unsigned_compilation", 0),
        )
        for bad in (
            {**evidence, "compiled_targets": []},
            {**evidence, "signing_operation": True},
        ):
            self.assertNotEqual(ios.compilation_outcome(0, bad, "positive", True)[1], 0)
        self.assertNotEqual(
            ios.compilation_outcome(0, evidence, "positive", False)[1], 0
        )

    def test_actual_build_is_sandboxed_and_no_raw_log_is_uploaded(self):
        source = (ROOT / "scripts/dialgate-ios-compile.py").read_text()
        self.assertEqual(
            ios.protected_command(["xcodebuild", "build"], Path("/tmp/rule")),
            ["/usr/bin/sandbox-exec", "-f", "/tmp/rule", "xcodebuild", "build"],
        )
        self.assertIn('(deny process-exec (regex #".*/(codesign|ldid)$"))', source)
        self.assertRegex(source, r"protected_command\(\s*compile_command\(")
        self.assertRegex(source, r'protected_command\(\["flutter", "--version"\]')
        text = (
            "private "
            + "12345678-1234-1234-1234-123456789abc"
            + " token=fixture-secret\n"
        )
        safe = json.dumps(ios.compile_evidence(text))
        self.assertNotIn("fixture-secret", safe)
        self.assertNotIn("12345678-", safe)

    def test_linux_targets_are_complete_unique_and_policy_has_real_builder(self):
        job = generator.job(
            (ROOT / ".github/workflows/dialgate-verify.yml").read_text(),
            "linux_client_tests",
        )
        for target in (
            "test/modules/updater",
            "test/services/update_checker_test.dart",
            "test/services/update_manifest_compatibility_test.dart",
            "test/automation/macos_manifest_floor_test.dart",
            "test/automation/prepare_manifest_rollback_test.dart",
            "test/automation/workflow_guard_test.dart",
        ):
            self.assertEqual(job.count("            " + target + " \\"), 1)
        self.assertIn("python3 scripts/ci/test_pipeline_archive_modes.py", job)
        self.assertIn("scripts/ci/test_candidate_policy_workflow.py", job)
        self.assertIn('--builder "$GITHUB_WORKSPACE/.client-verifier"', job)
        self.assertIn("go test -race -p 2 ./recovery .", job)
        self.assertIn("go test -tags with_gvisor -race -p 2 ./recovery .", job)


if __name__ == "__main__":
    unittest.main()
