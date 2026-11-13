"""Latest-release promotion proof: never downgrade on incomplete evidence."""

from __future__ import annotations

import copy
import importlib.util
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "dial_baseline", ROOT / "scripts/select-dial-baseline.py"
)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)
CUTOFF = mod.timestamp("2026-11-13T00:00:00Z")
SOURCE, BUILDER, TAG_OBJECT = "a" * 40, "b" * 40, "c" * 40
TAG = "v1.3.176"


class FakeGitHub:
    def __init__(self):
        self.binding = {
            "tag": TAG,
            "source": SOURCE,
            "builder": BUILDER,
            "tag_object": TAG_OBJECT,
            "attestation_run": "123",
        }
        self.release = {
            "id": 7,
            "tag_name": TAG,
            "draft": False,
            "prerelease": False,
            "published_at": "2026-11-12T00:00:00Z",
        }
        self.releases = [self.release]
        self.run = {
            "id": 456,
            "run_attempt": 2,
            "workflow_id": 789,
            "name": "Build YueLink",
            "path": ".github/workflows/build.yml",
            "event": "push",
            "head_branch": TAG,
            "head_sha": BUILDER,
            "repository": {"full_name": mod.REPOSITORY},
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-11-12T00:00:01Z",
            "updated_at": "2026-11-12T01:00:00Z",
        }
        self.runs = [self.run]
        self.attempt = self.run
        self.current_run = self.run
        self.workflow = {
            "id": 789,
            "name": "Build YueLink",
            "path": ".github/workflows/build.yml",
            "state": "active",
        }
        self.attestation = {
            "id": 123,
            "run_attempt": 1,
            "updated_at": "2026-11-11T00:00:00Z",
        }
        self.job = {
            "id": 888,
            "run_id": 456,
            "run_attempt": 2,
            "head_sha": BUILDER,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2026-11-12T01:00:00Z",
            "steps": [
                {
                    "name": mod.PROMOTION_STEP,
                    "number": 9,
                    "status": "completed",
                    "conclusion": "success",
                    "completed_at": "2026-11-12T00:59:00Z",
                }
            ],
        }
        self.jobs = [self.job]
        self.calls = []
        self.tag_error = False
        self.attest_error = False
        self.attested = []
        self.pages_override = {}

    def get(self, endpoint):
        self.calls.append(endpoint)
        if endpoint in self.pages_override:
            return copy.deepcopy(self.pages_override[endpoint])
        if endpoint.startswith("releases?per_page=100&page="):
            page = int(endpoint.rsplit("=", 1)[1])
            return copy.deepcopy(self.releases[(page - 1) * 100 : page * 100])
        if endpoint == "releases/7":
            return copy.deepcopy(self.release)
        if endpoint == "actions/runs/123":
            return copy.deepcopy(self.attestation)
        if endpoint == "actions/workflows/build.yml":
            return copy.deepcopy(self.workflow)
        if endpoint.startswith("actions/workflows/build.yml/runs?"):
            return {
                "total_count": len(self.runs),
                "workflow_runs": copy.deepcopy(self.runs),
            }
        if endpoint == "actions/runs/456/attempts/2":
            return copy.deepcopy(self.attempt)
        if endpoint.startswith("actions/runs/456/attempts/2/jobs?"):
            return {"total_count": len(self.jobs), "jobs": copy.deepcopy(self.jobs)}
        if endpoint == "actions/runs/456":
            return copy.deepcopy(self.current_run)
        raise AssertionError(f"unexpected read: {endpoint}")

    def tag(self, tag):
        self.calls.append("tag:" + tag)
        if self.tag_error:
            raise subprocess.CalledProcessError(1, "shared-tag-verifier")
        return copy.deepcopy(self.binding)

    def attest(self, binding, release_id):
        self.attested.append(copy.deepcopy(binding))
        if self.attest_error:
            raise subprocess.CalledProcessError(1, "existing-attestation-verifier")


class BaselineSelectionTests(unittest.TestCase):
    def test_exact_success_freezes_all_executed_identities(self):
        api = FakeGitHub()
        receipt = mod.select(api, CUTOFF)
        self.assertEqual(receipt["source"], SOURCE)
        self.assertEqual(receipt["build_attempt"], 2)
        self.assertEqual(receipt["promotion_job"], 888)
        self.assertEqual(receipt["promotion_step"], 9)
        self.assertEqual(receipt["attestation_attempt"], 1)
        self.assertEqual(receipt["selection_cutoff"], "2026-11-13T00:00:00Z")
        self.assertEqual(api.attested, [api.binding])
        self.assertTrue(any("attempts/2/jobs" in call for call in api.calls))

    def test_latest_unproven_release_never_falls_back_to_older(self):
        for failure in ("tag", "attest", "promotion"):
            api = FakeGitHub()
            api.releases.append(
                dict(
                    api.release,
                    id=6,
                    tag_name="v1.3.174",
                    published_at="2026-11-10T00:00:00Z",
                )
            )
            if failure == "tag":
                api.tag_error = True
            elif failure == "attest":
                api.attest_error = True
            else:
                api.job["steps"][0]["conclusion"] = "skipped"
            with (
                self.subTest(failure=failure),
                self.assertRaises((ValueError, subprocess.CalledProcessError)),
            ):
                mod.select(api, CUTOFF)
            self.assertNotIn("tag:v1.3.174", api.calls)

    def test_rejects_partial_skipped_wrong_or_conflicting_evidence(self):
        changes = {
            "builder": lambda a: a.run.update(head_sha="d" * 40),
            "tag": lambda a: a.run.update(head_branch="v1.3.174"),
            "wrong workflow": lambda a: a.run.update(workflow_id=10),
            "wrong event": lambda a: a.run.update(event="workflow_dispatch"),
            "wrong repository": lambda a: a.run.update(
                repository={"full_name": "other/repo"}
            ),
            "pending": lambda a: a.run.update(status="in_progress", conclusion=None),
            "failed": lambda a: a.run.update(conclusion="failure"),
            "run after cutoff": lambda a: a.run.update(
                updated_at="2026-11-13T01:00:00Z"
            ),
            "attest after cutoff": lambda a: a.attestation.update(
                updated_at="2026-11-13T01:00:00Z"
            ),
            "attempt mismatch": lambda a: setattr(
                a, "attempt", dict(a.run, run_attempt=1)
            ),
            "job attempt mismatch": lambda a: a.job.update(run_attempt=1),
            "job run mismatch": lambda a: a.job.update(run_id=457),
            "job source mismatch": lambda a: a.job.update(head_sha="d" * 40),
            "missing jobs": lambda a: setattr(a, "jobs", []),
            "missing step": lambda a: a.job.update(steps=[]),
            "duplicate step": lambda a: a.job["steps"].append(
                copy.deepcopy(a.job["steps"][0])
            ),
            "skipped job": lambda a: a.job.update(conclusion="skipped"),
            "failed promotion": lambda a: a.job["steps"][0].update(
                conclusion="failure"
            ),
            "skipped promotion": lambda a: a.job["steps"][0].update(
                conclusion="skipped"
            ),
            "promotion pending": lambda a: a.job["steps"][0].update(
                status="in_progress"
            ),
            "promotion after cutoff": lambda a: a.job["steps"][0].update(
                completed_at="2026-11-13T01:00:00Z"
            ),
            "rerun during verification": lambda a: setattr(
                a, "current_run", dict(a.run, run_attempt=3)
            ),
            "missing source binding": lambda a: a.binding.pop("source"),
            "wrong resolved tag": lambda a: a.binding.update(tag="v1.3.174"),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                api = FakeGitHub()
                change(api)
                with self.assertRaises(ValueError):
                    mod.select(api, CUTOFF)

    def test_newest_run_failure_does_not_reuse_older_success(self):
        api = FakeGitHub()
        api.runs.append(
            dict(
                api.run,
                id=457,
                created_at="2026-11-12T02:00:00Z",
                updated_at="2026-11-12T03:00:00Z",
                conclusion="failure",
            )
        )
        with self.assertRaisesRegex(ValueError, "not successful"):
            mod.select(api, CUTOFF)

    def test_cutoff_and_pagination_do_not_depend_on_listing_order(self):
        api = FakeGitHub()
        api.releases = [
            dict(
                api.release,
                id=100 + i,
                tag_name=f"v9.0.{i}",
                published_at="2026-11-14T00:00:00Z",
            )
            for i in range(100)
        ] + [api.release]
        receipt = mod.select(api, CUTOFF)
        self.assertEqual(receipt["tag"], TAG)
        self.assertIn("releases?per_page=100&page=2", api.calls)

    def test_prerelease_and_draft_are_not_stable_candidates(self):
        api = FakeGitHub()
        api.releases.extend(
            [
                dict(api.release, id=8, draft=True),
                dict(api.release, id=9, prerelease=True),
                dict(api.release, id=10, tag_name="v1.3.177-rc1"),
            ]
        )
        self.assertEqual(mod.select(api, CUTOFF)["tag"], TAG)

    def test_duplicate_or_truncated_pages_fail_closed(self):
        api = FakeGitHub()
        api.releases.append(api.release)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            mod.select(api, CUTOFF)
        api = FakeGitHub()
        endpoint = f"actions/workflows/build.yml/runs?event=push&branch={TAG}&per_page=100&page=1"
        api.pages_override[endpoint] = {"total_count": 2, "workflow_runs": [api.run]}
        with self.assertRaisesRegex(ValueError, "incomplete GitHub pagination"):
            mod.select(api, CUTOFF)


class BaselineWorkflowTests(unittest.TestCase):
    def test_selector_is_single_and_every_platform_consumes_its_sha(self):
        workflow = (ROOT / ".github/workflows/dial-gate.yml").read_text()
        self.assertEqual(workflow.count("python3 scripts/select-dial-baseline.py"), 1)
        self.assertIn("needs: [baseline_binding]", workflow)
        self.assertIn(
            "ref: ${{ needs.baseline_binding.outputs.baseline_source }}", workflow
        )
        self.assertNotIn("ref: v1.3.174", workflow)
        self.assertIn(
            'test "$(git -C baseline rev-parse HEAD)" = "$BASELINE_SOURCE"', workflow
        )
        self.assertIn("name: dial-baseline-receipt", workflow)
        self.assertIn(
            "value: ${{ jobs.baseline_binding.outputs.baseline_source }}", workflow
        )
        build = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertIn(
            "EXPECTED_BASELINE_SOURCE: ${{ needs.dial_gate.outputs.baseline_source }}",
            build,
        )

    def test_plan_and_selector_share_tag_and_attestation_verifiers(self):
        plan = (ROOT / ".github/workflows/plan.yml").read_text()
        self.assertIn('/resolve-release-tag.sh" "$REF_NAME" "$TRIGGER_COMMIT"', plan)
        self.assertNotIn("tag_json=", plan)
        self.assertIn(
            '"resolve-release-tag.sh"',
            (ROOT / "scripts/select-dial-baseline.py").read_text(),
        )
        self.assertIn(
            '"preserve-source-proof.py"',
            (ROOT / "scripts/select-dial-baseline.py").read_text(),
        )
        self.assertIn(
            '"verify-source-attestation.sh"',
            (ROOT / "scripts/preserve-source-proof.py").read_text(),
        )
