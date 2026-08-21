from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/source-attestation.yml"
RELEASE = ROOT / "release.sh"
SOURCE_VERIFIER = ROOT / "scripts/verify-source-attestation.sh"
README = ROOT / "README.md"
DEPENDABOT = ROOT / ".github/dependabot.yml"

EXPECTED_ACTION_YAML = {
    ".github/actions/setup-flutter/action.yml",
    ".github/workflows/build.yml",
    ".github/workflows/ephemeral-signer-bridge.yml",
    ".github/workflows/manifest-health.yml",
    ".github/workflows/policy-ci.yml",
    ".github/workflows/prune-r2.yml",
    ".github/workflows/source-attestation.yml",
}
EXPECTED_EXTERNAL_ACTION_REPOS = {
    "android-actions/setup-android",
    "onesyue/yuelink-ci",
}

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
    '[[ ! "$TAG" =~ ^v[0-9]+\\.[0-9]+\\.[0-9]+$ ]]',
    "prerelease 发布链已停用",
    'repos/onesyue/yuelink/git/ref/tags/$TAG',
    'repos/onesyue/yuelink/git/tags/$PRIVATE_TAG_OBJECT_SHA',
    '.verification.verified == true and .verification.reason == "valid"',
    "actions/workflows/source-attestation.yml/runs?event=workflow_dispatch&status=success",
    '.display_title == \\"$ATTESTATION_TITLE\\"',
    '.head_sha == \\"$BUILDER_COMMIT\\"',
    '.conclusion == \\"success\\"',
    "bash scripts/verify-source-attestation.sh",
    'git verify-commit "$BUILDER_COMMIT"',
    '.commit.verification.verified == true',
    'remote_public_tag_state() {',
    'inspect_remote_public_tag() {',
    'wait_for_verified_remote_public_tag() {',
    '[ "$remote_state" = exists ]',
    'if ! git push origin "refs/tags/$TAG:refs/tags/$TAG"; then',
    'remote_state="$(remote_public_tag_state)" || exit 1',
    'push 返回失败；重新读取远端真相',
    '60 秒内未确认 verified/valid',
    '绝不删除、覆盖或重打该 tag',
    "PUBLIC_TAG_MESSAGE=\"$(printf",
    "ATTESTED_SOURCE_COMMIT=%s\\nSOURCE_ATTESTATION_RUN_ID=%s",
    'git tag -s -m "$PUBLIC_TAG_MESSAGE" "$TAG" "$BUILDER_COMMIT"',
    '.message == $message',
    'git verify-tag "$TAG"',
    "inspect_local_public_tag() {",
    'git cat-file tag "$TAG"',
    'signed_payload.count(begin) == 1',
    'signed_payload.count(end) == 1',
    'signed_payload.endswith(end)',
    "hashlib.sha256(annotation).hexdigest()",
    'printf \'%s\\n\' "$PUBLIC_TAG_MESSAGE"',
    '[ "$actual_annotation_sha" = "$expected_annotation_sha" ]',
    "  inspect_local_public_tag\n",
)

SOURCE_VERIFIER_MARKERS = (
    '[[ "$TAG" =~ ^v[0-9]+\\.[0-9]+\\.[0-9]+$ ]]',
    '"repos/$REPOSITORY/actions/runs/$RUN_ID"',
    "(.id | tostring) == $run",
    '"repos/$REPOSITORY/actions/workflows/source-attestation.yml"',
    '.name == $expected_name',
    '.name == "Source attestation"',
    '.state == "active"',
    '((.workflow_id | tostring) == $workflow_id)',
    '.name == ("Source attestation " + $source)',
    '.display_title == ("Source attestation " + $source)',
    ".path == $workflow and .event == \"workflow_dispatch\"",
    '.status == "completed" and .conclusion == "success"',
    ".head_sha == $builder and .head_branch == \"master\"",
    ".repository.full_name == $repository",
    "RUN_ATTEMPT=",
    'gh run download "$RUN_ID"',
    '--name "yuelink-source-attestation-$SOURCE_COMMIT"',
    "artifact must contain exactly one regular proof file",
    '(keys | sort) == ([',
    '.sourceSha == $source',
    '.releaseTag == $release',
    '.workflowSha == $builder and .runId == $run and .runAttempt == $attempt',
    "(.gates | sort) == ([",
    'gh attestation verify "$proof"',
    '--repo "$REPOSITORY"',
    '--signer-workflow "$WORKFLOW_IDENTITY"',
    "--source-ref refs/heads/master",
    '--source-digest "$BUILDER_COMMIT"',
    '--signer-digest "$BUILDER_COMMIT"',
    "--deny-self-hosted-runners",
)

README_MARKERS = (
    "source_sha=\"$SOURCE_SHA\"",
    "reviewed floor of 2044",
    "local test output is never accepted",
    "GitHub artifact attestation",
    "2026-08-21",
    "allowed_actions=selected",
    "sha_pinning_required=true",
    "github_owned_allowed=true",
    "verified_allowed=false",
    "android-actions/setup-android@*",
    "onesyue/yuelink-ci@*",
    "existing name succeeds",
    "uncertain push is followed by a fresh",
    "never deletes, overwrites or re-tags",
)


def actions_policy_issues(automation: dict[str, str], readme: str) -> list[str]:
    issues: list[str] = []
    if set(automation) != EXPECTED_ACTION_YAML:
        issues.append(
            "public automation inventory must remain six workflows plus one composite action"
        )

    repositories: set[str] = set()
    action_count = 0
    for path, source in automation.items():
        actions = re.findall(r"(?m)^\s*(?:-\s*)?uses:\s*([^\s#]+)", source)
        for action in actions:
            action_count += 1
            if action.startswith("./"):
                issues.append(f"{path}: local/dynamic action is outside the policy")
                continue
            action_path, separator, ref = action.rpartition("@")
            if not separator or not re.fullmatch(r"[0-9a-f]{40}", ref):
                issues.append(f"{path}: mutable or malformed action ref: {action}")
                continue
            parts = action_path.split("/")
            if len(parts) < 2:
                issues.append(f"{path}: malformed action repository: {action}")
                continue
            repositories.add("/".join(parts[:2]))
    if action_count == 0:
        issues.append("public action inventory is unexpectedly empty")

    external = {
        repository
        for repository in repositories
        if repository.split("/", 1)[0] not in {"actions", "github"}
    }
    if external != EXPECTED_EXTERNAL_ACTION_REPOS:
        issues.append("external action repositories do not match the live closed set")

    policy_start = readme.find("following exact closed set")
    policy_block = None
    if policy_start >= 0:
        match = re.search(r"```\n(?P<body>.*?)\n```", readme[policy_start:], re.S)
        if match is not None:
            policy_block = {
                line for line in match.group("body").splitlines() if line
            }
    expected_patterns = {
        f"{repository}@*" for repository in EXPECTED_EXTERNAL_ACTION_REPOS
    }
    if policy_block != expected_patterns:
        issues.append("README selected-actions patterns are not the exact live closure")
    return issues


def source_flutter_issues(workflow: str) -> list[str]:
    issues: list[str] = []
    pin = (
        "onesyue/yuelink-ci/.github/actions/setup-flutter@"
        "6285433f149d03fc8a274736fd854a9b0cd919b3"
    )
    starts = [match.start() for match in re.finditer(re.escape(pin), workflow)]
    if len(starts) != 3:
        issues.append("source attestation must have exactly three installer usages")
    for index, start in enumerate(starts):
        end = workflow.find("\n      - ", start)
        if end < 0:
            end = len(workflow)
        block = workflow[start:end]
        if block.count("cache: false") != 1:
            issues.append(f"source installer usage {index + 1} must disable cache")
        if "cache: true" in block or "channel:" in block:
            issues.append(f"source installer usage {index + 1} has legacy inputs")
    return issues


def contract_issues(
    workflow: str,
    release: str,
    readme: str,
    verifier: str | None = None,
) -> list[str]:
    issues: list[str] = []
    verifier = SOURCE_VERIFIER.read_text(encoding="utf-8") if verifier is None else verifier

    for marker in WORKFLOW_MARKERS:
        if marker not in workflow:
            issues.append(f"source workflow missing {marker!r}")
    for marker in RELEASE_MARKERS:
        if marker not in release:
            issues.append(f"release gate missing {marker!r}")
    for marker in SOURCE_VERIFIER_MARKERS:
        if marker not in verifier:
            issues.append(f"shared source verifier missing {marker!r}")
    for marker in README_MARKERS:
        if marker not in readme:
            issues.append(f"policy missing {marker!r}")

    for marker, expected_count in (
        ('.verification.verified == true and .verification.reason == "valid"', 2),
        ('[ "$remote_state" = exists ]', 2),
        ('git verify-tag "$TAG"', 1),
    ):
        if release.count(marker) != expected_count:
            issues.append(
                f"release gate must contain exactly {expected_count} occurrences of {marker!r}"
            )
    issues.extend(source_flutter_issues(workflow))

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

    source_gate = release.find("bash scripts/verify-source-attestation.sh")
    tag_creation = release.find('git tag -s -m "$PUBLIC_TAG_MESSAGE"')
    if source_gate < 0 or tag_creation < 0 or source_gate > tag_creation:
        issues.append("proven source gate must run before public tag creation")
    if "git tag \"$TAG\"" in release:
        issues.append("public release tag must not be lightweight")

    creation = release.find('git tag -s -m "$PUBLIC_TAG_MESSAGE"')
    local_inspection = release.find("  inspect_local_public_tag\n", creation)
    push = release.find(
        'if ! git push origin "refs/tags/$TAG:refs/tags/$TAG"; then', creation
    )
    if min(creation, local_inspection, push) < 0 or not (
        creation < local_inspection < push
    ):
        issues.append("every public push must follow exact local tag message inspection")

    inspect_start = release.find("inspect_remote_public_tag() {")
    inspect_end = release.find("wait_for_verified_remote_public_tag() {")
    inspect_block = release[inspect_start:inspect_end]
    for marker in (
        '[ "$ref_type" = tag ]',
        '.object.type == "commit" and .object.sha == $builder',
        '.verification.verified == true and .verification.reason == "valid"',
    ):
        if marker not in inspect_block:
            issues.append(f"remote public tag inspection missing {marker!r}")

    if release.count("wait_for_verified_remote_public_tag") != 3:
        issues.append(
            "remote public tag must be verified for both existing and pushed paths"
        )
    push = release.find(
        'if ! git push origin "refs/tags/$TAG:refs/tags/$TAG"; then'
    )
    push_recheck = release.find(
        'remote_state="$(remote_public_tag_state)" || exit 1', push
    )
    post_push_verify = release.find("  wait_for_verified_remote_public_tag", push)
    if min(push, push_recheck, post_push_verify) < 0 or not (
        push < push_recheck < post_push_verify
    ):
        issues.append("failed/uncertain push must re-read and verify remote truth")
    return issues


class SourceAttestationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = WORKFLOW.read_text(encoding="utf-8")
        self.release = RELEASE.read_text(encoding="utf-8")
        self.verifier = SOURCE_VERIFIER.read_text(encoding="utf-8")
        self.readme = README.read_text(encoding="utf-8")
        automation_paths = {
            *ROOT.glob(".github/workflows/*.yml"),
            *ROOT.glob(".github/workflows/*.yaml"),
            *ROOT.glob(".github/actions/**/action.yml"),
            *ROOT.glob(".github/actions/**/action.yaml"),
        }
        self.automation = {
            path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(automation_paths)
        }

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

    def test_each_shared_source_verifier_gate_is_mutation_sensitive(self) -> None:
        for marker in SOURCE_VERIFIER_MARKERS:
            with self.subTest(marker=marker):
                mutated = self.verifier.replace(marker, "", 1)
                self.assertNotEqual(mutated, self.verifier)
                self.assertTrue(
                    contract_issues(
                        self.workflow,
                        self.release,
                        self.readme,
                        verifier=mutated,
                    )
                )

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

    def test_lightweight_public_tag_mutation_is_rejected(self) -> None:
        mutated = self.release.replace(
            'git tag -s -m "$PUBLIC_TAG_MESSAGE" "$TAG" "$BUILDER_COMMIT"',
            'git tag "$TAG"',
            1,
        )
        issues = contract_issues(self.workflow, mutated, self.readme)
        self.assertTrue(any("must not be lightweight" in issue for issue in issues))

    def test_remote_tag_builder_drift_is_rejected(self) -> None:
        mutated = self.release.replace(
            '.object.type == "commit" and .object.sha == $builder',
            '.object.type == "commit"',
            1,
        )
        issues = contract_issues(self.workflow, mutated, self.readme)
        self.assertTrue(any("inspection missing" in issue for issue in issues))

    def test_remote_tag_verification_drift_is_rejected(self) -> None:
        inspect = self.release.index("inspect_remote_public_tag() {")
        marker = (
            '.verification.verified == true and '
            '.verification.reason == "valid"'
        )
        position = self.release.index(marker, inspect)
        mutated = self.release[:position] + "true" + self.release[position + len(marker) :]
        issues = contract_issues(self.workflow, mutated, self.readme)
        self.assertTrue(any("inspection missing" in issue for issue in issues))

    def test_push_failure_remote_recheck_is_mandatory(self) -> None:
        mutated = self.release.replace(
            '    remote_state="$(remote_public_tag_state)" || exit 1\n',
            "",
            1,
        )
        issues = contract_issues(self.workflow, mutated, self.readme)
        self.assertTrue(any("re-read and verify" in issue for issue in issues))

    def test_post_push_github_verification_is_mandatory(self) -> None:
        position = self.release.rfind("  wait_for_verified_remote_public_tag\n")
        self.assertGreater(position, 0)
        mutated = (
            self.release[:position]
            + self.release[position + len("  wait_for_verified_remote_public_tag\n") :]
        )
        issues = contract_issues(self.workflow, mutated, self.readme)
        self.assertTrue(any("both existing and pushed" in issue for issue in issues))

    def test_every_public_workflow_action_is_commit_pinned(self) -> None:
        self.assertEqual(actions_policy_issues(self.automation, self.readme), [])

    def test_each_source_attestation_flutter_cache_mutation_is_rejected(self) -> None:
        pin = (
            "onesyue/yuelink-ci/.github/actions/setup-flutter@"
            "6285433f149d03fc8a274736fd854a9b0cd919b3"
        )
        starts = [
            match.start() for match in re.finditer(re.escape(pin), self.workflow)
        ]
        self.assertEqual(len(starts), 3)
        for index, start in enumerate(starts):
            cache = self.workflow.index("cache: false", start)
            with self.subTest(usage=index + 1):
                mutated = (
                    self.workflow[:cache]
                    + "cache: true"
                    + self.workflow[cache + len("cache: false") :]
                )
                self.assertTrue(source_flutter_issues(mutated))

    def test_actions_policy_rejects_unknown_external_repository(self) -> None:
        automation = dict(self.automation)
        automation[".github/workflows/policy-ci.yml"] += (
            "\n      - uses: unknown-owner/unknown-action@" + "a" * 40 + "\n"
        )
        self.assertTrue(actions_policy_issues(automation, self.readme))

    def test_actions_policy_rejects_unused_documented_pattern(self) -> None:
        mutated = self.readme.replace(
            "android-actions/setup-android@*\n",
            "android-actions/setup-android@*\nunused/example@*\n",
            1,
        )
        self.assertTrue(actions_policy_issues(self.automation, mutated))

    def test_actions_policy_rejects_automation_inventory_drift(self) -> None:
        automation = dict(self.automation)
        automation.pop(".github/workflows/ephemeral-signer-bridge.yml")
        self.assertTrue(actions_policy_issues(automation, self.readme))

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
