"""Execute the Android graph guard and the actual compilation shell boundary."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('android_guard', ROOT / 'scripts/dialgate-android-task-guard.py')
assert SPEC and SPEC.loader
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


def task(path: str, kind: str = 'org.gradle.api.DefaultTask') -> dict[str, str]:
    return {'path': path, 'type': kind}


class AndroidCompilationTests(unittest.TestCase):
    def test_required_graph_and_library_jar_dependencies(self) -> None:
        GUARD.validate([task(':app:compileDebugKotlin'), task(':media:bundleLibCompileToJarDebug'),
                        task(':app:processDebugResources'), task(':app:compileFlutterBuildDebug'),
                        task(':media:verifyNativeLibraries')])
        for graph in (None, [], [{}], [task(':app:other')],
                      [task(':app:compileDebugKotlin')] * 2):
            with self.subTest(graph=graph), self.assertRaises(ValueError):
                GUARD.validate(graph)

    def test_only_exact_observed_resource_merge_is_admitted(self) -> None:
        exact = task(':app:packageDebugResources', 'com.android.build.gradle.tasks.MergeResources_Decorated')
        GUARD.validate([task(':app:compileDebugKotlin'), exact])
        for changed in (
            task(exact['path'], 'com.android.build.gradle.tasks.PackageApplication'),
            task(exact['path'], 'com.android.build.gradle.tasks.MergeResources'),
            task(':app:packageReleaseResources', exact['type']),
            task(':app:packageDebug', exact['type']),
            task(':app:bundleDebugResources', exact['type']),
        ):
            with self.subTest(task=changed), self.assertRaises(ValueError):
                GUARD.validate([task(':app:compileDebugKotlin'), changed])

    def test_forbidden_graph_tasks_fail_before_execution(self) -> None:
        for path, kind in (
            (':app:packageDebug', 'PackageApplication'),
            (':app:bundleDebug', 'BundleApplication'),
            (':app:assembleDebug', 'DefaultTask'),
            (':app:validateSigningDebug', 'DefaultTask'),
            (':app:installDebug', 'DefaultTask'),
            (':lib:publish', 'DefaultTask'),
            (':lib:compileReleaseKotlin', 'DefaultTask'),
            (':lib:buildCMakeDebug[arm64-v8a]', 'DefaultTask'),
            (':lib:externalNativeBuildDebug', 'DefaultTask'),
            (':lib:buildNdkBuildDebug', 'DefaultTask'),
            (':lib:custom', 'com.android.build.gradle.tasks.ExternalNativeBuildTask'),
            (':app:custom', 'com.android.build.gradle.tasks.PackageAndroidArtifact'),
        ):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'forbidden'):
                GUARD.validate([task(':app:compileDebugKotlin'), task(path, kind)])

    def test_actual_compile_command_and_failure_receipts(self) -> None:
        workflow = (ROOT / '.github/workflows/dialgate-verify.yml').read_text()
        step = workflow.split('- name: Compile only the admitted debug Kotlin graph\n', 1)[1].split('      - name:', 1)[0]
        script = textwrap.dedent(step.split('run: |\n', 1)[1])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); candidate = root / 'candidate'; android = candidate / 'android'; android.mkdir(parents=True)
            (root / '.android-verifier').symlink_to(ROOT, target_is_directory=True)
            wrapper = android / 'gradlew'
            wrapper.write_text("#!/usr/bin/env python3\nimport os, json, pathlib, sys\n"
                               "pathlib.Path(os.environ['CALLS']).write_text(json.dumps(sys.argv[1:]))\n"
                               "pathlib.Path(os.environ['DIAL_ANDROID_GRAPH']).write_text(os.environ['GRAPH'])\n"
                               "print('> Task :app:compileDebugKotlin' + os.environ['OUTCOME'])\n"
                               "raise SystemExit(int(os.environ['FAKE_EXIT']))\n")
            wrapper.chmod(0o755)
            env = {**os.environ, 'RUNNER_TEMP': str(root), 'GITHUB_WORKSPACE': str(root),
                   'REVIEWED_FLUTTER_ROOT': '/reviewed/flutter', 'ANDROID_HOME': '/reviewed/android',
                   'EXPECTED_CANDIDATE': 'a' * 40, 'BUILDER_COMMIT': 'b' * 40,
                   'CALLS': str(root / 'calls'), 'FAKE_EXIT': '0', 'OUTCOME': '',
                   'GRAPH': json.dumps([task(':app:compileDebugKotlin')])}
            for changes, expected in (({}, 0), ({'FAKE_EXIT': '17'}, 17),
                                      ({'OUTCOME': ' NO-SOURCE'}, 1), ({'OUTCOME': ' FROM-CACHE'}, 1),
                                      ({'OUTCOME': ' SKIPPED'}, 1), ({'OUTCOME': ' UP-TO-DATE'}, 1),
                                      ({'GRAPH': json.dumps([task(':app:packageDebug')])}, 1)):
                with self.subTest(changes=changes):
                    result = subprocess.run(['bash', '-c', script], cwd=candidate, env={**env, **changes}, capture_output=True, text=True)
                    self.assertEqual(result.returncode, expected, result.stderr)
            self.assertEqual(json.loads((root / 'calls').read_text()), [
                '--no-daemon', '--no-configuration-cache', '--no-build-cache', '--max-workers=2', '--console=plain',
                '--init-script', str(root / '.android-verifier/scripts/dialgate-android-compile.init.gradle'),
                ':app:compileDebugKotlin'])
            self.assertEqual((android / 'local.properties').read_text(), 'sdk.dir=/reviewed/android\nflutter.sdk=/reviewed/flutter\n')
            self.assertEqual((root / 'android-compile-identity.txt').read_text(), 'source=' + 'a' * 40 + '\nbuilder=' + 'b' * 40 + '\n')

    def test_workflow_binding_and_init_guard_wiring(self) -> None:
        workflow = (ROOT / '.github/workflows/dialgate-verify.yml').read_text()
        block = workflow.split('  android_kotlin_compile:\n', 1)[1].split('  dial_gate:\n', 1)[0]
        self.assertIn('needs: [dial_source_binding]', block)
        self.assertIn('ref: ${{ needs.dial_source_binding.outputs.candidate_commit }}', block)
        self.assertIn('dialgate-binding.py confirm --checkout .', block)
        self.assertLess(block.index('Confirm Android source identity'), block.index('flutter pub get'))
        self.assertIn('flutter pub get --enforce-lockfile', block)
        self.assertNotIn('flutter build', block)
        self.assertNotIn('environment:', block)
        init = (ROOT / 'scripts/dialgate-android-compile.init.gradle').read_text()
        self.assertIn('gradle.taskGraph.whenReady', init)
        self.assertIn('graph.allTasks.collect', init)
        self.assertIn("new ProcessBuilder('python3', System.getenv('DIAL_ANDROID_GUARD'), receipt.absolutePath)", init)
        self.assertIn('process.waitFor() != 0', init)
        self.assertIn("gradle.startParameter.taskNames != [':app:compileDebugKotlin']", init)


class PublicGoOverlayTests(unittest.TestCase):
    def overlay(self, source: str) -> subprocess.CompletedProcess[str]:
        text = (ROOT / 'sync-build.sh').read_text()
        script = text.split("python3 - \"$GENERATED\" <<'PY'\n", 1)[1].split('# 1) 顶部 banner\n', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'build.yml'; path.write_text(source)
            return subprocess.run(['python3', '-', str(path)], input=script + '\nprint(s, end="")\n', capture_output=True, text=True)

    def test_frozen_old_template_keeps_public_security_upgrade(self) -> None:
        old = "env:\n  GO_VERSION: '1.27.3'\n"
        expected = "env:\n  # 2026-11-11: Go 1.27.4 addresses GO-2026-6859 (net/http MaxBytesReader race).\n  GO_VERSION: '1.27.4'\n"
        result = self.overlay(old)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected)
        self.assertEqual(self.overlay(expected).stdout, expected)

    def test_unknown_missing_and_duplicate_versions_rejected(self) -> None:
        for text in ("env:\n", "  GO_VERSION: '1.28.0'\n", "  GO_VERSION: '1.27.4'\n" * 2,
                     "  GO_VERSION: '1.27.3'\n  GO_VERSION: '1.27.4'\n",
                     "  GO_VERSION: '1.27.4'\n  GO_VERSION: 1.28.0\n"):
            with self.subTest(text=text):
                self.assertNotEqual(self.overlay(text).returncode, 0)


if __name__ == '__main__':
    unittest.main()
