"""One dial workflow serves pull requests and release, without a weaker pass rule."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / ".github" / "workflows" / "build.yml"
DIAL = ROOT / ".github" / "workflows" / "dial-gate.yml"
SYNC = ROOT / "sync-build.sh"
IOS = ROOT / "scripts" / "ios-unsigned-compile.py"
TABLE = "| platform | protocol | baseline | candidate | baseline p95 ms | candidate p95 ms | pass |"


def one_dial_issues(build: str, dial: str, sync: str, ios: str) -> list[str]:
    issues: list[str] = []
    if build.count("\n  dial_gate:\n") != 1:
        issues.append("build.yml must call the dial gate once")
    if "uses: ./.github/workflows/dial-gate.yml" not in build:
        issues.append("dial gate is not the reusable workflow")
    if "needs.dial_gate.result == 'success'" not in build:
        issues.append("release no longer blocks on the dial gate")
    if "needs.dial_gate_summary.result == 'success'" not in build:
        issues.append("release no longer blocks on the dial summary")
    if TABLE not in build:
        issues.append("dial table shape changed")
    for name in ("windows", "macos", "linux", "android", "ios"):
        if f"\n          - name: {name}\n" not in dial:
            issues.append(f"missing platform {name}")
    if "ios_unsigned_compile:" not in dial:
        issues.append("unsigned iOS compilation is missing")
    if "linux_auth_storage:" not in dial:
        issues.append("auth and storage verification is missing")
    if dial.count("ssh-key:") < 4:
        issues.append("private source checkouts lost ssh-key")
    if "feat/real-dial-gate-20261111" in sync or "feat/real-dial-gate-20261111" in dial:
        issues.append("deleted dial branch returned")
    if "generic/platform" in ios or "generic/platform" in dial:
        issues.append("device SDK destination returned")
    return issues


class OneDialGateTests(unittest.TestCase):
    def test_live_files_keep_five_platforms_and_the_release_block(self) -> None:
        issues = one_dial_issues(
            BUILD.read_text(encoding="utf-8"),
            DIAL.read_text(encoding="utf-8"),
            SYNC.read_text(encoding="utf-8"),
            IOS.read_text(encoding="utf-8"),
        )
        self.assertEqual(issues, [])

    def test_dropping_unsigned_ios_compilation_fails(self) -> None:
        dial = DIAL.read_text(encoding="utf-8").replace("ios_unsigned_compile:", "ios_compile_removed:", 1)
        issues = one_dial_issues(
            BUILD.read_text(encoding="utf-8"),
            dial,
            SYNC.read_text(encoding="utf-8"),
            IOS.read_text(encoding="utf-8"),
        )
        self.assertTrue(any("unsigned iOS" in issue for issue in issues))
