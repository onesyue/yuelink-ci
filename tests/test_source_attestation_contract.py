from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/source-attestation.yml"
RELEASE = ROOT / "release.sh"
README = ROOT / "README.md"
DEPENDABOT = ROOT / ".github/dependabot.yml"

WORKFLOW_MARKERS = (
    "run-name: Source attestation ${{ inputs.source_sha }}",
    '[[ ! "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]]',
    "git show-ref --verify --quiet refs/remotes/origin/master",
    'git merge-base --is-ancestor "$SOURCE_SHA" refs/remotes/origin/master',
    "bash scripts/ci/test_android_release_signing.sh",
    'dart format --output=none --set-exit-if-changed "${changed_files[@]}"',
    "flutter analyze --no-fatal-infos",
    "bash scripts/check_imports.sh",
    "bash scripts/check_cocoapods_residue.sh",
    "dart run tool/automation/workflow_guard.dart",
    "bash scripts/security_scan.sh --json",
    "test -f windows/third_party/wintun/wintun.sha256",
    'dart run tool/automation/release_guard.dart --tag "$RELEASE_TAG"',
    "dart run tool/automation/manifest_guard.dart --self-check",
    "Checkout exact attestation parser",
    "ref: ${{ github.sha }}",
    "flutter test --no-pub --coverage --machine",
    ".source-attestation-tools/scripts/count-flutter-machine-tests.py",
    "/tmp/flutter-tests.jsonl --minimum 2044",
    "GITLEAKS_VERSION: '8.30.1'",
    "GITLEAKS_LINUX_X64_SHA256: '551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb'",
    "gitleaks/releases/download/v${GITLEAKS_VERSION}",
    "| sha256sum -c -",
    'test "$(gitleaks version)" = "$GITLEAKS_VERSION"',
    "gitleaks git --config .gitleaks.toml --redact --verbose .",
    "module: [core, service]",
    "GO_VERSION: '1.26.7'",
    "MODULE: ${{ matrix.module }}",
    'bash scripts/ci/govulncheck_targets.sh "$MODULE"',
    "flutter test integration_test/ -d macos --reporter expanded",
    "dart run tool/windows_durability_probe.dart",
    "needs: [source_contract, gitleaks, govulncheck, macos_integration, windows_durability]",
    "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/attest-build-provenance@a2bbfa25375fe432b6a289bc6b6cd05ecd0c4c32",
    '"flutter-tests-floor-2044"',
    '"gitleaks-full-history"',
    '"govulncheck-core-service"',
    '"macos-integration"',
    '"windows-durability"',
)

RELEASE_MARKERS = (
    'repos/onesyue/yuelink/commits/$TAG',
    "actions/workflows/source-attestation.yml/runs?event=workflow_dispatch&status=success",
    '.display_title == \\"$ATTESTATION_TITLE\\"',
    '.head_sha == \\"$BUILDER_COMMIT\\"',
    '.conclusion == \\"success\\"',
    'gh run download "$ATTESTATION_RUN_ID"',
    '.sourceSha == $source',
    '.releaseTag == $release',
    '.workflowSha == $builder',
    '.runId == $run',
    "(.gates | sort) == ([",
    'gh attestation verify "$ATTESTATION_PROOF"',
    "--signer-workflow onesyue/yuelink-ci/.github/workflows/source-attestation.yml",
    '--source-digest "$BUILDER_COMMIT"',
    'git tag "$TAG"',
)

README_MARKERS = (
    "source_sha=\"$SOURCE_SHA\"",
    "reviewed floor of 2044",
    "local test output is never accepted",
    "GitHub artifact attestation",
)


def contract_issues(workflow: str, release: str, readme: str) -> list[str]:
    issues: list[str] = []

    for marker in WORKFLOW_MARKERS:
        if marker not in workflow:
            issues.append(f"source workflow missing {marker!r}")
    for marker in RELEASE_MARKERS:
        if marker not in release:
            issues.append(f"release gate missing {marker!r}")
    for marker in README_MARKERS:
        if marker not in readme:
            issues.append(f"policy missing {marker!r}")

    source_checkouts = re.findall(
        r"(?m)^\s*repository:\s*onesyue/yuelink\s*$", workflow
    )
    if len(source_checkouts) != 5:
        issues.append("every one of the five source jobs must checkout yuelink")
    if workflow.count("fetch-depth: 0") != 5:
        issues.append("every source checkout must use full history")
    if workflow.count("submodules: recursive") != 5:
        issues.append("every source checkout must resolve recursive modules")
    if "write-all" in workflow:
        issues.append("source workflow may not use write-all")

    external_uses = re.findall(r"(?m)^\s*uses:\s*([^\s#]+)", workflow)
    for action in external_uses:
        if action.startswith("./"):
            continue
        _, separator, ref = action.rpartition("@")
        if not separator or not re.fullmatch(r"[0-9a-f]{40}", ref):
            issues.append(f"mutable or malformed action ref: {action}")

    source_gate = release.find("gh attestation verify")
    tag_creation = release.find('git tag "$TAG"')
    if source_gate < 0 or tag_creation < 0 or source_gate > tag_creation:
        issues.append("proven source gate must run before public tag creation")
    return issues


class SourceAttestationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = WORKFLOW.read_text(encoding="utf-8")
        self.release = RELEASE.read_text(encoding="utf-8")
        self.readme = README.read_text(encoding="utf-8")

    def test_current_contract_is_complete(self) -> None:
        self.assertEqual(contract_issues(self.workflow, self.release, self.readme), [])

    def test_each_critical_workflow_gate_is_mutation_sensitive(self) -> None:
        for marker in WORKFLOW_MARKERS:
            with self.subTest(marker=marker):
                mutated = self.workflow.replace(marker, "", 1)
                self.assertNotEqual(mutated, self.workflow)
                self.assertTrue(contract_issues(mutated, self.release, self.readme))

    def test_each_release_enforcement_is_mutation_sensitive(self) -> None:
        for marker in RELEASE_MARKERS:
            with self.subTest(marker=marker):
                mutated = self.release.replace(marker, "", 1)
                self.assertNotEqual(mutated, self.release)
                self.assertTrue(contract_issues(self.workflow, mutated, self.readme))

    def test_policy_cannot_drop_machine_proof_requirements(self) -> None:
        for marker in README_MARKERS:
            with self.subTest(marker=marker):
                mutated = self.readme.replace(marker, "", 1)
                self.assertNotEqual(mutated, self.readme)
                self.assertTrue(contract_issues(self.workflow, self.release, mutated))

    def test_mutable_action_ref_is_rejected(self) -> None:
        mutated = self.workflow.replace(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            "actions/checkout@v6",
            1,
        )
        issues = contract_issues(mutated, self.release, self.readme)
        self.assertTrue(any("mutable or malformed action ref" in issue for issue in issues))

    def test_every_public_workflow_action_is_commit_pinned(self) -> None:
        workflow_paths = sorted((ROOT / ".github/workflows").glob("*.yml"))
        workflow_paths += sorted((ROOT / ".github/workflows").glob("*.yaml"))
        action_count = 0
        for workflow_path in workflow_paths:
            workflow = workflow_path.read_text(encoding="utf-8")
            actions = re.findall(r"(?m)^\s*(?:-\s*)?uses:\s*([^\s#]+)", workflow)
            for action in actions:
                action_count += 1
                with self.subTest(workflow=workflow_path.name, action=action):
                    self.assertFalse(
                        action.startswith("./"),
                        "local/dynamic actions are outside the enabled SHA-pinning policy",
                    )
                    self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")
        self.assertGreater(action_count, 0, "workflow action inventory is unexpectedly empty")

    def test_sha_pinning_policy_has_dependabot_maintenance(self) -> None:
        dependabot = DEPENDABOT.read_text(encoding="utf-8")
        self.assertEqual(dependabot.count("package-ecosystem: github-actions"), 1)
        self.assertRegex(dependabot, r"(?m)^\s+directory: /$")
        self.assertRegex(dependabot, r"(?m)^\s+interval: weekly$")
        limit = re.search(r"(?m)^\s+open-pull-requests-limit:\s*(\d+)\s*$", dependabot)
        self.assertIsNotNone(limit)
        self.assertGreater(int(limit.group(1)), 0)
        self.assertIn("sha_pinning_required=true", self.readme)
        self.assertIn(
            "Local or dynamically\nresolved actions are intentionally absent",
            self.readme,
        )

    def test_run_scripts_do_not_expand_github_expressions(self) -> None:
        """Actions values enter shell scripts through step env, not templates."""

        for workflow_path in sorted((ROOT / ".github/workflows").glob("*.yml")):
            lines = workflow_path.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(lines):
                match = re.match(r"^(\s*)(?:-\s+)?run:\s*(.*)$", line)
                if match is None:
                    continue
                indent = len(match.group(1))
                value = match.group(2)
                script_lines = [value]
                if re.fullmatch(r"[>|][-+]?", value):
                    script_lines = []
                    for following in lines[index + 1 :]:
                        following_indent = len(following) - len(following.lstrip())
                        if following.strip() and following_indent <= indent:
                            break
                        script_lines.append(following)
                script = "\n".join(script_lines)
                with self.subTest(workflow=workflow_path.name, line=index + 1):
                    self.assertNotIn("${{", script)

    def test_r2_prune_runs_are_serialized(self) -> None:
        prune = (ROOT / ".github/workflows/prune-r2.yml").read_text(encoding="utf-8")
        self.assertIn("group: yuelink-r2-prune", prune)
        self.assertIn("cancel-in-progress: false", prune)

    def test_workflow_run_prune_validates_source_without_consuming_artifacts(self) -> None:
        prune = (ROOT / ".github/workflows/prune-r2.yml").read_text(encoding="utf-8")
        markers = (
            "zizmor: ignore[dangerous-triggers] no upstream artifacts/code",
            "Validate trusted Build workflow_run identity",
            "SOURCE_REPOSITORY: ${{ github.event.workflow_run.repository.full_name }}",
            "SOURCE_HEAD_REPOSITORY: ${{ github.event.workflow_run.head_repository.full_name }}",
            "SOURCE_EVENT: ${{ github.event.workflow_run.event }}",
            "SOURCE_CONCLUSION: ${{ github.event.workflow_run.conclusion }}",
            "SOURCE_HEAD_SHA: ${{ github.event.workflow_run.head_sha }}",
            "SOURCE_WORKFLOW_PATH: ${{ github.event.workflow_run.path }}",
            '[ "$SOURCE_REPOSITORY" = "$GITHUB_REPOSITORY" ]',
            '[ "$SOURCE_HEAD_REPOSITORY" = "$GITHUB_REPOSITORY" ]',
            "push|workflow_dispatch)",
            '[ "$SOURCE_CONCLUSION" = success ]',
            '[ "$SOURCE_WORKFLOW_PATH" = .github/workflows/build.yml ]',
            '[[ "$SOURCE_HEAD_SHA" =~ ^[0-9a-f]{40}$ ]]',
            'gh api "repos/$GITHUB_REPOSITORY/commits/$SOURCE_HEAD_SHA" --jq .sha',
            "ref: ${{ github.sha }}",
        )
        for marker in markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, prune)
        self.assertNotIn("actions/download-artifact", prune)

    def test_manifest_issue_write_is_scoped_to_the_watchdog_job(self) -> None:
        manifest = (ROOT / ".github/workflows/manifest-health.yml").read_text(
            encoding="utf-8"
        )
        workflow_header, jobs = manifest.split("\njobs:\n", 1)
        self.assertNotIn("issues: write", workflow_header)
        job_header = jobs.split("\n    steps:\n", 1)[0]
        self.assertIn("permissions:\n      contents: read", job_header)
        self.assertIn("issues: write", job_header)

    def test_ephemeral_signer_bridge_is_bound_to_one_private_run(self) -> None:
        bridge = (ROOT / ".github/workflows/ephemeral-signer-bridge.yml").read_text(
            encoding="utf-8"
        )
        markers = (
            "private_run_id:",
            "private_run_attempt:",
            "PRIVATE_RUN_ID: ${{ inputs.private_run_id }}",
            "PRIVATE_RUN_ATTEMPT: ${{ inputs.private_run_attempt }}",
            '[[ "$PRIVATE_RUN_ID" =~ ^[1-9][0-9]{0,19}$ ]]',
            '[[ "$PRIVATE_RUN_ATTEMPT" =~ ^[1-9][0-9]{0,5}$ ]]',
            'runner_label="yuelink-signer-${PRIVATE_RUN_ID}-${PRIVATE_RUN_ATTEMPT}"',
            "--unattended --ephemeral --disableupdate",
            "--no-default-labels",
            '--labels "$runner_label"',
            "trap cleanup EXIT",
            'find -P "$runner_dir" -mindepth 1 -delete',
            "timeout --signal=TERM --kill-after=30s 4200 ./run.sh",
        )
        for marker in markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, bridge)
        self.assertIn("https://github.com/onesyue/yuelink", bridge)
        self.assertNotIn("actions/checkout", bridge)


if __name__ == "__main__":
    unittest.main()
