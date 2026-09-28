from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseTagLookupRetryTests(unittest.TestCase):
    def lookup(self, statuses: list[int]):
        source = (ROOT / "release.sh").read_text()
        function = source.split("remote_public_tag_state() {", 1)[1].split(
            "\ninspect_remote_public_tag() {", 1
        )[0]
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            (tmp / "statuses.json").write_text(json.dumps(statuses))
            git = tmp / "git"
            git.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, pathlib, sys\n"
                "root = pathlib.Path(os.environ['LOOKUP_TEST_DIR'])\n"
                "calls = root / 'calls.json'\n"
                "seen = json.loads(calls.read_text()) if calls.exists() else []\n"
                "seen.append(sys.argv[1:])\n"
                "calls.write_text(json.dumps(seen))\n"
                "statuses = json.loads((root / 'statuses.json').read_text())\n"
                "sys.exit(statuses[min(len(seen) - 1, len(statuses) - 1)])\n"
            )
            git.chmod(0o755)
            sleep = tmp / "sleep"
            sleep.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$1" >> "$LOOKUP_TEST_DIR/sleeps"\n'
            )
            sleep.chmod(0o755)
            result = subprocess.run(
                [
                    "/bin/bash", "-c",
                    "set -eu\nTAG=v9.8.7\n"
                    + "remote_public_tag_state() {" + function
                    + "\nremote_public_tag_state\n",
                ],
                env={
                    **os.environ,
                    "LOOKUP_TEST_DIR": directory,
                    "PATH": directory + os.pathsep + os.environ["PATH"],
                },
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
            calls = json.loads((tmp / "calls.json").read_text())
            pauses = tmp / "sleeps"
            sleeps = pauses.read_text().splitlines() if pauses.exists() else []
        for call in calls:
            self.assertEqual(
                call,
                ["ls-remote", "--exit-code", "--tags", "origin", "refs/tags/v9.8.7"],
            )
        return result, calls, sleeps

    def test_confirmed_presence_or_absence_returns_without_retry(self):
        for status, expected in [(0, "exists"), (2, "absent")]:
            with self.subTest(status=status):
                result, calls, sleeps = self.lookup([status])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)
                self.assertEqual(len(calls), 1)
                self.assertEqual(sleeps, [])

    def test_transient_failure_can_recover_to_confirmed_presence(self):
        result, calls, sleeps = self.lookup([128, 0])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "exists")
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, ["1"])

    def test_absence_requires_a_successful_no_match_read(self):
        result, calls, sleeps = self.lookup([128, 128, 2])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "absent")
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, ["1", "2"])

    def test_exhausted_or_unknown_failure_never_becomes_absence(self):
        for status in [128, 1]:
            with self.subTest(status=status):
                result, calls, sleeps = self.lookup([status])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("拒绝把网络错误当作不存在", result.stderr)
                self.assertEqual(len(calls), 3)
                self.assertEqual(sleeps, ["1", "2"])


if __name__ == "__main__":
    unittest.main()
