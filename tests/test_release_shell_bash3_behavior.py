from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release.sh"

UTF8_MESSAGES = (
    "无法确定远端 public tag 状态",
    "未绑定 exact builder",
    "复用本机 exact signed tag",
    "已确认远端 exact verified signed tag",
)


def message_lines(source: str) -> list[str]:
    lines = []
    for marker in UTF8_MESSAGES:
        matches = [line.strip() for line in source.splitlines() if marker in line]
        if len(matches) != 1:
            raise AssertionError(f"expected one real release message for {marker!r}")
        lines.append(matches[0])
    return lines


def execute_real_messages(lines: list[str]) -> subprocess.CompletedProcess[str]:
    script = "\n".join(
        (
            "set -u",
            "status=128",
            "BUILDER_COMMIT=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "TAG=v9.8.7",
            *lines,
        )
    )
    return subprocess.run(
        ["/bin/bash", "-c", script],
        text=True,
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "LC_ALL": "en_US.UTF-8"},
        check=False,
    )


class ReleaseShellBash3BehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = RELEASE.read_text(encoding="utf-8")
        self.lines = message_lines(self.source)

    def test_real_utf8_release_messages_execute_under_errexit_nounset(self) -> None:
        result = execute_real_messages(self.lines)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("exit=128", result.stdout)
        self.assertIn("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb；", result.stdout)
        self.assertIn("v9.8.7（", result.stdout)
        self.assertIn("v9.8.7；", result.stdout)

    def test_bare_variable_utf8_adjacency_is_absent(self) -> None:
        self.assertIsNone(
            re.search(r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]", self.source)
        )

    def test_each_previous_bash3_failure_is_a_real_behavioral_regression(self) -> None:
        version = subprocess.run(
            ["/bin/bash", "-c", 'printf "%s" "${BASH_VERSINFO[0]}"'],
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        ).stdout
        if version != "3":
            self.skipTest("the behavioral regression requires the supported macOS Bash 3")
        replacements = (
            ("${status}）", "$status）"),
            ("${BUILDER_COMMIT}；", "$BUILDER_COMMIT；"),
            ("${TAG}（", "$TAG（"),
            ("${TAG}；", "$TAG；"),
        )
        for safe, unsafe in replacements:
            with self.subTest(unsafe=unsafe):
                mutated = [line.replace(safe, unsafe, 1) for line in self.lines]
                self.assertNotEqual(mutated, self.lines)
                result = execute_real_messages(mutated)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("unbound variable", result.stdout)


if __name__ == "__main__":
    unittest.main()
