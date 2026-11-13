"""Live release-workflow contract for the public builder.

These assertions used to read the disabled private copies. The subjects are
the workflows that actually run. A missing file fails; it is not a skip.
"""

from __future__ import annotations

import re
import subprocess
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / ".github/workflows/build.yml"
ATTESTATION = ROOT / ".github/workflows/source-attestation.yml"
INSTALLER = re.compile(
    r"onesyue/yuelink-ci/\.github/actions/setup-flutter@[0-9a-f]{40}"
)

_JOB_HEAD = re.compile(r"(?m)^(?=  [A-Za-z0-9_-]+:\s*$)")


def build_job_needs(workflow: str) -> list[str]:
    for job in _JOB_HEAD.split(workflow):
        if not job.startswith("  build:\n"):
            continue
        inline = re.search(r"(?m)^    needs:\s*\[([^\]]*)\]\s*$", job)
        if inline:
            return [
                item.strip().strip("'\"")
                for item in inline.group(1).split(",")
                if item.strip()
            ]
        return []
    return []


class PublicReleaseWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        from scripts.check_public_build import composed_text

        self.build = composed_text(BUILD)
        self.attestation = ATTESTATION.read_text(encoding="utf-8")

    def test_release_markers_remain_on_the_public_workflow(self) -> None:
        required = [
            "name: Assemble R2 candidate",
            "Publish to R2 (yuetong.app CDN origin)",
            "Record exact sideload identity",
            "ATTESTED_SOURCE_COMMIT: ${{ inputs.source_commit }}",
            '[ "$source_commit" = "$ATTESTED_SOURCE_COMMIT" ]',
            "Validate current-tag sideload artifacts",
            "needs.validate.result == 'success'",
            "stable manifest must contain all 9 platform artifacts",
            "repository: onesyue/yuelink-ci",
            'bash .public-builder/sync-build.sh --check "$GITHUB_WORKSPACE"',
            'APP_VERSION="${GITHUB_REF_NAME#v}"',
            "RELEASE-STATUS-${status_key}",
            "A partial checksum aggregate would also become stale",
            "name: Sign root manifest and promote (in-pipeline)",
            "if: env.SKIP_MANIFEST != '1' && inputs.tag_kind == 'stable'",
            'bash scripts/ci/promote_in_pipeline.sh "$APP_VERSION"',
            "root manifest was NOT promoted",
            "SOURCE_ATTESTATION_RUN_ID: ${{ inputs.attestation_run_id }}",
            "BUILDER_COMMIT: ${{ github.workflow_sha }}",
            "BUILD_RUN_ID: ${{ github.run_id }}",
            "CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_R2_CONFIG_TOKEN }}",
            "Candidate SHA-256:",
            "dart run tool/windows_durability_probe.dart",
            "YUELINK_FLUTTER_INSTALLER:",
            "value: ${{ jobs.source_binding.outputs.source_commit }}",
            "value: ${{ jobs.preflight.outputs.tag_kind }}",
            "source_commit: ${{ needs.plan.outputs.source_commit }}",
            "tag_kind: ${{ needs.validate.outputs.tag_kind }}",
            "secrets: inherit",
        ]
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, self.build)
        self.assertNotIn("OFFICIAL_UNSIGNED_RELEASE", self.build)
        self.assertNotIn("official_unsigned_release", self.build)
        self.assertIsNone(
            re.search(
                r"(?m)(?:^|\s)gh release (?:create|upload|view)(?:\s|$)|uses:\s\S*action-gh-release",
                self.build,
            )
        )
        identity = self.build.split("      - name: Record exact sideload identity", 1)[1]
        identity = identity.split("      - name: Attest build provenance", 1)[0]
        self.assertNotIn("git rev-list", identity)
        android = self.build.split("      # ANDROID", 1)[1].split("      # iOS —", 1)[0]
        self.assertIn("Verify APK signature is the pinned release key", android)

    def test_dropping_validate_from_build_needs_is_rejected(self) -> None:
        from scripts.check_public_build import composed_workflow, issues

        composed = composed_workflow(BUILD)
        needs = build_job_needs(composed)
        self.assertIn("validate", needs)
        self.assertEqual(issues(composed), [])
        mutated = composed.replace(
            "needs: [plan, validate]",
            "needs: [plan]",
            1,
        )
        self.assertNotEqual(mutated, composed)
        self.assertNotIn("validate", build_job_needs(mutated))
        self.assertTrue(issues(mutated))

    def test_dial_platforms_arrive_from_the_called_workflow(self) -> None:
        from scripts.check_public_build import composed_workflow, issues

        caller = BUILD.read_text(encoding="utf-8")
        composed = composed_workflow(BUILD)
        self.assertEqual(issues(composed), [])
        self.assertNotIn("\n          - name: android\n", caller)
        self.assertIn("\n          - name: android\n", composed)
        mutated = composed.replace(
            "\n          - name: android\n",
            "\n          - name: android-removed\n",
            1,
        )
        self.assertTrue(any("lost android" in item for item in issues(mutated)))

    def test_orchestrator_alone_is_not_a_pass(self) -> None:
        from scripts.check_public_build import issues

        self.assertTrue(issues(BUILD.read_text(encoding="utf-8")))

    def test_flutter_builds_keep_isolated_symbols(self) -> None:
        commands = re.findall(
            r"^\s*flutter build [^\n]+", self.build.replace("\\\n", " "), re.M
        )
        self.assertEqual(len(commands), 6)
        directories = []
        for command in commands:
            self.assertIn("--obfuscate", command)
            match = re.search(r'--split-debug-info="([^"]+)"', command)
            self.assertIsNotNone(match)
            directories.append(match.group(1))
        self.assertEqual(len(set(directories)), 6)
        retention = self.build.split("- name: Encrypt and retain Flutter split symbols", 1)[1]
        retention = retention.split("- name: Attest build provenance", 1)[0]
        self.assertIn("set -euo pipefail", retention)
        self.assertNotIn("continue-on-error", retention)
        self.assertIn('cmp "$file" "$file.readback"', retention)
        self.assertIn("scripts/ci/archive_flutter_symbols.py archive", retention)

    def test_govulncheck_scans_each_module_on_the_live_attestation_workflow(self) -> None:
        self.assertIn("module: [core, service]", self.attestation)
        self.assertIn("MODULE: ${{ matrix.module }}", self.attestation)
        self.assertIn(
            'run: bash scripts/ci/govulncheck_targets.sh "$MODULE"',
            self.attestation,
        )

    def test_flutter_installer_env_matches_the_uses_pin(self) -> None:
        for label, text in (("build", self.build), ("source-attestation.yml", self.attestation)):
            uses = set(INSTALLER.findall(text))
            envs = set(
                re.findall(r"(?m)^[ ]*YUELINK_FLUTTER_INSTALLER:\s*(\S+)\s*$", text)
            )
            with self.subTest(path=label):
                self.assertEqual(uses, envs)
                self.assertEqual(len(uses), 1)

    def test_public_self_check_rejects_a_private_checkout_without_the_deploy_key(self) -> None:
        from scripts.check_public_build import composed_workflow, issues

        composed = composed_workflow(BUILD)
        self.assertEqual(issues(composed), [])
        mutated = composed.replace(
            "ssh-key: ${{ secrets.SRC_DEPLOY_KEY }}\n", "", 1
        )
        self.assertNotEqual(mutated, composed)
        self.assertTrue(issues(mutated))
        self.assertTrue(issues(""))

    def test_android_sdk_packages_must_stay_platform_tools(self) -> None:
        from scripts.check_public_build import composed_workflow, issues

        composed = composed_workflow(BUILD)
        self.assertEqual(issues(composed), [])
        mutated = composed.replace(
            "packages: platform-tools",
            "packages: tools platform-tools",
            1,
        )
        self.assertNotEqual(mutated, composed)
        self.assertTrue(
            any("platform-tools" in item for item in issues(mutated))
        )
    def test_called_workflow_checkouts_hold_the_private_floor(self) -> None:
        from scripts.check_public_build import composed_workflow, issues

        caller = BUILD.read_text(encoding="utf-8")
        composed = composed_workflow(BUILD)
        self.assertTrue(composed.startswith(caller))
        called = composed[len(caller) :]
        self.assertIn("repository: onesyue/yuelink", called)
        self.assertEqual(issues(composed), [])
        stripped = caller + called.replace(
            "repository: onesyue/yuelink", "repository: example.invalid"
        )
        self.assertTrue(
            any("fell below the floor" in item for item in issues(stripped))
        )

    def test_a_missing_local_workflow_is_not_a_smaller_success(self) -> None:
        import tempfile

        from scripts.check_public_build import composed_workflow

        with tempfile.TemporaryDirectory() as directory:
            workflows = Path(directory) / ".github" / "workflows"
            workflows.mkdir(parents=True)
            entry = workflows / "build.yml"
            entry.write_text(
                "uses: ./.github/workflows/missing-called.yml\n",
                encoding="utf-8",
            )
            with self.assertRaises(FileNotFoundError):
                composed_workflow(entry)
            called = workflows / "called.yml"
            called.write_text("called-only-marker\n", encoding="utf-8")
            entry.write_text(
                "uses: ./.github/workflows/called.yml\n", encoding="utf-8"
            )
            text = composed_workflow(entry)
            self.assertIn("called-only-marker", text)

class PublicWorkflowShellObservationTests(unittest.TestCase):
    def run_shell(self, script: str, data: str, *, status: int = 0) -> subprocess.CompletedProcess[str]:
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "output"
            fixture.write_text(data)
            env = dict(os.environ, FIXTURE=str(fixture), PRODUCER_STATUS=str(status))
            return subprocess.run(
                ["bash", "-euo", "pipefail", "-c", script],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

    def test_apk_signer_first_hash_drains_full_output_and_keeps_failures(self) -> None:
        from scripts.check_public_build import composed_text

        source = composed_text(BUILD)
        assignments = re.findall(r'(?ms)^            got=\$\("\$APKSIGNER".*?\)\n', source)
        self.assertEqual(len(assignments), 1)
        script = (
            'APKSIGNER=signer\napk=fixture.apk\n'
            'signer() { cat "$FIXTURE" || return $?; return "$PRODUCER_STATUS"; }\n'
            + textwrap.dedent(assignments[0])
            + 'printf "%s\\n" "$got"\n'
        )
        data = "Signer #1 certificate SHA-256 digest: ABCD\n" + (
            "Signer #2 certificate SHA-256 digest: EEEE\n" * 65536
        )
        result = self.run_shell(script, data)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "abcd\n")
        self.assertNotEqual(self.run_shell(script, data, status=23).returncode, 0)

    def test_bundle_first_match_consumes_listing(self) -> None:
        from scripts.check_public_build import composed_text

        matches = re.findall(
            r"(?m)^          BUNDLE_DIR=\$\(find build/linux .*\)$",
            composed_text(BUILD),
        )
        self.assertEqual(len(matches), 1)
        script = (
            'find() { cat "$FIXTURE" || return $?; return "$PRODUCER_STATUS"; }\n'
            + matches[0].strip()
            + '\nprintf "%s\\n" "$BUNDLE_DIR"\n'
        )
        data = "build/linux/first/bundle\n" + ("build/linux/other/bundle\n" * 65536)
        result = self.run_shell(script, data)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "build/linux/first/bundle\n")
        self.assertNotEqual(self.run_shell(script, data, status=23).returncode, 0)


if __name__ == "__main__":
    unittest.main()
