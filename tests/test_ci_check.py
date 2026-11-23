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
        scenario = json.loads(open(os.environ["GH_SCENARIO"], encoding="utf-8").read())
        if sys.argv[1:3] == ["workflow", "run"]:
            if scenario.get(sys.argv[3], {}).get("dispatch_fails"):
                print("example dispatch denied", file=sys.stderr)
                raise SystemExit(7)

            run_id = scenario[sys.argv[3]].get("dispatch_id")
            if run_id:
                print(f"https://github.com/example/yuelink-ci/actions/runs/{run_id}")
            raise SystemExit(0)
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
                timeout=15,
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
                "dispatch_id": 1,
                "views": {"1": {"status": "completed","jobs": [{"name": "Policy", "conclusion": "success"}]}},
            },
            "dial-gate.yml": {
                "dispatch_id": 2,
                "views": {
                    "2": {
                        "status": "completed",
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
        calls = [json.loads(line) for line in proc.gh_log.splitlines()]  # type: ignore[attr-defined]
        self.assertEqual([call[:4] for call in calls], [
            ["workflow", "run", "policy-ci.yml", "--repo"],
            ["workflow", "run", "dial-gate.yml", "--repo"],
            ["run", "view", "1", "--repo"],
            ["run", "view", "2", "--repo"],
        ])

    def test_failing_jobs_are_printed(self) -> None:
        scenario = {
            "policy-ci.yml": {
                "dispatch_id": 3,
                "views": {"3": {"status": "completed","jobs": [{"name": "Updater manifest trust contract", "conclusion": "failure"}]}},
            },
            "dial-gate.yml": {
                "dispatch_id": 4,
                "views": {"4": {"status": "completed","jobs": [{"name": "Dial gate (ios)", "conclusion": "failure"}]}},
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

    def test_dispatch_failure_stops_before_dial_or_polling(self) -> None:
        proc = self.run_check({"policy-ci.yml": {"dispatch_fails": True}}, [SHA])
        self.assertEqual(proc.returncode, 7, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertIn("example dispatch denied", proc.stderr)
        calls = [json.loads(line) for line in proc.gh_log.splitlines()]  # type: ignore[attr-defined]
        self.assertEqual([call[:3] for call in calls], [["workflow", "run", "policy-ci.yml"]])

    def test_incomplete_or_unusable_job_evidence_is_not_success(self) -> None:
        dial = {
            "dispatch_id": 2,
            "views": {"2": {"status": "completed", "jobs": [{"name": "Dial", "conclusion": "success"}]}},
        }
        for view, failure in (
            ({"status": "in_progress"}, "incomplete"),
            ({"status": "completed"}, "unreadable"),
            ({"status": "completed", "jobs": []}, "no-jobs"),
        ):
            with self.subTest(failure=failure):
                policy = {"dispatch_id": 1, "views": {"1": view}}
                proc = self.run_check({"policy-ci.yml": policy, "dial-gate.yml": dial}, [SHA])
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertEqual(proc.stdout.splitlines(), [f"policy-ci/{failure}"])

    def test_dispatch_without_run_identity_cannot_use_previous_green_runs(self) -> None:
        scenario = {
            workflow: {
                "runs": [{"databaseId": run_id, "displayTitle": title, "status": "completed"}],
                "views": {str(run_id): {"jobs": [{"conclusion": "success"}]}},
            }
            for workflow, run_id, title in (
                ("policy-ci.yml", 5, "b" * 40),
                ("dial-gate.yml", 6, SHA),
            )
        }
        proc = self.run_check(scenario, [SHA])
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.splitlines(), ["policy-ci/missing", "dial/missing"])

    def test_pending_dispatch_cannot_pass_on_current_jobs(self) -> None:
        scenario = {
            "policy-ci.yml": {"dispatch_id": 7, "views": {
                "7": {"status": "in_progress", "jobs": [{"conclusion": "success"}]},
            }},
            "dial-gate.yml": {"dispatch_id": 8, "views": {
                "8": {"status": "completed", "jobs": [{"conclusion": "success"}]},
            }},
        }
        proc = self.run_check(scenario, [SHA])
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.splitlines(), ["policy-ci/incomplete"])
