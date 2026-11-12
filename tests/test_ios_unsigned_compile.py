"""Simulator destination and SwiftCompile evidence for unsigned iOS compilation."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ios_unsigned_compile", ROOT / "scripts" / "ios-unsigned-compile.py"
)
assert SPEC and SPEC.loader
compile_mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compile_mod)

PHONE = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"


def _listing(available: bool, name: str = "iPhone 17") -> dict:
    return {
        "devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-26-6": [
                {
                    "name": name,
                    "udid": PHONE,
                    "isAvailable": available,
                }
            ]
        }
    }


class IosUnsignedCompileTests(unittest.TestCase):
    def test_available_iphone_is_selected_from_simctl_json(self) -> None:
        listing = _listing(True)
        listing["devices"]["com.apple.CoreSimulator.SimRuntime.iOS-26-6"].insert(
            0,
            {"name": "iPad Pro", "udid": "IPAD", "isAvailable": True},
        )
        destination = compile_mod.simulator_destination(listing)
        self.assertEqual(destination, f"platform=iOS Simulator,id={PHONE}")

    def test_unavailable_iphone_fails(self) -> None:
        with self.assertRaises(ValueError):
            compile_mod.simulator_destination(_listing(False))

    def test_compile_uses_the_simulator_sdk(self) -> None:
        argv = compile_mod.compile_argv(
            f"platform=iOS Simulator,id={PHONE}", Path("derived")
        )
        self.assertIn("iphonesimulator", argv)
        self.assertIn("build", argv)
        self.assertNotIn("CODE_SIGNING_ALLOWED=YES", argv)
        self.assertEqual(argv[argv.index("-configuration") + 1], "Debug")
        self.assertNotIn("Release", argv)
        self.assertEqual(compile_mod.PRODUCT_DIRECTORY, "Debug-iphonesimulator")
        source = (ROOT / "scripts" / "ios-unsigned-compile.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("generic/platform", source)

    def test_device_destination_is_rejected(self) -> None:
        forbidden = "generic" + "/platform=iOS"
        with self.assertRaises(ValueError):
            compile_mod.compile_argv(forbidden, Path("derived"))

    def test_warm_success_without_swift_compile_fails(self) -> None:
        issues = compile_mod.swift_compile_issues("** BUILD SUCCEEDED **\n")
        self.assertTrue(any("Runner" in issue for issue in issues))
        self.assertTrue(any("PacketTunnel" in issue for issue in issues))

    def test_swift_compile_of_both_targets_passes(self) -> None:
        log = "\n".join(
            (
                "SwiftCompile normal arm64 Compiling A.swift /src/ios/Runner/A.swift",
                "SwiftCompile normal arm64 Compiling B.swift /src/ios/PacketTunnel/B.swift",
                "** BUILD SUCCEEDED **",
            )
        )
        self.assertEqual(compile_mod.swift_compile_issues(log), [])

    def test_device_archive_is_not_a_simulator_binary(self) -> None:
        self.assertTrue(compile_mod.simulator_archive_issues(""))
        self.assertTrue(compile_mod.simulator_archive_issues("platform 2\n"))
        self.assertEqual(
            compile_mod.simulator_archive_issues("platform IOSSIMULATOR\n"), []
        )
        self.assertEqual(compile_mod.simulator_archive_issues("platform 7\n"), [])

    def test_prepare_step_targets_the_simulator(self) -> None:
        job = (ROOT / ".github/workflows/dial-gate.yml").read_text(encoding="utf-8")
        job = job.split("ios_unsigned_compile:", 1)[1]
        self.assertIn("flutter build ios --simulator --config-only --no-pub", job)
        self.assertNotIn("flutter build ios --release", job)
        self.assertNotIn("dart setup.dart build -p ios", job)


class FailureDiagnosticsTests(unittest.TestCase):
    def test_linker_cause_survives_generic_clang_error(self) -> None:
        log = "\n".join(
            (
                "Ld PacketTunnel normal arm64",
                "ld: framework 'MissingFramework' not found",
                "clang: error: linker command failed with exit code 1",
            )
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            compile_mod._emit_failure(log)
        self.assertIn("ld: framework 'MissingFramework' not found", stderr.getvalue())

    def test_undefined_symbol_references_are_preserved(self) -> None:
        log = "\n".join(
            (
                "Undefined symbols for architecture arm64:",
                '  "_missing_native_call", referenced from:',
                "      _start in bridge.o",
                "ld: symbol(s) not found for architecture arm64",
                "clang: error: linker command failed with exit code 1",
            )
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            compile_mod._emit_failure(log)
        self.assertIn("_missing_native_call", stderr.getvalue())
