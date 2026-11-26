import json
import tempfile
import unittest
from pathlib import Path

from check_flag_manifest import check


WORKFLOW = """\
# ${{ vars.COMMENT_ONLY }}
env:
  SERVICE_E2E: '1'
jobs:
  build:
    strategy:
      matrix:
        os: [linux]
    steps:
      - env: {UPDATE_FORCE_PLATFORMS: "${{ vars.UPDATE_FORCE_PLATFORMS }}"}
        run: |
          if [ "${SKIP_MANIFEST:-0}" != "1" ]; then
            echo "SKIP_MANIFEST=1" >> "$GITHUB_ENV"
          fi
          cat <<'XCCONFIG'
          SWIFT_ENABLE_PREFIX_MAPPING = YES
          XCCONFIG
"""

SCRIPT = """\
import argparse
import os
from typing import Final

FORCE_PLATFORMS_DEFAULT: Final = ""
parser = argparse.ArgumentParser()
parser.add_argument(
    "--platforms",
    default=os.environ.get("UPDATE_FORCE_PLATFORMS", FORCE_PLATFORMS_DEFAULT),
)
"""


class FlagManifestTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.workflow = self.root / ".github/workflows/build.yml"
        self.script = self.root / "rollout.py"
        self.workflow.parent.mkdir(parents=True)
        self.workflow.write_text(WORKFLOW)
        self.script.write_text(SCRIPT)
        (self.root / "policy").mkdir()
        self.manifest = {
            "workflows": {
                "build.yml": {
                    "env": {"SERVICE_E2E": "1"},
                    "xcconfig": {"SWIFT_ENABLE_PREFIX_MAPPING": "YES"},
                    "shell_defaults": {"SKIP_MANIFEST": "0"},
                }
            },
            "variables": {
                "UPDATE_FORCE_PLATFORMS": {
                    "env_workflows": ["build.yml"],
                    "workflow_default": "",
                    "script": "rollout.py",
                    "constant": "FORCE_PLATFORMS_DEFAULT",
                    "script_default": "",
                }
            },
        }
        self.save_manifest()

    def save_manifest(self):
        (self.root / "policy/flags.json").write_text(json.dumps(self.manifest))

    def test_reviewed_defaults_pass(self):
        self.assertEqual(check(self.root), [])

    def test_changed_or_missing_workflow_settings_fail(self):
        cases = [
            ("SERVICE_E2E: '1'", "SERVICE_E2E: '2'", "env SERVICE_E2E"),
            ("SERVICE_E2E: '1'", "ORDINARY: text", "reviewed setting missing"),
            ("SWIFT_ENABLE_PREFIX_MAPPING = YES", "SWIFT_ENABLE_PREFIX_MAPPING = NO", "xcconfig"),
            ("SKIP_MANIFEST:-0", "SKIP_MANIFEST:-1", "shell_defaults"),
            ("vars.UPDATE_FORCE_PLATFORMS", "vars.UPDATE_FORCE_PLATFORMS || 'linux'", "var_env"),
            ("${{ vars.UPDATE_FORCE_PLATFORMS }}", "linux", "var_env"),
        ]
        for before, after, diagnostic in cases:
            with self.subTest(after=after):
                self.workflow.write_text(WORKFLOW.replace(before, after))
                self.assertIn(diagnostic, "\n".join(check(self.root)))

    def test_new_switches_in_other_workflows_fail(self):
        other = self.workflow.with_name("other.yml")
        other.write_text("env: {TRACE: false, BUILD_MODE: release, RETRIES: 4}\n")
        self.assertEqual(set(check(self.root)), {
            "other.yml env TRACE: missing manifest row",
            "other.yml env BUILD_MODE: missing manifest row",
        })

    def test_unregistered_repo_variable_outside_env_fails(self):
        self.workflow.write_text(WORKFLOW.replace("[linux]", '["${{ vars.NEW_SWITCH }}"]'))
        self.assertEqual(check(self.root), ["build.yml vars.NEW_SWITCH: missing manifest row"])

    def test_each_occurrence_is_checked(self):
        self.workflow.write_text(WORKFLOW + "      - env: {SERVICE_E2E: '0'}\n")
        self.assertIn("build.yml env SERVICE_E2E: differs from the reviewed value", check(self.root))

    def test_script_literal_and_live_argument_binding_are_checked(self):
        cases = [
            ('Final = ""', 'Final = "linux"', "differs from the reviewed default"),
            ("FORCE_PLATFORMS_DEFAULT)", '"linux")', "argument default must use"),
            ("default=os.environ.get", 'default="linux" or os.environ.get', "argument default must use"),
        ]
        for before, after, diagnostic in cases:
            with self.subTest(after=after):
                self.script.write_text(SCRIPT.replace(before, after))
                self.assertIn(diagnostic, "\n".join(check(self.root)))

    def test_matching_reviewed_change_passes(self):
        workflow = WORKFLOW.replace("SERVICE_E2E: '1'", "SERVICE_E2E: '0'")
        workflow = workflow.replace("vars.UPDATE_FORCE_PLATFORMS", "vars.UPDATE_FORCE_PLATFORMS || 'linux'")
        self.workflow.write_text(workflow)
        self.script.write_text(SCRIPT.replace('Final = ""', 'Final = "linux"'))
        self.manifest["workflows"]["build.yml"]["env"]["SERVICE_E2E"] = "0"
        variable = self.manifest["variables"]["UPDATE_FORCE_PLATFORMS"]
        variable.update(workflow_default="linux", script_default="linux")
        self.save_manifest()
        self.assertEqual(check(self.root), [])

    def test_missing_workflow_directory_fails_closed(self):
        self.workflow.unlink()
        self.workflow.parent.rmdir()
        self.assertIn("no workflow YAML files found", check(self.root))


if __name__ == "__main__":
    unittest.main()
