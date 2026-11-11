"""Offline orchestration assurance; no Xcode, signing or product build is run."""

from __future__ import annotations

import importlib.util
import json
import os
import plistlib
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
    def test_verify_preflight_reaches_sdk_stage_with_fixed_xcode_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = ios.argparse.Namespace(
                checkout=root,
                expected_builder="a" * 40,
                expected_source="b" * 40,
                mode="positive",
                receipt_dir=root / "receipt",
                flutter_sdk=root / "sdk",
            )

            def recording_run(command, cwd, env, log):
                log.write_text(
                    "Xcode 26.6\nBuild version fixture\n"
                    if log.name == "xcode-version"
                    else ""
                )
                return 0

            with (
                patch.dict(os.environ, {"RUNNER_TEMP": directory}),
                patch.object(
                    ios,
                    "git",
                    side_effect=lambda root, *args: (
                        "a" * 40 if args == ("rev-parse", "HEAD") else ""
                    ),
                ),
                patch.object(ios, "source_identity", return_value={}),
                patch.object(ios, "validate_project"),
                patch.object(ios, "validate_mode"),
                patch.object(ios, "run", side_effect=recording_run),
                patch.object(
                    ios, "prepare_sdk", side_effect=ValueError("fixture stop")
                ),
            ):
                self.assertEqual(ios.verify(args), 1)
            receipt = json.loads((root / "receipt/receipt.json").read_text())
            self.assertEqual(receipt["stage"], "prepare-isolated-sdk")
            self.assertEqual(
                receipt["preparation_result"],
                {"stage": "sandbox-check", "exit_code": 0, "outcome": "success"},
            )
            self.assertNotIn("fixture stop", json.dumps(receipt))

    def test_plugin_identifier_is_not_plugin_failure(self):
        for text in (
            "error: FlutterGeneratedPluginSwiftPackage failed",
            "error: PluginFixtureIdentifier has errors",
            "error: sandbox PluginFixtureIdentifier not permitted",
        ):
            result = ios.compile_evidence(text, failed=True)
            self.assertNotIn("plugin_execution_failed", result["diagnostic_codes"])
            self.assertNotIn("plugin_sandbox_denied", result["diagnostic_codes"])
            self.assertNotIn("plugin", result["failure_token_counts"])
        self.assertIn(
            "plugin_execution_failed",
            ios.compile_evidence("error: plugin failed", failed=True)[
                "diagnostic_codes"
            ],
        )

    def test_failure_vocabulary_is_closed_and_counts_only_failure_context(self):
        text = (
            "SwiftCompile success\nordinary sandbox read write\n"
            "error: rsync sandbox deny read write framework xcframework missing\n"
            "Command PhaseScriptExecution failed with a nonzero exit code\n"
            "The following build commands failed:\n"
            "  SwiftEmitModule normal arm64 /private/secret-path\n"
            'error: token=signing password=validation https://fixture.invalid/module /private/framework "secret-module" 12345678-1234-1234-1234-123456789abc\n'
        )
        result = ios.compile_evidence(text, failed=True)
        counts = result["failure_token_counts"]
        self.assertEqual(result["failure_line_count"], 4)
        self.assertEqual(counts["sandbox"], 1)
        self.assertEqual(counts["read"], 1)
        self.assertEqual(counts["framework"], 1)
        self.assertEqual(counts["SwiftEmitModule"], 1)
        self.assertEqual(counts["PhaseScriptExecution"], 1)
        for excluded in ("SwiftCompile", "signing", "validation", "module"):
            self.assertNotIn(excluded, counts)
        self.assertTrue(set(counts) <= set(ios.FAILURE_TOKENS))
        for secret in (
            "secret-path",
            "fixture.invalid",
            "secret-module",
            "12345678-1234-1234-1234-123456789abc",
            "password=",
            "token=",
        ):
            self.assertNotIn(secret, json.dumps(result))
        unknown = ios.compile_evidence("error: opaque_fixture_identifier", failed=True)
        self.assertEqual(unknown["diagnostic_codes"], ["unclassified_compiler_failure"])
        self.assertEqual(unknown["failure_token_counts"], {"error": 1})

    def test_failure_vocabulary_excludes_complete_credentials_and_relative_paths(self):
        for suffix in (
            'token="plugin validation"',
            "authorization: Bearer plugin",
            "password='module framework' signing",
            "module/plugin.swift",
            r"module\plugin.swift",
            "./module/plugin.swift:12:3",
            "\"module 'plugin' framework\"",
            r'"module \"plugin\" framework"',
            '"module plugin',
        ):
            with self.subTest(suffix=suffix):
                self.assertEqual(
                    ios.failure_vocabulary("error: " + suffix),
                    {"failure_line_count": 1, "failure_token_counts": {"error": 1}},
                )
        self.assertEqual(
            ios.failure_vocabulary('error: token="plugin validation"\nerror: module'),
            {
                "failure_line_count": 2,
                "failure_token_counts": {"error": 2, "module": 1},
            },
        )

    def test_actual_compile_diagnostics_cover_fixed_categories_only(self):
        examples = {
            "signature_verification_failed": 'The signature of "Fixture.xcframework" cannot be verified',
            "artifact_file_missing": 'The file "Fixture.xcframework" cannot be opened because it does not exist',
            "plugin_validation_failed": "plugin failed validation",
            "plugin_approval_required": "plugin must be enabled",
            "plugin_execution_failed": "build tool plugin failed",
            "plugin_sandbox_denied": "plugin sandbox execution denied",
            "macro_execution_failed": "external macro implementation type cannot be found",
            "missing_module": "no such module 'UIKit'",
            "missing_package_product": "Missing package product 'fixture'",
            "missing_build_input": "Build input file cannot be found",
            "missing_framework_or_library": "framework 'Flutter' not found",
            "swift_syntax_error": "error: expected expression",
            "swift_typecheck_error": "cannot find 'fixture' in scope",
            "swift_compilation_failed": "emit-module command failed",
            "clang_compilation_failed": "clang: error: fixture",
            "linker_failed": "Undefined symbols for architecture arm64",
            "platform_binary_mismatch": "building for iOS but linking simulator binary",
            "script_phase_failed": "Command PhaseScriptExecution failed with a nonzero exit code",
            "asset_catalog_failed": "CompileAssetCatalog failed",
            "invalid_project_configuration": "Info.plist invalid",
            "signing_configuration_error": "requires a provisioning profile",
            "tool_process_failed": "Command failed with a nonzero exit code",
        }
        self.assertEqual(set(examples), set(ios.COMPILE_DIAGNOSTIC_PATTERNS))
        for code, text in examples.items():
            with self.subTest(code=code):
                self.assertIn(
                    code, ios.compile_evidence(text, failed=True)["diagnostic_codes"]
                )
                self.assertNotIn("diagnostic_codes", ios.compile_evidence(text))
        nested = ios.compile_evidence(
            "sandbox_apply: Operation not permitted", failed=True
        )
        self.assertIn("nested_sandbox_denied", nested["diagnostic_codes"])
        self.assertFalse(nested["expected_negative_diagnostic"])

    def test_inventory_and_signature_diagnostics_do_not_prove_negative(self):
        for text, category in (
            (
                'error: signature of "Fixture.xcframework" cannot be verified',
                "signature_verification_failed",
            ),
            (
                'error: file "Fixture.xcframework" cannot be opened because it does not exist',
                "artifact_file_missing",
            ),
        ):
            result = ios.compile_evidence(text, failed=True)
            self.assertIn(category, result["diagnostic_codes"])
            self.assertFalse(result["signing_operation"])
            self.assertFalse(result["expected_negative_diagnostic"])
            self.assertEqual(result["compiled_targets"], [])
        for text in (
            "signature verified successfully",
            "SignatureFixture cannot be verified",
            "file cannot be opened: permission denied",
        ):
            result = ios.compile_evidence(text, failed=True)
            self.assertNotIn(
                "signature_verification_failed", result["diagnostic_codes"]
            )
            self.assertNotIn("artifact_file_missing", result["diagnostic_codes"])
        self.assertEqual(
            ios.failure_vocabulary('error: token="signature verified opened exists"')[
                "failure_token_counts"
            ],
            {"error": 1},
        )

    def test_inventory_literal_declarations_are_bounded_and_do_not_execute(self):
        good = '.binaryTarget( name: "App", /* comment */ path: "App.xcframework", )'
        self.assertEqual(
            ios.inventory_declarations(good), ({"App": "App.xcframework"}, True, 0)
        )
        self.assertEqual(ios.inventory_declarations("// " + good), ({}, True, 0))
        self.assertEqual(ios.inventory_declarations('".binaryTarget"'), ({}, True, 0))
        for text in (
            '.binaryTarget(name: "App", path: computed())',
            '.binaryTarget(name: "App", path: "\\(secret)")',
            '.binaryTarget(name: "App", url: "https://fixture.invalid", checksum: "opaque")',
            "/* unfinished",
            "/* nested /* comment */ " + good + " */",
            good + "\n" + good,
            "\n".join('.binaryTarget(name: "Other", path: "other")' for _ in range(33)),
        ):
            self.assertFalse(ios.inventory_declarations(text)[1])

    def test_inventory_known_paths_plist_counts_and_phase_independence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "checkout"
            sdk = Path(directory) / "sdk"
            root.mkdir()
            sdk.mkdir()
            manifest = root / ios.GENERATED_MANIFEST
            manifest.parent.mkdir(parents=True)
            manifest.write_text('.binaryTarget(name: "App", path: "App.xcframework")')
            flutter = sdk / "bin/cache/artifacts/engine/ios-release/Flutter.xcframework"
            flutter.mkdir(parents=True)
            libraries = [
                {
                    "SupportedPlatform": "ios",
                    "SupportedArchitectures": ["arm64"],
                    "LibraryIdentifier": "opaque-not-output",
                },
                {
                    "SupportedPlatform": "ios",
                    "SupportedPlatformVariant": "simulator",
                    "SupportedArchitectures": ["arm64", "x86_64"],
                },
            ]
            (flutter / "Info.plist").write_bytes(
                plistlib.dumps({"AvailableLibraries": libraries})
            )
            first = ios.artifact_inventory(root, sdk)
            row = first["artifacts"][0]
            self.assertEqual(
                (
                    row["exists"],
                    row["plist_status"],
                    row["ios_device_arm64_count"],
                    row["ios_simulator_arm64_count"],
                    row["ios_simulator_x86_64_count"],
                ),
                (True, "parsed", 1, 1, 1),
            )
            self.assertFalse(first["artifacts"][2]["exists"])
            self.assertEqual(first["artifacts"][3]["exists"], "not_declared")
            (manifest.parent / "App.xcframework").mkdir()
            second = ios.artifact_inventory(root, sdk)
            self.assertTrue(second["artifacts"][2]["exists"])
            self.assertEqual(second["artifacts"][2]["plist_status"], "missing")
            self.assertFalse(first["artifacts"][2]["exists"])
            self.assertNotIn("opaque-not-output", json.dumps(second))

    def test_inventory_unknown_and_sensitive_inputs_never_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "checkout"
            sdk = Path(directory) / "sdk"
            root.mkdir()
            sdk.mkdir()
            manifest = root / ios.GENERATED_MANIFEST
            manifest.parent.mkdir(parents=True)
            outside = (
                Path(directory)
                / "secret-signature-12345678-1234-1234-1234-123456789abc"
            )
            outside.mkdir()
            (manifest.parent / "App.xcframework").symlink_to(
                outside, target_is_directory=True
            )
            for value in (
                str(outside),
                "App.xcframework",
                "../../../../../../secret-signature-12345678-1234-1234-1234-123456789abc/App.xcframework",
            ):
                manifest.write_text(f'.binaryTarget(name: "App", path: "{value}")')
                result = ios.artifact_inventory(root, sdk)
                self.assertEqual(result["artifacts"][2]["exists"], "unknown")
                self.assertNotIn("secret-signature", json.dumps(result))
                self.assertNotIn(str(root), json.dumps(result))
            manifest.write_text("x" * (ios.INVENTORY_LIMIT + 1))
            self.assertEqual(
                ios.artifact_inventory(root, sdk)["declaration_status"], "unknown"
            )
            manifest.unlink()
            manifest.symlink_to(outside / "absent.swift")
            self.assertEqual(
                ios.artifact_inventory(root, sdk)["manifest_status"], "unknown"
            )

    def test_inventory_malformed_plists_are_unknown_not_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "App.xcframework"
            artifact.mkdir()
            info = artifact / "Info.plist"
            samples = [
                b"not a plist",
                b'<?xml version="1.0"?><plist><dict>',
                b"x" * (ios.INVENTORY_LIMIT + 1),
            ]
            for library in (
                {
                    "SupportedPlatform": "secret-platform",
                    "SupportedArchitectures": ["arm64"],
                },
                {"SupportedPlatform": "ios", "SupportedArchitectures": ["secret-arch"]},
            ):
                samples.append(plistlib.dumps({"AvailableLibraries": [library]}))
            samples.append(plistlib.dumps({"AvailableLibraries": [{}] * 33}))
            for sample in samples:
                info.write_bytes(sample)
                row = ios.inventory_artifact(
                    "App", "generated_manifest", True, artifact, (root.resolve(),)
                )
                self.assertTrue(row["exists"])
                self.assertEqual(row["plist_status"], "unknown")
                self.assertIsNone(row["ios_device_arm64_count"])
                self.assertNotIn("secret", json.dumps(row))
            info.unlink()
            info.symlink_to("/not-an-approved-root/Info.plist")
            self.assertEqual(
                ios.inventory_artifact(
                    "App", "generated_manifest", True, artifact, (root.resolve(),)
                )["plist_status"],
                "unknown",
            )

    def test_missing_inventory_does_not_short_circuit_compile_or_change_verdict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = ios.argparse.Namespace(
                checkout=root,
                expected_builder="a" * 40,
                expected_source="b" * 40,
                mode="positive",
                receipt_dir=root / "receipt",
                flutter_sdk=root / "sdk",
            )
            ffi = root / "ios/Frameworks"
            ffi.mkdir(parents=True)
            for name in ("libclash.a", "libclash.h"):
                (ffi / name).write_bytes(b"fixture")

            def prepare(original, isolated):
                cache = isolated / "bin/cache"
                cache.mkdir(parents=True)
                (cache / "flutter_tools.snapshot").write_bytes(b"fixture")
                return {"adaptation": {}}

            def preparation(command, cwd, env, log, receipt):
                contents = {
                    "xcode-version": "Xcode 26.6\n",
                    "flutter-version": json.dumps(
                        {
                            "frameworkVersion": "3.47.8",
                            "frameworkRevision": ios.FLUTTER_COMMIT,
                        }
                    ),
                    "go-version": "go version go1.27.4 darwin/arm64",
                    "settings-Runner": "{}",
                    "settings-PacketTunnel": "{}",
                }
                log.write_text(contents.get(log.name, ""))

            def compilation(command, cwd, env, log, timeout):
                self.assertIn("xcodebuild", command)
                self.assertIn("build", command)
                log.write_text("error: unrelated fixture failure")
                return 65

            with (
                patch.dict(os.environ, {"RUNNER_TEMP": directory}),
                patch.object(
                    ios,
                    "git",
                    side_effect=lambda root, *values: (
                        "a" * 40 if values == ("rev-parse", "HEAD") else ""
                    ),
                ),
                patch.object(ios, "source_identity", return_value={}),
                patch.object(ios, "validate_project"),
                patch.object(ios, "validate_mode"),
                patch.object(ios, "prepare_sdk", side_effect=prepare),
                patch.object(ios, "require_run", side_effect=preparation),
                patch.object(ios, "config_with_recovery", return_value=False),
                patch.object(ios, "signing_disabled", return_value={}),
                patch.object(ios, "read_xcresult_errors", return_value=""),
                patch.object(ios, "run", side_effect=compilation) as run,
            ):
                self.assertEqual(ios.verify(args), 65)
                run.assert_called_once()
            receipt = json.loads((root / "receipt/receipt.json").read_text())
            self.assertEqual(receipt["status"], "compilation_failed_or_targets_missing")
            for phase in ("post_config", "post_failed_build"):
                inventory = receipt["artifact_inventory"][phase]
                self.assertEqual(inventory["manifest_status"], "missing")
                self.assertFalse(inventory["artifacts"][0]["exists"])

    def test_compile_diagnostics_never_capture_untrusted_context_or_prove_negative(
        self,
    ):
        sensitive = "12345678-1234-1234-1234-123456789abc https://fixture.invalid/sub token=fixture-secret /private/fixture-path"
        text = (
            "SwiftCompile normal arm64 (in target 'Runner' from project 'Runner')\n"
            "PhaseScriptExecution fixture\n"
            "error: no such module 'UIKit'\n"
            "error: no such module 'private_module_fixture'\n"
            "(in target 'private_target_fixture' from project 'private_project_fixture')\n"
            + sensitive
        )
        result = ios.compile_evidence(text, failed=True)
        self.assertEqual(result["observed_targets"], ["Runner"])
        self.assertEqual(
            result["observed_stages"], ["SwiftCompile", "PhaseScriptExecution"]
        )
        self.assertEqual(result["referenced_modules"], ["UIKit"])
        for forbidden in sensitive.split() + [
            "private_module_fixture",
            "private_target_fixture",
            "private_project_fixture",
        ]:
            self.assertNotIn(forbidden, json.dumps(result))
        self.assertEqual(
            ios.compile_evidence(sensitive, failed=True)["diagnostic_codes"],
            ["unclassified_compiler_failure"],
        )
        self.assertEqual(
            ios.compilation_outcome(65, result, "negative", False)[0],
            "negative_control_not_proven",
        )
        expected = ios.compile_evidence(
            "AppDelegate.swift:12:3: error: missing argument for parameter 'extensionProfile' in call",
            failed=True,
        )
        self.assertEqual(
            ios.compilation_outcome(65, expected, "negative", False)[0],
            "expected_swift_failure",
        )
        signing = ios.compile_evidence("CodeSign fixture", failed=True)
        self.assertEqual(
            ios.compilation_outcome(65, signing, "positive", False)[0],
            "signing_operation_rejected",
        )

    def test_manifest_wrapper_delegates_exact_argv_and_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ios.install_manifest_xcrun(root)
            script = (root / "xcrun").read_text()
            self.assertEqual(script.count("exec /usr/bin/xcrun"), 2)
            delegate = root / "delegate"
            delegate.write_text(
                '#!/usr/bin/env python3\nimport os,sys,json\nopen(os.environ["CALLS"],"w").write(json.dumps(sys.argv[1:]))\nsys.exit(19)\n'
            )
            delegate.chmod(0o755)
            # Substitute only the OS executable in this isolated recording fixture.
            (root / "xcrun").write_text(script.replace("/usr/bin/xcrun", str(delegate)))
            env = dict(os.environ, CALLS=str(root / "calls"))
            for args, expected in (
                (
                    ["xcodebuild", "-list", "space value"],
                    [
                        "xcodebuild",
                        ios.MANIFEST_SANDBOX_ARGUMENT,
                        "-list",
                        "space value",
                    ],
                ),
                (["swift", "--version"], ["swift", "--version"]),
                (["--find", "xcodebuild"], ["--find", "xcodebuild"]),
            ):
                result = subprocess.run(
                    [str(root / "xcrun"), *args],
                    env=env,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 19)
                self.assertEqual(json.loads((root / "calls").read_text()), expected)
            self.assertNotIn(
                ios.MANIFEST_SANDBOX_ARGUMENT, ios.compile_command(root, root)
            )
            self.assertIn(
                ios.MANIFEST_SANDBOX_ARGUMENT, ios.compile_command(root, root, True)
            )
            for recovery in (False, True):
                command = ios.compile_command(root, root, recovery)
                for flag in ios.PACKAGE_PLUGIN_ARGUMENTS:
                    self.assertEqual(command.count(flag), 1)
                self.assertNotIn("codesign", command)
                self.assertNotIn("ldid", command)

    def test_config_recovery_is_single_exact_nested_failure_and_keeps_first(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            guard = root / "guard-bin"
            guard.mkdir()
            receipt = {}
            seen = []

            def record(command, cwd, env, log):
                seen.append(command)
                log.write_text(
                    "sandbox-exec: sandbox_apply: Operation not permitted"
                    if len(seen) == 1
                    else "ok"
                )
                return 1 if len(seen) == 1 else 0

            with patch.object(ios, "run", side_effect=record):
                self.assertTrue(
                    ios.config_with_recovery(
                        ["flutter", "build", "ios"],
                        root,
                        {},
                        root,
                        root / "no-sign.sb",
                        receipt,
                    )
                )
            self.assertEqual(len(seen), 2)
            self.assertEqual(seen[0], seen[1])
            self.assertEqual(
                seen[0][:3], ["/usr/bin/sandbox-exec", "-f", str(root / "no-sign.sb")]
            )
            self.assertEqual(receipt["config_recovery"]["attempts"], 1)
            self.assertTrue(receipt["config_recovery"]["completed"])
            first = receipt["config_recovery"]["first_failure"]
            self.assertEqual(first["exit_code"], 1)
            self.assertEqual(first["stage"], "config")
            self.assertIn("nested_sandbox_denied", first["diagnostic_codes"])
            self.assertEqual(receipt["preparation_result"]["exit_code"], 0)
            with patch.object(ios, "run") as process, self.assertRaises(ValueError):
                ios.config_with_recovery(
                    ["unused"], root, {}, root, root / "no-sign.sb", receipt
                )
            process.assert_not_called()

    def test_config_second_attempt_cancellation_preserves_failure_and_signer_marker_blocks_retry(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "guard-bin").mkdir()
            receipt = {}
            calls = []

            def cancelled_retry(command, cwd, env, log):
                calls.append(command)
                if len(calls) == 2:
                    raise KeyboardInterrupt
                log.write_text("sandbox_apply: Operation not permitted")
                return 1

            with (
                patch.object(ios, "run", side_effect=cancelled_retry),
                self.assertRaises(KeyboardInterrupt),
            ):
                ios.config_with_recovery(
                    ["flutter"], root, {}, root, root / "profile", receipt
                )
            self.assertEqual(len(calls), 2)
            self.assertFalse(receipt["config_recovery"]["completed"])
            self.assertEqual(
                receipt["config_recovery"]["first_failure"]["exit_code"], 1
            )
            self.assertEqual(receipt["preparation_result"]["outcome"], "cancelled")
            (root / "signing-violation").write_text("signing attempt rejected")
            calls.clear()
            with (
                patch.object(ios, "run", side_effect=cancelled_retry) as process,
                self.assertRaises(ValueError),
            ):
                ios.config_with_recovery(
                    ["flutter"], root, {}, root, root / "profile", {}
                )
            self.assertEqual(process.call_count, 1)

    def test_config_recovery_rejects_unknown_network_signer_and_cancellation(self):
        failures = [
            "unknown failure",
            "Operation not permitted",
            "sandbox_apply: Permission denied",
            "sandbox_apply: Operation not permitted\nfailed to connect",
            "sandbox_apply: Operation not permitted\ndeny process-exec /usr/bin/codesign",
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for text in failures:
                receipt = {}

                def failed(command, cwd, env, log, text=text):
                    log.write_text(text)
                    return 1

                with (
                    patch.object(ios, "run", side_effect=failed) as process,
                    self.assertRaises(ValueError),
                ):
                    ios.config_with_recovery(
                        ["flutter"], root, {}, root, root / "profile", receipt
                    )
                self.assertEqual(process.call_count, 1)
                self.assertNotIn("config_recovery", receipt)
            for interruption in (
                KeyboardInterrupt(),
                subprocess.TimeoutExpired("fixture", 1),
            ):
                with (
                    patch.object(ios, "run", side_effect=interruption) as process,
                    self.assertRaises(type(interruption)),
                ):
                    ios.config_with_recovery(
                        ["flutter"], root, {}, root, root / "profile", {}
                    )
                self.assertEqual(process.call_count, 1)
            (root / "guard-bin").mkdir()

            def always_nested(command, cwd, env, log):
                log.write_text("sandbox_apply_container: Operation not permitted")
                return 7

            receipt = {}
            with (
                patch.object(ios, "run", side_effect=always_nested) as process,
                self.assertRaises(ValueError),
            ):
                ios.config_with_recovery(
                    ["flutter"], root, {}, root, root / "profile", receipt
                )
            self.assertEqual(process.call_count, 2)
            self.assertFalse(receipt["config_recovery"]["completed"])
            self.assertEqual(
                receipt["config_recovery"]["first_failure"]["exit_code"], 7
            )

    def test_preparation_failure_records_only_fixed_diagnostics(self):
        sensitive = "secret=fixture-only /private/fixture-path https://fixture.invalid/sub 12345678-1234-1234-1234-123456789abc"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "config"
            receipt = {}
            with self.assertRaises(ValueError):
                ios.require_run(
                    [
                        sys.executable,
                        "-c",
                        "import sys; print('Error running pod install'); print(sys.argv[1]); sys.exit(23)",
                        sensitive,
                    ],
                    root,
                    dict(os.environ),
                    log,
                    receipt,
                )
            self.assertEqual(
                receipt,
                {
                    "stage": "config",
                    "preparation_result": {
                        "stage": "config",
                        "exit_code": 23,
                        "outcome": "failure",
                        "diagnostic_codes": ["pod_install_failed"],
                    },
                },
            )
            serialized = json.dumps(receipt)
            for forbidden in sensitive.split():
                self.assertNotIn(forbidden, serialized)
            self.assertEqual(
                ios.preparation_diagnostics(sensitive),
                ["unclassified_preparation_failure"],
            )
            with self.assertRaises(ValueError):
                ios.validate_go_version("go version go1.27.3 darwin/arm64", receipt)
            self.assertEqual(receipt["stage"], "validate-go-version")
            ios.validate_go_version("go version go1.27.4 darwin/arm64", receipt)

    def test_diagnostic_enum_examples_and_timeout_remain_fail_closed(self):
        examples = {
            "nested_sandbox_denied": "sandbox-exec: sandbox_apply: Operation not permitted",
            "signer_execution_denied": "deny process-exec /usr/bin/codesign",
            "package_network_failure": "could not resolve host",
            "signing_attempt_rejected": "Code signing is required",
            "sandbox_denied": "sandbox: deny process-exec",
            "unavailable_xcode_sdk": "SDK iphoneos cannot be located",
            "xcode_license_required": "Agree to the Xcode license",
            "missing_cocoapods": "CocoaPods not installed",
            "pod_install_failed": "Error running pod install",
            "swift_package_resolution_failed": "Could not resolve package dependencies",
            "swift_package_manager_configuration": "Swift Package Manager is not supported",
            "deployment_target_mismatch": "requires a higher minimum deployment target",
            "dependency_resolution_failed": "version solving failed",
            "missing_generated_configuration": "Generated.xcconfig must exist",
        }
        self.assertEqual(set(examples), set(ios.DIAGNOSTIC_PATTERNS))
        for expected, text in examples.items():
            self.assertIn(expected, ios.preparation_diagnostics(text))
        for exc, outcome in (
            (subprocess.TimeoutExpired(["secret-argv"], 1), "timeout"),
            (KeyboardInterrupt(), "cancelled"),
        ):
            receipt = {}
            with (
                patch.object(ios, "run", side_effect=exc),
                self.assertRaises(type(exc)),
            ):
                ios.require_run(["unused"], ROOT, {}, Path("config"), receipt)
            self.assertEqual(
                receipt["preparation_result"],
                {"stage": "config", "exit_code": None, "outcome": outcome},
            )
            self.assertNotIn("secret-argv", json.dumps(receipt))
        receipt = {"preparation_result": {"outcome": "success"}}
        with (
            patch.object(ios, "run", side_effect=OSError("secret-path")),
            self.assertRaises(OSError),
        ):
            ios.require_run(["unused"], ROOT, {}, Path("config"), receipt)
        self.assertEqual(
            receipt["preparation_result"],
            {"stage": "config", "exit_code": None, "outcome": "launch_failed"},
        )
        self.assertNotIn("secret-path", json.dumps(receipt))
        with patch.object(ios, "run") as process, self.assertRaises(ValueError):
            ios.require_run(["unused"], ROOT, {}, Path("untrusted-stage"), {})
        process.assert_not_called()

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
            for flag in ios.PACKAGE_PLUGIN_ARGUMENTS:
                self.assertEqual(args.count(flag), 1)
            self.assertNotIn("codesign", args)
            self.assertNotIn("ldid", args)
            self.assertIn("private simulated diagnostic", (root / "raw").read_text())

    def test_xcresult_error_commands_cannot_prove_compilation(self):
        command_shaped = "\n".join(
            f"SwiftCompile normal arm64 (in target '{target}' from project 'Runner')"
            for target in ("Runner", "PacketTunnel")
        )
        errors = ios.xcresult_error_messages({"errors": [{"message": command_shaped}]})
        evidence = ios.compile_evidence("", failed=True, xcresult_errors=errors)
        self.assertEqual(evidence["compiled_targets"], [])
        self.assertEqual(evidence["observed_targets"], ["Runner", "PacketTunnel"])
        self.assertEqual(
            ios.compilation_outcome(0, evidence, "positive", True),
            ("compilation_failed_or_targets_missing", 1),
        )
        real = ios.compile_evidence(command_shaped, failed=True)
        self.assertEqual(real["compiled_targets"], ["Runner", "PacketTunnel"])

    def test_xcresult_errors_retain_exact_negative_proof_only(self):
        error = (
            "AppDelegate.swift:12:3: error: missing argument "
            "for parameter 'extensionProfile' in call"
        )
        evidence = ios.compile_evidence("", failed=True, xcresult_errors=error)
        self.assertTrue(evidence["expected_negative_diagnostic"])
        self.assertEqual(evidence["compiled_targets"], [])
        self.assertEqual(
            ios.compilation_outcome(65, evidence, "negative", False),
            ("expected_swift_failure", 65),
        )
        generic = ios.compile_evidence(
            "", failed=True, xcresult_errors="error: module not found"
        )
        self.assertFalse(generic["expected_negative_diagnostic"])
        self.assertEqual(
            ios.compilation_outcome(65, generic, "negative", False),
            ("negative_control_not_proven", 65),
        )
        mentioned = ios.compile_evidence(
            "", failed=True, xcresult_errors="CodeSign fixture"
        )
        self.assertFalse(mentioned["signing_operation"])
        actual = ios.compile_evidence("CodeSign fixture", failed=True)
        self.assertTrue(actual["signing_operation"])

    def test_xcresult_errors_drop_locations_and_do_not_invent_compilation(self):
        secret = "file:///private/fixture/AppDelegate.swift"
        payload = {
            "errors": [
                {
                    "issueType": "Swift Compiler Error",
                    "message": (
                        "AppDelegate.swift:1001:72: error: missing argument "
                        "for parameter 'extensionProfile' in call"
                    ),
                    "sourceURL": secret,
                    "targetName": "Runner",
                },
                {"message": "could not resolve host fixture.invalid"},
                "not-an-object",
                {"message": 12},
            ],
            "warnings": [
                {
                    "message": (
                        "AppDelegate.swift:1:1: error: missing argument "
                        "for parameter 'extensionProfile' in call"
                    )
                }
            ],
        }
        text = ios.xcresult_error_messages(payload)
        self.assertNotIn(secret, text)
        self.assertNotIn("sourceURL", text)
        evidence = ios.compile_evidence(text, failed=True)
        self.assertTrue(evidence["expected_negative_diagnostic"])
        self.assertEqual(evidence["compiled_targets"], [])
        self.assertNotIn(secret, json.dumps(evidence))
        self.assertFalse(
            ios.compile_evidence(
                ios.xcresult_error_messages({"warnings": payload["warnings"]}),
                failed=True,
            )["expected_negative_diagnostic"]
        )
        self.assertFalse(
            ios.compile_evidence(
                ios.xcresult_error_messages(
                    {"errors": [{"message": "could not resolve host fixture.invalid"}]}
                ),
                failed=True,
            )["expected_negative_diagnostic"]
        )
        self.assertEqual(ios.xcresult_error_messages({"errors": "nope"}), "")
        self.assertEqual(ios.xcresult_error_messages([]), "")
        self.assertEqual(
            ios.read_xcresult_errors(Path("/tmp/lane-c-missing-xcresult")), ""
        )
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "compile.xcresult"
            bundle.mkdir()
            failed = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="tool failed"
            )
            with patch.object(ios.subprocess, "run", return_value=failed) as invoked:
                self.assertEqual(ios.read_xcresult_errors(bundle), "")
            self.assertEqual(invoked.call_args.args[0][0], "/usr/bin/xcrun")
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(payload), stderr=""
            )
            with patch.object(ios.subprocess, "run", return_value=completed):
                scanned = ios.read_xcresult_errors(bundle)
        self.assertNotIn(secret, scanned)
        scanned_evidence = ios.compile_evidence(scanned, failed=True)
        self.assertTrue(scanned_evidence["expected_negative_diagnostic"])
        self.assertEqual(scanned_evidence["compiled_targets"], [])
        self.assertNotIn(secret, json.dumps(scanned_evidence))

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
                    SCOPE="full",
                    SOURCE="b" * 40,
                    BUILDER="a" * 40,
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
        self.assertEqual(policy["iosCompileMode"], "negative")
        self.assertEqual(
            (policy["sourcePr"], policy["sourceCommit"]),
            (133, "1b28d5fb1e8122a03d46279e03f27fcf8ee191a7"),
        )
        self.assertNotEqual(policy["sourceCommit"], ios.POSITIVE_SOURCE)

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
