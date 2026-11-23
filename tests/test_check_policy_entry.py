"""Exercise the commands launched by the shared policy entry without recursion."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
# Policy commands from git show 67a58bc:.github/workflows/policy-ci.yml.
# Globs below are expanded against the small fixture repository.
DISCOVER = ["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"]
HANDLE = ["python3", "scripts/check_bot_handle.py"]
LINT = ["actionlint", ".github/workflows/policy-ci.yml"]
SHELL = ["shellcheck", "--severity=warning", "./sync-build.sh", "scripts/check-policy.sh"]
BASE_POLICY = [DISCOVER, HANDLE, LINT, SHELL]

class CheckPolicyEntryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.entry = self.root / "scripts/check-policy.sh"
        self.entry.parent.mkdir()
        shutil.copy2(ROOT / "scripts/check-policy.sh", self.entry)
        workflow = self.root / LINT[1]
        workflow.parent.mkdir(parents=True)
        workflow.touch()
        self.source = self.root / "YueLink source"
        self.source.mkdir()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        self.trace = self.root / "trace"
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
                        TRACE=str(self.trace), GITHUB_ACTIONS="false", HOME=str(self.root))
        for tool in ("python3", "actionlint", "shellcheck", "sync-build.sh"):
            stub = (self.root if tool == "sync-build.sh" else bin_dir) / tool
            stub.write_text(f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
command = [Path(sys.argv[0]).name, *sys.argv[1:]]
if command == ["actionlint", "-version"]:
    print(os.environ.get("STUB_VERSION", "1.7.12"))
else:
    with open(os.environ["TRACE"], "a") as trace:
        print(json.dumps(command), file=trace)
    if command[0] == "sync-build.sh" and os.environ.get("CHECK_ARCHIVE"):
        marker = Path(command[2]) / "marker"
        assert marker.read_text() == "committed" and not marker.stat().st_mode & 0o222
""")
            stub.chmod(0o755)

    def invoke(self, *args):
        self.trace.write_text("")
        proc = subprocess.run(["bash", str(self.entry), *args], cwd=self.root,
                              env=self.env, capture_output=True, text=True, timeout=30)
        commands = [json.loads(line) for line in self.trace.read_text().splitlines()]
        return proc, commands

    def test_ci_matches_baseline_plus_mirror_check(self):
        proc, commands = self.invoke("ci", "--yuelink", str(self.source))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(commands, [DISCOVER, ["sync-build.sh", "--check", str(self.source)],
                                    *BASE_POLICY[1:]])

    def test_quick_runs_discovery_and_workflow_lint(self):
        proc, commands = self.invoke("quick")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(commands, [DISCOVER, LINT])

    def test_removing_handle_check_breaks_golden_list(self):
        self.entry.write_text(self.entry.read_text().replace("python3 scripts/check_bot_handle.py", ":"))
        self.env["GITHUB_ACTIONS"] = "true"
        proc, commands = self.invoke("ci")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(commands.pop(1), ["sync-build.sh", "--check"])
        self.assertNotEqual(commands, BASE_POLICY)
        self.assertEqual(commands, [DISCOVER, LINT, SHELL])

    def test_wrong_actionlint_version_fails_before_checks(self):
        self.env["STUB_VERSION"] = "0.0.0"
        proc, commands = self.invoke("quick")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("requires actionlint 1.7.12", proc.stderr)
        self.assertEqual(commands, [])

    def test_default_archive_uses_origin_master_and_is_cleaned_up(self):
        repo = self.root / "Projects/yueto/yuelink"
        repo.mkdir(parents=True)

        def git(*args):
            subprocess.run(["git", "-C", str(repo), *args], env=self.env, check=True,
                           capture_output=True, timeout=10)

        git("init")
        marker = repo / "marker"
        marker.write_text("committed")
        git("add", "marker")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-m", "fixture")
        git("update-ref", "refs/remotes/origin/master", "HEAD")
        marker.write_text("local")
        self.env["CHECK_ARCHIVE"] = "1"
        proc, commands = self.invoke("ci")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(commands[1][:2], ["sync-build.sh", "--check"])
        self.assertFalse(Path(commands[1][2]).exists())
        self.assertEqual(marker.read_text(), "local")
