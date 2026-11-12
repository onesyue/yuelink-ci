"""ci-check.sh dispatches Policy CI and the dial gate, never the release build."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
def _fake_body() -> str:
    return textwrap.dedent(
        """\
        #!/usr/bin/env python3
        import json, os, sys
        with open(os.environ["GH_LOG"], "a", encoding="utf-8") as log:
            log.write(json.dumps(sys.argv[1:]) + "\\n")
        if "build.yml" in sys.argv:
            raise SystemExit(9)
        if sys.argv[1:3] == ["workflow", "run"]:
            raise SystemExit(0)
        scenario = json.loads(open(os.environ["GH_SCENARIO"], encoding="utf-8").read())
        if sys.argv[1:3] == ["run", "list"]:
            workflow = sys.argv[sys.argv.index("--workflow") + 1]
            print(json.dumps(scenario[workflow]["runs"]))
            raise SystemExit(0)
        if sys.argv[1:3] == ["run", "view"]:
            run_id = sys.argv[3]
            for item in scenario.values():
                if run_id in item["views"]:
                    print(json.dumps(item["views"][run_id]))
                    raise SystemExit(0)
        raise SystemExit("unexpected gh " + " ".join(sys.argv[1:]))
        """
    )


class CiCheckTests(unittest.TestCase):
    def run_check(self, scenario: dict, args: list[str]) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "gh"
            fake.write_text(_fake_body(), encoding="utf-8")
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            (root / "scenario.json").write_text(json.dumps(scenario), encoding="utf-8")
            env = os.environ.copy()
            env["PATH"] = str(root) + os.pathsep + env.get("PATH", "")
            env["GH_LOG"] = str(root / "gh.log")
            env["GH_SCENARIO"] = str(root / "scenario.json")
            env["CI_CHECK_ATTEMPTS"] = "1"
            env["CI_CHECK_INTERVAL"] = "0"
            env["CI_CHECK_REPO"] = "example/yuelink-ci"
            proc = subprocess.run(
                ["sh", str(ROOT / "scripts" / "ci-check.sh"), *args],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            proc.gh_log = (root / "gh.log").read_text(encoding="utf-8") if (root / "gh.log").exists() else ""  # type: ignore[attr-defined]
            return proc

    def test_usage_rejects_a_short_sha(self) -> None:
        proc = self.run_check({}, ["abc"])
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.gh_log, "")  # type: ignore[attr-defined]

    def test_green_runs_print_nothing_and_skip_the_release_build(self) -> None:
        scenario = {
            "policy-ci.yml": {
                "runs": [
                    {
                        "databaseId": 1,
                        "displayTitle": SHA,
                        "status": "completed",
                        "conclusion": "success",
                        "createdAt": "2026-11-12T00:00:00Z",
                    }
                ],
                "views": {"1": {"jobs": [{"name": "Policy", "conclusion": "success"}]}},
            },
            "dial-gate.yml": {
                "runs": [
                    {
                        "databaseId": 2,
                        "displayTitle": SHA,
                        "status": "completed",
                        "conclusion": "success",
                        "createdAt": "2026-11-12T00:00:01Z",
                    }
                ],
                "views": {
                    "2": {
                        "jobs": [
                            {"name": "Dial gate (ios)", "conclusion": "success"},
                            {"name": "Auth and storage Linux verification", "conclusion": "success"},
                        ]
                    }
                },
            },
        }
        proc = self.run_check(scenario, [SHA])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertNotIn("build.yml", proc.gh_log)  # type: ignore[attr-defined]
        self.assertIn("policy-ci.yml", proc.gh_log)  # type: ignore[attr-defined]
        self.assertIn("dial-gate.yml", proc.gh_log)  # type: ignore[attr-defined]

    def test_failing_jobs_are_printed(self) -> None:
        scenario = {
            "policy-ci.yml": {
                "runs": [
                    {
                        "databaseId": 3,
                        "displayTitle": SHA,
                        "status": "completed",
                        "conclusion": "failure",
                        "createdAt": "2026-11-12T00:00:00Z",
                    }
                ],
                "views": {"3": {"jobs": [{"name": "Updater manifest trust contract", "conclusion": "failure"}]}},
            },
            "dial-gate.yml": {
                "runs": [
                    {
                        "databaseId": 4,
                        "displayTitle": SHA,
                        "status": "completed",
                        "conclusion": "failure",
                        "createdAt": "2026-11-12T00:00:01Z",
                    }
                ],
                "views": {"4": {"jobs": [{"name": "Dial gate (ios)", "conclusion": "failure"}]}},
            },
        }
        proc = self.run_check(scenario, [SHA])
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(
            proc.stdout.splitlines(),
            [
                "policy-ci/Updater-manifest-trust-contract",
                "dial/Dial-gate-(ios)",
            ],
        )

    def test_missing_run_is_not_success(self) -> None:
        scenario = {
            "policy-ci.yml": {"runs": [], "views": {}},
            "dial-gate.yml": {"runs": [], "views": {}},
        }
        proc = self.run_check(scenario, [SHA])
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.splitlines(), ["policy-ci/missing", "dial/missing"])
