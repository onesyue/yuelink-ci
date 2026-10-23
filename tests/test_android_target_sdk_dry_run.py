"""Fail-closed dry-run contracts; actual Android qualification runs only in CI."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('target_sdk', ROOT / 'scripts/android-target-sdk-dry-run.py')
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def marker(mode):
    return MOD.MARKER + json.dumps({'project': ':app', 'originalTarget': 36,
        'effectiveTarget': 37 if mode == 'target37' else 36, 'mode': mode,
        'runtime': {'gradleVersion': '9.7.1', 'javaMajor': '17', 'maxHeapBytes': 3221225472,
                    'workers': 2, 'parallel': False}}) + '\n'


class ManifestControls(unittest.TestCase):
    def test_real_badging_shape_and_baseline_rejection(self):
        text = "package: name='com.yueto.yuelink' versionCode='1'\nsdkVersion:'24'\ntargetSdkVersion:'36'\n"
        self.assertEqual(MOD.target_from_badging(text, 36), "targetSdkVersion:'36'")
        with self.assertRaisesRegex(MOD.Rejected, '^manifest-target-mismatch$'):
            MOD.target_from_badging(text, 37)

    def test_missing_duplicate_preview_and_malicious_targets_rejected(self):
        for text in ('', "sdkVersion:'37'", "targetSdkVersion:'37'\ntargetSdkVersion:'37'",
                     "targetSdkVersion:'Baklava'", "targetSdkVersion:'37' secret=private"):
            with self.subTest(text=text), self.assertRaises(MOD.Rejected):
                MOD.target_from_badging(text, 37)

    def test_gradle_markers_require_original_target_and_exact_mode(self):
        for mode in ('baseline', 'invalid', 'target37'):
            self.assertEqual(MOD.dsl_marker(marker(mode), mode)['originalTarget'], 36)
        for text in ('', marker('baseline') * 2, marker('target37'),
                     marker('baseline').replace('36', '37'), MOD.MARKER + '{broken}'):
            with self.subTest(text=text), self.assertRaises(MOD.Rejected):
                MOD.dsl_marker(text, 'baseline')

    def test_actual_gradle_runtime_budget_is_enforced(self):
        for replacement in [('9.7.1', '9.6.0'), ('3221225472', '4294967296'), ('\"workers\": 2', '\"workers\": 8')]:
            with self.subTest(replacement=replacement), self.assertRaisesRegex(MOD.Rejected, '^dsl-runtime-budget$'):
                MOD.dsl_marker(marker('baseline').replace(*replacement), 'baseline')

    def test_ambient_override_cannot_contaminate_controls(self):
        base = {MOD.OVERRIDE: '37', 'TMPDIR': '/preserved'}
        self.assertNotIn(MOD.OVERRIDE, MOD.child_env(base, 'baseline'))
        self.assertEqual(MOD.child_env(base, 'invalid')[MOD.OVERRIDE], 'invalid')
        self.assertEqual(MOD.child_env(base, 'target37')[MOD.OVERRIDE], '37')
        self.assertEqual(MOD.child_env(base, 'baseline')['TMPDIR'], '/preserved')
        self.assertEqual(base[MOD.OVERRIDE], '37')
        with self.assertRaises(MOD.Rejected):
            MOD.child_env(base, 'arbitrary')


class RealProcessBoundaries(unittest.TestCase):
    def test_actual_child_environment_and_private_output_not_published(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt = {}
            env = dict(os.environ, **{MOD.OVERRIDE: 'pollution'})
            runner = MOD.Runner(Path(temp), env, receipt)
            public = io.StringIO()
            with contextlib.redirect_stdout(public):
                code, output = runner.command('environment-control', [sys.executable, '-c',
                    "import os; print(os.getenv('YUE_ANDROID_TARGET_SDK_DRY_RUN', 'absent')); print('PRIVATE_SENTINEL')"])
            self.assertEqual(code, 0)
            self.assertEqual(output, 'absent\nPRIVATE_SENTINEL\n')
            self.assertNotIn('PRIVATE_SENTINEL', public.getvalue() + json.dumps(receipt))
            self.assertEqual(receipt['commands'][0]['output_bytes'], len(output))
            self.assertEqual(len(receipt['commands'][0]['output_sha256']), 64)

    def test_failure_and_timeout_do_not_become_negative_control_success(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = MOD.Runner(Path(temp), dict(os.environ), {})
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(MOD.Rejected, '^command-failed$'):
                    runner.command('failure', [sys.executable, '-c', 'raise SystemExit(7)'])
                with self.assertRaisesRegex(MOD.Rejected, '^command-timeout$'):
                    runner.command('timeout', [sys.executable, '-c', 'import time; time.sleep(30)'],
                                   timeout=0.05, allow_failure=True)
            self.assertTrue(runner.receipt['commands'][-1]['timed_out'])

    def test_dirty_tracked_source_and_retained_credentials_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            subprocess.run(['git', 'init', '-q', str(source)], check=True)
            subprocess.run(['git', '-C', temp, 'config', 'user.name', 'Fixture'], check=True)
            subprocess.run(['git', '-C', temp, 'config', 'user.email', 'fixture@example.invalid'], check=True)
            (source / 'tracked').write_text('original')
            subprocess.run(['git', '-C', temp, 'add', 'tracked'], check=True)
            subprocess.run(['git', '-C', temp, '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture'], check=True)
            head = subprocess.check_output(['git', '-C', temp, 'rev-parse', 'HEAD'], text=True).strip()
            subprocess.run(['git', '-C', temp, 'update-ref', 'refs/remotes/origin/master', head], check=True)
            runner = MOD.Runner(source, dict(os.environ), {})
            (source / 'tracked').write_text('changed')
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(MOD.Rejected, '^tracked-source-dirty$'):
                    MOD.bind_source(runner, head)
                (source / 'tracked').write_text('original')
                subprocess.run(['git', '-C', temp, 'config', 'core.sshCommand', 'private-value'], check=True)
                with self.assertRaisesRegex(MOD.Rejected, '^retained-checkout-credentials$'):
                    MOD.bind_source(runner, head)
            self.assertNotIn('private-value', json.dumps(runner.receipt))


class ConsumerSequence(unittest.TestCase):
    """Synthetic tools exercise orchestration failure branches, not Android acceptance."""
    def run_sequence(self, fault=None):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source, work, output = (base / name for name in ('source', 'work', 'evidence'))
            for path in (source, work, output):
                path.mkdir()
            native = source / 'android/app/src/main/jniLibs/arm64-v8a/libclash.so'
            native.parent.mkdir(parents=True)
            native.write_bytes(b'fixture-native')
            apk = source / 'build/app/outputs/flutter-apk/app-debug.apk'
            apk.parent.mkdir(parents=True)
            apk.write_bytes(b'stale-output-must-be-removed')
            calls = []

            class FixtureRunner:
                def __init__(self):
                    self.source, self.env, self.receipt = source, {MOD.OVERRIDE: 'pollution'}, {}

                def command(self, label, argv, **kwargs):
                    calls.append(label)
                    mode = kwargs.get('mode', 'baseline')
                    if label.endswith('-apk-build'):
                        if apk.exists():
                            raise AssertionError('stale final APK was not removed')
                        if fault != 'missing-apk':
                            with zipfile.ZipFile(apk, 'w') as archive:
                                archive.writestr('lib/arm64-v8a/libclash.so',
                                    b'changed-native' if fault == 'native-mutated' and mode == 'target37'
                                    else b'fixture-native')
                                archive.writestr('fixture-target', mode)
                        return 0, '' if fault == 'missing-marker' else marker(mode)
                    if label == 'invalid-override':
                        if fault == 'unrelated-invalid-failure':
                            return 1, 'plugin resolution failed'
                        if fault == 'invalid-zero':
                            return 0, marker(mode) + MOD.INVALID
                        return 1, marker(mode) + MOD.INVALID
                    if label.endswith('-apk-manifest'):
                        value = 36 if label.startswith('baseline') or fault == 'override-ignored' else 37
                        return 0, "private package label\ntargetSdkVersion:'%d'\n" % value
                    return 0, ''

            runner = FixtureRunner()
            with patch.object(MOD, 'sdk_identity', return_value={'fixture': True}):
                MOD.qualify(runner, work, output, base, base)
            self.assertEqual(calls.count('native-arm64-build'), 1)
            self.assertEqual([x for x in calls if x.endswith('-apk-build')],
                             ['baseline-apk-build', 'target37-apk-build'])
            self.assertTrue(runner.receipt['baseline']['baseline_rejected_as_37'])
            self.assertEqual(runner.receipt['invalid']['marker']['originalTarget'], 36)
            self.assertEqual(runner.receipt['target37']['target_sdk'], 37)
            self.assertNotIn('private package label', '\n'.join(p.read_text() for p in output.iterdir()))
            return runner.receipt

    def test_all_controls_run_in_order_and_export_only_target_lines(self):
        self.run_sequence()

    def test_failed_real_consumer_evidence_cannot_pass(self):
        for fault, code in [('missing-apk', 'apk-missing-or-symlink'),
                            ('missing-marker', 'dsl-marker-count'),
                            ('unrelated-invalid-failure', 'invalid-override-not-rejected'),
                            ('invalid-zero', 'invalid-override-not-rejected'),
                            ('override-ignored', 'manifest-target-mismatch'),
                            ('native-mutated', 'apk-native-variant-mismatch')]:
            with self.subTest(fault=fault), self.assertRaisesRegex(MOD.Rejected, '^' + code + '$'):
                self.run_sequence(fault)

    def test_apk_native_member_is_required(self):
        with tempfile.TemporaryDirectory() as temp:
            apk = Path(temp) / 'app.apk'
            with zipfile.ZipFile(apk, 'w') as archive:
                archive.writestr('not-native', b'fixture')
            with self.assertRaisesRegex(MOD.Rejected, '^apk-native-member$'):
                MOD.inspect_apk(None, apk, Path(temp), 'baseline', Path(temp))


class WorkflowContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / '.github/workflows/android-target-sdk-dry-run.yml').read_text()

    def test_nonpublishing_permissions_and_exact_identity(self):
        self.assertIn('permissions:\n  contents: read\n', self.workflow)
        self.assertNotIn('id-token:', self.workflow)
        self.assertEqual(self.workflow.count('secrets.'), 1)
        self.assertIn('secrets.SRC_DEPLOY_KEY', self.workflow)
        self.assertEqual(self.workflow.count('persist-credentials: false'), 2)
        self.assertIn('test "$GITHUB_WORKFLOW_SHA" = "$PUBLIC_SHA"', self.workflow)
        for forbidden in ('R2_', 'KEY_PASSWORD', 'promote', 'release.sh', 'gh release', 'aws s3'):
            self.assertNotIn(forbidden, self.workflow)
        self.assertIn('cancel-in-progress: false', self.workflow)
        self.assertIn('timeout-minutes: 75', self.workflow)

    def test_artifacts_are_exact_allowlist_without_private_output(self):
        upload = self.workflow.split('name: Retain only allowlisted qualification receipts')[1]
        self.assertNotIn('*', upload)
        for name in ('preflight.json', 'run.json', 'baseline-target.txt', 'target37-target.txt'):
            self.assertIn('/target-sdk-evidence/' + name, upload)
        for forbidden in ('.apk', '.log', 'source/', 'target-sdk-work'):
            self.assertNotIn(forbidden, upload)

    def test_real_override_is_job_owned_and_default_is_unchanged(self):
        self.assertIn('finalizeDsl', MOD.INIT)
        self.assertIn('baseline != 36', MOD.INIT)
        self.assertIn("override != '37'", MOD.INIT)
        self.assertIn('androidDsl.defaultConfig.targetSdk = 37', MOD.INIT)
        self.assertIn('org.gradle.configuration-cache=false', MOD.GRADLE_PROPERTIES)
        self.assertIn('org.gradle.workers.max=2', MOD.GRADLE_PROPERTIES)
        self.assertIn('platforms;android-37.0', self.workflow)


if __name__ == '__main__':
    unittest.main()
