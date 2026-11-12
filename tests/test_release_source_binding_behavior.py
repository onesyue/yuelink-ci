from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/build.yml"
SOURCE = "a" * 40
BUILDER = "b" * 40
TAG_OBJECT = "c" * 40
VERIFIER = ROOT / "scripts/verify-source-attestation.sh"
GATES = [
    "source-master-ancestry",
    "android-release-signing-contract",
    "unreleased-dart-format",
    "flutter-analyze",
    "architecture-imports",
    "cocoapods-residue",
    "workflow-policy",
    "security-scan",
    "wintun-bundle",
    "release-metadata",
    "manifest-schema",
    "flutter-tests-floor-2044",
    "gitleaks-full-history",
    "govulncheck-core-service",
    "macos-integration",
    "windows-durability",
    "windows-native-core-runtime",
]


def binding_script(workflow: str) -> str:
    marker = "      - name: Resolve immutable source binding\n"
    start = workflow.index(marker)
    run = workflow.index("        run: |\n", start) + len("        run: |\n")
    lines: list[str] = []
    for line in workflow[run:].splitlines():
        if line and not line.startswith("          "):
            break
        lines.append(line[10:] if line else "")
    script = "\n".join(lines).rstrip() + "\n"
    if "Resolve immutable source binding" in script or "prepare:" in script:
        raise AssertionError("failed to isolate the real source-binding run block")
    return script


def valid_ref() -> str:
    return json.dumps({"object": {"type": "tag", "sha": TAG_OBJECT}})


def tag_json(*, verified: bool = True, builder: str = BUILDER, message: str | None = None) -> str:
    return json.dumps(
        {
            "tag": "v9.8.7",
            "object": {"type": "commit", "sha": builder},
            "verification": {
                "verified": verified,
                "reason": "valid" if verified else "unsigned",
            },
            "message": message
            or (
                "YueLink public builder v9.8.7\n\n"
                f"ATTESTED_SOURCE_COMMIT={SOURCE}\n"
                "SOURCE_ATTESTATION_RUN_ID=12345"
            ),
        }
    )


def source_run(**overrides: object) -> str:
    payload: dict[str, object] = {
        "id": 12345,
        "name": "Source attestation",
        "display_title": f"Source attestation {SOURCE}",
        "path": ".github/workflows/source-attestation.yml",
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "head_sha": BUILDER,
        "head_branch": "master",
        "run_attempt": 1,
        "workflow_id": 56789,
        "repository": {"full_name": "onesyue/yuelink-ci"},
    }
    payload.update(overrides)
    return json.dumps(payload)


def source_workflow(**overrides: object) -> str:
    payload: dict[str, object] = {
        "id": 56789,
        "name": "Source attestation",
        "path": ".github/workflows/source-attestation.yml",
        "state": "active",
    }
    payload.update(overrides)
    return json.dumps(payload)


def source_proof(**overrides: object) -> str:
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "sourceRepository": "onesyue/yuelink",
        "sourceSha": SOURCE,
        "releaseTag": "v9.8.7",
        "builderRepository": "onesyue/yuelink-ci",
        "workflowSha": BUILDER,
        "runId": "12345",
        "runAttempt": "1",
        "gates": GATES,
    }
    payload.update(overrides)
    return json.dumps(payload)

def source_runs(*runs: str) -> str:
    payloads = [json.loads(run) for run in runs] if runs else [json.loads(source_run())]
    return json.dumps({"workflow_runs": payloads})

_EXACT_SOURCE_REF = "ref: ${{ needs.source_binding.outputs.source_commit }}"
_SOURCE_EQUALITY = '[ "$BUILT_SOURCE_COMMIT" = "$ATTESTED_SOURCE_COMMIT" ]'
_SOURCE_DEPLOY_KEY = "ssh-key: ${{ secrets.SRC_DEPLOY_KEY }}"
_COVERAGE_SOURCE_CHECKOUTS = (
    "Checkout attested source for one coverage shard",
    "Checkout attested source for the coverage join",
)


def public_source_binding_issues(workflow: str) -> list[str]:
    """Bind every exact public source checkout. Do not freeze how many exist.

    A checkout of onesyue/yuelink whose ref is exactly the attested source
    commit must carry the deploy key, and the same job must compare HEAD to
    that commit. The dial candidate uses a compound ref and the baseline
    stays on its reviewed tag, so neither is an exact attested checkout.
    Coverage shard and join checkouts belong to that derived set.
    """
    issues: list[str] = []
    bound: list[str] = []
    jobs = re.split(r"(?m)^(?=  [A-Za-z0-9_-]+:\s*$)", workflow)
    for job in jobs:
        steps = re.split(r"(?m)^(?=      - )", job)
        for step in steps:
            if "repository: onesyue/yuelink\n" not in step:
                continue
            if _EXACT_SOURCE_REF not in step:
                continue
            name_match = re.search(r"(?m)^      - name: (.+)$", step)
            name = (
                name_match.group(1).strip()
                if name_match
                else "<unnamed attested checkout>"
            )
            bound.append(name)
            if _SOURCE_DEPLOY_KEY not in step:
                issues.append(
                    f"{name} checks out public source without the deploy key"
                )
            if _SOURCE_EQUALITY not in job:
                issues.append(
                    f"{name} does not compare HEAD to the attested source"
                )
    if not bound:
        issues.append("no public source checkout is bound to the attested commit")
    for name in _COVERAGE_SOURCE_CHECKOUTS:
        if name not in bound:
            issues.append(f"missing bound checkout: {name}")
    return issues

class ReleaseSourceBindingBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = WORKFLOW.read_text(encoding="utf-8")
        self.script = binding_script(self.workflow)

    def run_binding(
        self,
        *,
        event: str = "push",
        ref_json: str | None = None,
        tag: str | None = None,
        run: str | None = None,
        workflow: str | None = None,
        proof: str | None = None,
        attestation_ok: bool = True,
        dispatch_source: str = "",
        runs: str | None = None,
        ref_name: str = "v9.8.7",
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            gh = fake_bin / "gh"
            gh.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    set -euo pipefail
                    printf '%s\\n' "$*" >> "${FAKE_GH_LOG:-/dev/null}"
                    if [ "$1" = api ]; then
                      case "$2" in
                        *'/actions/workflows/source-attestation.yml/runs'*) printf '%s' "$FAKE_RUNS_JSON" ;;
                        *'/git/ref/tags/'*) printf '%s' "$FAKE_REF_JSON" ;;
                        *'/git/tags/'*) printf '%s' "$FAKE_TAG_JSON" ;;
                        *'/actions/workflows/source-attestation.yml') printf '%s' "$FAKE_WORKFLOW_JSON" ;;
                        *'/actions/runs/'*) printf '%s' "$FAKE_RUN_JSON" ;;
                        *) exit 97 ;;
                      esac
                    elif [ "$1 $2" = 'run download' ]; then
                      shift 2
                      output=''
                      while [ "$#" -gt 0 ]; do
                        case "$1" in
                          --dir) output="$2"; shift 2 ;;
                          *) shift ;;
                        esac
                      done
                      [ -n "$output" ] || exit 96
                      mkdir -p "$output"
                      printf '%s' "$FAKE_PROOF_JSON" > "$output/source-attestation.json"
                    elif [ "$1 $2" = 'attestation verify' ]; then
                      [ "$FAKE_ATTESTATION_OK" = 1 ]
                    else
                      exit 95
                    fi
                    """
                ),
                encoding="utf-8",
            )
            gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
            sleep = fake_bin / "sleep"
            sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            sleep.chmod(sleep.stat().st_mode | stat.S_IXUSR)
            output = tmp_path / "github-output"
            output.touch()
            gh_log = tmp_path / "gh.log"
            gh_log.touch()
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "EVENT_NAME": event,
                "REPOSITORY": "onesyue/yuelink-ci",
                "REF": f"refs/tags/{ref_name}"
                if event != "workflow_dispatch"
                else "refs/heads/master",
                "REF_NAME": ref_name if event != "workflow_dispatch" else "master",
                "TRIGGER_COMMIT": BUILDER,
                "DISPATCH_SOURCE_SHA": dispatch_source,
                "FAKE_RUNS_JSON": source_runs() if runs is None else runs,
                "FAKE_GH_LOG": str(gh_log),
                "GITHUB_OUTPUT": str(output),
                "FAKE_REF_JSON": ref_json or valid_ref(),
                "FAKE_TAG_JSON": tag or tag_json(),
                "FAKE_RUN_JSON": run or source_run(),
                "FAKE_WORKFLOW_JSON": workflow or source_workflow(),
                "FAKE_PROOF_JSON": proof or source_proof(),
                "FAKE_ATTESTATION_OK": "1" if attestation_ok else "0",
                "SOURCE_ATTESTATION_VERIFIER": str(VERIFIER),
                "TMPDIR": str(tmp_path),
            }
            result = subprocess.run(
                ["/bin/bash", "-c", self.script],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                check=False,
            )
            result.gh_log = gh_log.read_text(encoding="utf-8")
            return result, output.read_text(encoding="utf-8")

    def test_verified_public_annotation_selects_exact_source(self) -> None:
        result, output = self.run_binding()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(
            output,
            f"source_commit={SOURCE}\nattestation_run_id=12345\n",
        )
        self.assertIn(f"ATTESTED_SOURCE_COMMIT={SOURCE}", result.stdout)

    def test_dispatch_looks_up_the_green_attestation_run(self) -> None:
        wrong_branch = source_run(
            id=99999,
            head_branch="other",
            created_at="2026-12-01T00:00:00Z",
        )
        wrong_builder = source_run(
            id=88888,
            head_sha="d" * 40,
            created_at="2026-12-02T00:00:00Z",
        )
        older = source_run(id=11111, created_at="2026-01-01T00:00:00Z")
        current = source_run(id=12345, created_at="2026-11-12T00:00:00Z")
        result, output = self.run_binding(
            event="workflow_dispatch",
            dispatch_source=SOURCE,
            runs=source_runs(wrong_branch, wrong_builder, older, current),
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(
            output,
            f"source_commit={SOURCE}\nattestation_run_id=12345\n",
        )
        self.assertIn("exact public source attestation verified", result.stdout)
        self.assertIn("attestation verify", result.gh_log)
        self.assertNotIn("attested_source_commit:", self.workflow)
        self.assertNotIn("source_attestation_run_id:", self.workflow)
        self.assertIn("\n      source_sha:\n", self.workflow)
        self.assertIn("dialgate_candidate_ref:", self.workflow)

    def test_dispatch_rejects_a_sha_before_any_lookup(self) -> None:
        for bad in ("main", SOURCE.upper(), ""):
            with self.subTest(bad=bad):
                result, output = self.run_binding(
                    event="workflow_dispatch",
                    dispatch_source=bad,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(output, "")
                self.assertEqual(result.stdout.count("::error::"), 1)
                self.assertIn("source_sha must be 40 lowercase hex", result.stdout)
                self.assertEqual(result.gh_log, "")

    def test_dispatch_rejects_a_sha_with_no_green_run(self) -> None:
        result, output = self.run_binding(
            event="workflow_dispatch",
            dispatch_source=SOURCE,
            runs=json.dumps({"workflow_runs": []}),
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(output, "")
        self.assertEqual(result.stdout.count("::error::"), 1)
        self.assertIn(
            f"no green Source attestation for {SOURCE} on builder {BUILDER}",
            result.stdout,
        )
        self.assertNotIn("run download", result.gh_log)
        self.assertNotIn("attestation verify", result.gh_log)

    def test_dispatch_rejects_a_proof_for_another_source(self) -> None:
        result, output = self.run_binding(
            event="workflow_dispatch",
            dispatch_source=SOURCE,
            proof=source_proof(sourceSha="d" * 40),
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(output, "")
        self.assertEqual(result.stdout.count("::error::"), 1)
        self.assertIn(
            f"source attestation proof is for {'d' * 40} not {SOURCE}",
            result.stdout,
        )
        self.assertNotIn("exact source/tag/builder", result.stdout)
        self.assertNotIn("attestation verify", result.gh_log)

    def test_dispatch_rejects_a_non_release_proof_tag(self) -> None:
        result, output = self.run_binding(
            event="workflow_dispatch",
            dispatch_source=SOURCE,
            proof=source_proof(releaseTag="v9.8.7-pre.1"),
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(output, "")
        self.assertEqual(result.stdout.count("::error::"), 1)
        self.assertIn("release tag is not vX.Y.Z", result.stdout)
        self.assertNotIn("attestation verify", result.gh_log)

    def test_unverified_or_wrong_builder_tag_is_rejected(self) -> None:
        for payload in (
            tag_json(verified=False),
            tag_json(builder="d" * 40),
        ):
            with self.subTest(payload=payload):
                result, output = self.run_binding(tag=payload)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(output, "")

    def test_prerelease_tag_is_rejected_before_remote_evidence_lookup(self) -> None:
        result, output = self.run_binding(ref_name="v9.8.7-pre.1")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("prerelease is retired", result.stdout)
        self.assertEqual(output, "")

    def test_missing_or_duplicate_annotation_binding_is_rejected(self) -> None:
        messages = (
            "YueLink public builder v9.8.7\nSOURCE_ATTESTATION_RUN_ID=12345",
            (
                f"ATTESTED_SOURCE_COMMIT={SOURCE}\n"
                f"ATTESTED_SOURCE_COMMIT={SOURCE}\n"
                "SOURCE_ATTESTATION_RUN_ID=12345"
            ),
        )
        for message in messages:
            with self.subTest(message=message):
                result, output = self.run_binding(tag=tag_json(message=message))
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(output, "")

    def test_signed_tag_cannot_name_a_false_source_run(self) -> None:
        invalid_runs = (
            source_run(id=99999),
            source_run(path=".github/workflows/build.yml"),
            source_run(event="push"),
            source_run(conclusion="failure"),
            source_run(head_sha="d" * 40),
            source_run(display_title="Source attestation arbitrary"),
            source_run(workflow_id=98765),
            source_run(name="unexpected run name"),
        )
        for run in invalid_runs:
            with self.subTest(run=run):
                result, output = self.run_binding(run=run)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(output, "")

        invalid_workflows = (
            source_workflow(id=98765),
            source_workflow(name="Build YueLink"),
            source_workflow(path=".github/workflows/build.yml"),
            source_workflow(state="disabled_manually"),
        )
        for workflow in invalid_workflows:
            with self.subTest(workflow=workflow):
                result, output = self.run_binding(workflow=workflow)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(output, "")

    def test_current_github_dynamic_run_name_is_accepted(self) -> None:
        result, output = self.run_binding(
            run=source_run(name=f"Source attestation {SOURCE}")
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(output, f"source_commit={SOURCE}\nattestation_run_id=12345\n")

    def test_wrong_proof_or_failed_attestation_is_rejected(self) -> None:
        invalid_proofs = (
            source_proof(sourceSha="d" * 40),
            source_proof(workflowSha="d" * 40),
            source_proof(runId="99999"),
            source_proof(gates=GATES[:-1]),
        )
        for proof in invalid_proofs:
            with self.subTest(proof=proof):
                result, output = self.run_binding(proof=proof)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(output, "")
        result, output = self.run_binding(attestation_ok=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(output, "")

    def test_all_public_source_checkouts_and_summary_are_bound(self) -> None:
        self.assertEqual(public_source_binding_issues(self.workflow), [])
        for marker in (
            "Checkout exact source-attestation verifier",
            'bash "$SOURCE_ATTESTATION_VERIFIER"',
            "sourceCommit: $sourceCommit",
            "sha256SumsSha256: $sha256SumsSha256",
            "ATTESTED_SOURCE_COMMIT:",
            "BUILT_SOURCE_COMMIT:",
        ):
            self.assertIn(marker, self.workflow)

    def test_each_attested_source_checkout_is_mutation_sensitive(self) -> None:
        workflow = self.workflow
        seen = 0
        jobs = re.split(r"(?m)^(?=  [A-Za-z0-9_-]+:\s*$)", workflow)
        for job in jobs:
            steps = re.split(r"(?m)^(?=      - )", job)
            for step in steps:
                if "repository: onesyue/yuelink\n" not in step:
                    continue
                if _EXACT_SOURCE_REF not in step:
                    continue
                seen += 1
                without_key = step.replace(
                    _SOURCE_DEPLOY_KEY, "ssh-key: ${{ secrets.MISSING }}", 1
                )
                self.assertNotEqual(without_key, step)
                self.assertTrue(
                    public_source_binding_issues(workflow.replace(step, without_key, 1))
                )
                self.assertIn(_SOURCE_EQUALITY, job)
                self.assertTrue(
                    public_source_binding_issues(
                        workflow.replace(job, job.replace(_SOURCE_EQUALITY, "true"), 1)
                    )
                )
        self.assertGreaterEqual(seen, 1)
        for name in _COVERAGE_SOURCE_CHECKOUTS:
            mutated = workflow.replace(
                f"      - name: {name}\n", "      - name: unbound checkout\n", 1
            )
            issues = public_source_binding_issues(mutated)
            self.assertTrue(any(name in issue for issue in issues), issues)

    def test_platform_identity_uses_attested_commit_not_private_tag_ref(self) -> None:
        start = self.workflow.index("      - name: Record exact sideload identity")
        end = self.workflow.index("      - name: Attest build provenance", start)
        identity = self.workflow[start:end]
        self.assertIn(
            "ATTESTED_SOURCE_COMMIT: "
            "${{ needs.source_binding.outputs.source_commit }}",
            identity,
        )
        self.assertIn(
            '[ "$source_commit" = "$ATTESTED_SOURCE_COMMIT" ]',
            identity,
        )
        self.assertNotIn("git rev-list", identity)


if __name__ == "__main__":
    unittest.main()
