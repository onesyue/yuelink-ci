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


def _caller_job(build: str) -> str:
    marker = "\n  dial_gate:\n"
    if build.count(marker) != 1:
        return ""
    lines: list[str] = []
    for line in build.split(marker, 1)[1].splitlines():
        if (
            line.startswith("  ")
            and not line.startswith("   ")
            and line.rstrip().endswith(":")
            and not line.startswith("  #")
        ):
            break
        lines.append(line)
    return "\n".join(lines)


def _permission_lines(block: str) -> set[str]:
    granted: set[str] = set()
    in_permissions = False
    for line in block.splitlines():
        if line == "    permissions:":
            in_permissions = True
            continue
        if not in_permissions:
            continue
        if line.startswith("      ") and ": " in line:
            granted.add(line.strip())
            continue
        break
    return granted


def one_dial_issues(build: str, dial: str, sync: str, ios: str) -> list[str]:
    issues: list[str] = []
    if build.count("\n  dial_gate:\n") != 1:
        issues.append("build.yml must call the dial gate once")
    if "uses: ./.github/workflows/dial-gate.yml" not in build:
        issues.append("dial gate is not the reusable workflow")
    caller = _caller_job(build)
    granted = _permission_lines(caller)
    if granted != {"contents: read", "actions: write"}:
        issues.append("dial caller must grant contents: read and actions: write")
    for fragment in dial.split("\n    permissions:\n")[1:]:
        requested = _permission_lines("    permissions:\n" + fragment)
        extra = requested - granted
        if extra:
            issues.append(
                "dial workflow requests permissions the caller does not grant: "
                + ", ".join(sorted(extra))
            )
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

    def test_caller_must_grant_the_actions_write_the_dial_job_requests(self) -> None:
        build = BUILD.read_text(encoding="utf-8").replace(
            "    needs: [source_binding]\n"
            "    permissions:\n"
            "      contents: read\n"
            "      actions: write\n",
            "    needs: [source_binding]\n"
            "    permissions:\n"
            "      contents: read\n",
            1,
        )
        issues = one_dial_issues(
            build,
            DIAL.read_text(encoding="utf-8"),
            SYNC.read_text(encoding="utf-8"),
            IOS.read_text(encoding="utf-8"),
        )
        self.assertTrue(any("actions: write" in issue for issue in issues))
