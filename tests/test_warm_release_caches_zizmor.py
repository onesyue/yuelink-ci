"""Regression checks for the release cache warmer's toolchains and trust boundary."""

import re
import unittest
from pathlib import Path

from scripts import release_cache_plan as plan


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/warm-release-caches.yml"
XCODE_PATH = "format('/Applications/Xcode_{0}.app/Contents/Developer', needs.plan.outputs.xcode)"


class WarmReleaseCachesZizmorTest(unittest.TestCase):
    def setUp(self):
        self.workflow = WORKFLOW.read_text(encoding="utf-8")
        self.steps = dict(plan.steps(self.workflow))
        self.build_core = self.steps["Build exact native core and service inputs"]

    def test_workflow_does_not_append_to_github_env(self):
        self.assertNotRegex(self.workflow, r'''>>\s*["']?\$(?:GITHUB_ENV\b|\{GITHUB_ENV\})''')

    def test_apple_core_receives_checked_xcode_path(self):
        select = self.steps["Select canonical Xcode for Apple core"]
        self.assertEqual(plan.field(select, "if"), "runner.os == 'macOS'")
        self.assertEqual(plan.field(select, "DEVELOPER_DIR"), "${{ " + XCODE_PATH + " }}")
        self.assertIn('test -d "$DEVELOPER_DIR"', plan.field(select, "run").splitlines())
        self.assertEqual(
            plan.field(self.build_core, "DEVELOPER_DIR"),
            "${{ runner.os == 'macOS' && " + XCODE_PATH + " || '' }}",
        )

    def test_android_build_steps_export_both_ndk_variables_before_build_tools(self):
        install = self.steps["Install canonical NDK"]
        self.assertEqual(plan.field(install, "NDK"), "${{ needs.plan.outputs.ndk }}")
        self.assertIn('sdkmanager "ndk;$NDK"', plan.field(install, "run").splitlines())
        for step in (self.build_core, self.steps["Resolve Android dependencies without signing or packaging"]):
            with self.subTest(step=step.splitlines()[0].strip()):
                self.assertEqual(plan.field(step, "NDK"), "${{ needs.plan.outputs.ndk }}")
                run = plan.field(step, "run")
                exports = (
                    'export ANDROID_NDK_HOME="$ANDROID_SDK_ROOT/ndk/$NDK"\n'
                    'export ANDROID_NDK="$ANDROID_NDK_HOME"'
                )
                if step == self.build_core:
                    exports = 'if [ "$PLATFORM" = android ]; then\n' + exports + '\nfi'
                else:
                    self.assertIn("matrix.platform == 'android'", plan.field(step, "if"))
                self.assertIn(exports + '\nflutter pub get --enforce-lockfile', run)

    def test_workflow_run_requires_successful_same_repo_manual_master_attestation(self):
        self.assertRegex(
            self.workflow,
            r"(?m)^on:\n  workflow_run:[^\n]*\n"
            r"    workflows: \[Source attestation\]\n    types: \[completed\]\n    branches: \[master\]\n",
        )
        match = re.search(r"(?m)^  plan:\n    if: >-\n((?:      [^\n]+\n)+)", self.workflow)
        self.assertIsNotNone(match)
        self.assertCountEqual(
            [guard.strip() for guard in match[1].split("&&")],
            [
                "github.event.workflow_run.conclusion == 'success'",
                "github.event.workflow_run.event == 'workflow_dispatch'",
                "github.event.workflow_run.head_branch == 'master'",
                "github.event.workflow_run.head_repository.full_name == github.repository",
            ],
        )
        self.assertIn("\n  warm:\n    if: github.ref == 'refs/heads/master'\n", self.workflow)
        self.assertIn("\n    needs: [plan]\n", self.workflow.split("\n  warm:\n", 1)[1])

    def test_workflow_checks_out_full_source_commit_from_attestation_title(self):
        derive = self.steps["Derive source, platform matrix and toolchains from canonical workflows"]
        self.assertEqual(plan.field(derive, "ATTESTATION_TITLE"), "${{ github.event.workflow_run.display_title }}")
        self.assertIn('python3 scripts/release_cache_plan.py plan --title "$ATTESTATION_TITLE"', plan.field(derive, "run"))
        self.assertIn("\n      source_sha: ${{ steps.plan.outputs.source_sha }}\n", self.workflow)
        checkout = self.steps["Checkout exact attested source"]
        self.assertEqual(plan.field(checkout, "ref"), "${{ needs.plan.outputs.source_sha }}")

        sha = "a" * 40
        self.assertEqual(plan.source_sha("Source attestation " + sha, ""), sha)
        for invalid in (sha[:-1], sha + "a", sha.upper(), sha + " extra"):
            with self.subTest(title=invalid), self.assertRaises(plan.PlanError):
                plan.source_sha("Source attestation " + invalid, "")
