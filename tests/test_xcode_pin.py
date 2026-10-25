import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / ".github/workflows/build.yml"
ATTESTATION = ROOT / ".github/workflows/source-attestation.yml"


class XcodePinTests(unittest.TestCase):
    def run_apple_step(self, workflow: str, *, version="Xcode 26.6", installed=True):
        step = workflow.split("- name: Select Xcode 26.6 for Apple builds\n", 1)[1]
        step = step.split("\n      - name:", 1)[0]
        script = textwrap.dedent(step.split("        run: |\n", 1)[1])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            developer = root / "Xcode_26.6.app/Contents/Developer"
            if installed:
                developer.mkdir(parents=True)
            binary = root / "bin"
            binary.mkdir()
            xcodebuild = binary / "xcodebuild"
            xcodebuild.write_text('#!/bin/sh\nprintf "%s\\n" "$TEST_XCODE_VERSION"\n')
            xcodebuild.chmod(0o755)
            home = root / "home"
            output = root / "github-env"
            env = dict(
                os.environ,
                PATH=str(binary) + os.pathsep + os.environ["PATH"],
                HOME=str(home),
                DEVELOPER_DIR=str(developer),
                GITHUB_WORKSPACE=str(root / "workspace"),
                GITHUB_ENV=str(output),
                TEST_XCODE_VERSION=version,
            )
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            exported = (
                dict(line.split("=", 1) for line in output.read_text().splitlines())
                if output.exists()
                else {}
            )
            expected = {
                "DEVELOPER_DIR": str(developer),
                "COMPILATION_CACHE_ENABLE_CACHING": "YES",
                "COMPILATION_CACHE_PREFIX_MAP": str(root / "workspace") + "=.",
                "COMPILATION_CACHE_CAS_PATH": str(
                    home
                    / "Library/Developer/Xcode/DerivedData/CompilationCache.noindex"
                ),
            }
            cache_created = Path(expected["COMPILATION_CACHE_CAS_PATH"]).is_dir()
            return result, exported, expected, cache_created

    def test_apple_release_build_uses_xcode_26_6(self) -> None:
        workflow = BUILD.read_text(encoding="utf-8")
        assert workflow.count('"os":"macos-26"') == 2
        assert '"os":"macos-latest"' not in workflow
        assert "if: matrix.platform == 'ios' || matrix.platform == 'macos'" in workflow
        assert (
            "DEVELOPER_DIR: /Applications/Xcode_26.6.app/Contents/Developer" in workflow
        )
        assert "xcodebuild -version" in workflow
        assert "Xcode 26.6" in workflow
        result, exported, expected, cache_created = self.run_apple_step(workflow)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(exported, expected)
        self.assertTrue(cache_created)

    def test_apple_release_refuses_missing_or_wrong_xcode_before_export(self) -> None:
        workflow = BUILD.read_text(encoding="utf-8")
        for installed, version in ((False, "Xcode 26.6"), (True, "Xcode 26.5")):
            with self.subTest(installed=installed, version=version):
                result, exported, _expected, cache_created = self.run_apple_step(
                    workflow,
                    installed=installed,
                    version=version,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(exported, {})
                self.assertFalse(cache_created)

    def test_macos_attestation_uses_xcode_26_6(self) -> None:
        workflow = ATTESTATION.read_text(encoding="utf-8")
        assert "runs-on: macos-latest" not in workflow
        assert "runs-on: macos-26" in workflow
        assert (
            "DEVELOPER_DIR: /Applications/Xcode_26.6.app/Contents/Developer" in workflow
        )
        assert "xcodebuild -version" in workflow
        assert "Xcode 26.6" in workflow
        assert 'echo "DEVELOPER_DIR=$DEVELOPER_DIR" >> "$GITHUB_ENV"' in workflow
