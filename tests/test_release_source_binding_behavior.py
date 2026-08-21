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
        "repository": {"full_name": "onesyue/yuelink-ci"},
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
        proof: str | None = None,
        attestation_ok: bool = True,
        dispatch_source: str = "",
        dispatch_run: str = "",
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
                    if [ "$1" = api ]; then
                      case "$2" in
                        *'/git/ref/tags/'*) printf '%s' "$FAKE_REF_JSON" ;;
                        *'/git/tags/'*) printf '%s' "$FAKE_TAG_JSON" ;;
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
                "DISPATCH_SOURCE_COMMIT": dispatch_source,
                "DISPATCH_ATTESTATION_RUN_ID": dispatch_run,
                "GITHUB_OUTPUT": str(output),
                "FAKE_REF_JSON": ref_json or valid_ref(),
                "FAKE_TAG_JSON": tag or tag_json(),
                "FAKE_RUN_JSON": run or source_run(),
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
            return result, output.read_text(encoding="utf-8")

    def test_verified_public_annotation_selects_exact_source(self) -> None:
        result, output = self.run_binding()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(
            output,
            f"source_commit={SOURCE}\nattestation_run_id=12345\n",
        )
        self.assertIn(f"ATTESTED_SOURCE_COMMIT={SOURCE}", result.stdout)

    def test_dispatch_requires_exact_machine_inputs(self) -> None:
        result, output = self.run_binding(
            event="workflow_dispatch", dispatch_source=SOURCE, dispatch_run="9876"
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(output, f"source_commit={SOURCE}\nattestation_run_id=9876\n")
        bad, _ = self.run_binding(
            event="workflow_dispatch", dispatch_source="main", dispatch_run="9876"
        )
        self.assertNotEqual(bad.returncode, 0, bad.stdout)

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
        )
        for run in invalid_runs:
            with self.subTest(run=run):
                result, output = self.run_binding(run=run)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(output, "")

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
        self.assertEqual(
            self.workflow.count("ref: ${{ needs.source_binding.outputs.source_commit }}"),
            3,
        )
        self.assertEqual(
            self.workflow.count('[ "$BUILT_SOURCE_COMMIT" = "$ATTESTED_SOURCE_COMMIT" ]'),
            4,
        )
        for marker in (
            "Checkout exact source-attestation verifier",
            'bash "$SOURCE_ATTESTATION_VERIFIER"',
            "sourceCommit: $sourceCommit",
            "sha256SumsSha256: $sha256SumsSha256",
            "ATTESTED_SOURCE_COMMIT:",
            "BUILT_SOURCE_COMMIT:",
        ):
            self.assertIn(marker, self.workflow)


if __name__ == "__main__":
    unittest.main()
