"""The generated verifier has the canonical matrix and no publication path."""
from __future__ import annotations

import ast
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('dial_generator', ROOT / 'scripts/generate-dialgate-verify.py')
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def admitted(workflow: str, name: str, values: dict[str, str]) -> bool:
    lines = builder.job(workflow, name).splitlines()
    for index, line in enumerate(lines):
        if line.startswith('    if: '):
            expression = line.removeprefix('    if: ')
            if expression == '>-':
                parts = []
                for continuation in lines[index + 1:]:
                    if not continuation.startswith('      '): break
                    parts.append(continuation.strip())
                expression = ' '.join(parts)
            break
    else:
        raise ValueError('job admission condition is missing')
    for key in sorted(values, key=len, reverse=True):
        expression = expression.replace(key, repr(values[key]))
    tree = ast.parse(expression.replace('&&', ' and ').replace('||', ' or '), mode='eval')
    allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Compare, ast.Eq,
               ast.NotEq, ast.Constant, ast.Load, ast.Call, ast.Name)
    if any(not isinstance(node, allowed) for node in ast.walk(tree)):
        raise ValueError('unexpected admission expression')
    if any(isinstance(node, ast.Name) and node.id not in {'always', 'startsWith'} for node in ast.walk(tree)):
        raise ValueError('unexpected admission function')
    return bool(eval(compile(tree, '<job-admission>', 'eval'),
                     {'__builtins__': {}, 'always': lambda: True,
                      'startsWith': lambda value, prefix: value.startswith(prefix)}, {}))


class DialgateVerifyOnlyTests(unittest.TestCase):
    def test_sdk_package_and_go_preload_execute_with_fail_closed_order(self) -> None:
        workflow = (ROOT / '.github/workflows/dialgate-verify.yml').read_text()
        go_block = workflow.split('      - name: Run exact-candidate dialgate Go regressions\n', 1)[1].split('      - name:', 1)[0]
        go_script = textwrap.dedent(go_block.split('        run: |\n', 1)[1])
        android = builder.job(workflow, 'android_kotlin_compile')
        sdk_script = android.split('        run: sdkmanager ', 1)[1].splitlines()[0]
        sdk_script = 'sdkmanager ' + sdk_script
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for tool in ('go', 'sdkmanager'):
                path = root / tool
                path.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\nif [ "$1" = mod ] && [ "$PRELOAD_FAIL" = yes ]; then exit 23; fi\n')
                path.chmod(0o755)
            calls = root / 'calls'
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                   'RUNNER_TEMP': directory, 'CALLS': str(calls), 'PRELOAD_FAIL': 'no'}
            result = subprocess.run(['bash', '-c', sdk_script], env=env, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(calls.read_text().splitlines(), ['platforms;android-37.0 platforms;android-36 ndk;28.2.13676358'])
            calls.unlink()
            result = subprocess.run(['bash', '-c', go_script], env=env, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(calls.read_text().splitlines(), [
                'mod download github.com/metacubex/gvisor',
                'test -p 2 ./dialgate/...', 'test -race -p 2 ./recovery .',
                'test -tags with_gvisor -p 2 ./dialgate/...',
                'test -tags with_gvisor -race -p 2 ./recovery .'])
            calls.unlink()
            result = subprocess.run(['bash', '-c', go_script], env={**env, 'PRELOAD_FAIL': 'yes'}, capture_output=True, check=False)
            self.assertEqual(result.returncode, 23)
            self.assertEqual(calls.read_text().splitlines(), ['mod download github.com/metacubex/gvisor'])

    def test_generated_workflow_matches_canonical_dial_jobs(self) -> None:
        source = (ROOT / '.github/workflows/build.yml').read_text()
        workflow = (ROOT / '.github/workflows/dialgate-verify.yml').read_text()
        self.assertEqual(workflow, builder.generate(source))
        self.assertEqual(re.findall(r'^  (\w+):$', workflow.split('\njobs:\n', 1)[1], re.M),
                         ['dial_source_binding', 'linux_client_tests', 'android_kotlin_compile', 'ios_swift_compile', 'native_compile_summary', 'dial_gate', 'dial_gate_summary'])
        source_matrix = builder.job(source, 'dial_gate').split('    strategy:\n', 1)[1].split('    steps:\n', 1)[0]
        verify_matrix = builder.job(workflow, 'dial_gate').split('    strategy:\n', 1)[1].split('    steps:\n', 1)[0]
        self.assertEqual(source_matrix, verify_matrix)
        self.assertEqual(verify_matrix.count('          - name:'), 5)

    def test_only_reviewed_private_sha_reaches_matrix(self) -> None:
        workflow = (ROOT / '.github/workflows/dialgate-verify.yml').read_text()
        dial = builder.job(workflow, 'dial_gate')
        self.assertNotIn('github.event.pull_request.head.sha', dial)
        self.assertNotIn('feat/real-dial-gate', dial)
        self.assertNotIn('dialgate_candidate_ref', workflow)
        self.assertIn('ref: ${{ needs.dial_source_binding.outputs.candidate_commit }}', dial)
        self.assertIn('EXPECTED_CANDIDATE_REVISION: ${{ needs.dial_source_binding.outputs.candidate_commit }}', dial)
        self.assertIn('refs/pull/${{ steps.inputs.outputs.source_pr }}/head', workflow)
        self.assertIn('scripts/dialgate-binding.py confirm --checkout bound-source', workflow)
        malformed = (ROOT / '.github/workflows/build.yml').read_text().replace(
            'needs.source_binding.outputs.source_commit', 'github.event.pull_request.head.sha')
        with self.assertRaises(ValueError):
            builder.generate(malformed)

    def test_actual_job_admission_event_matrix(self) -> None:
        verify = (ROOT / '.github/workflows/dialgate-verify.yml').read_text()
        release = (ROOT / '.github/workflows/build.yml').read_text()
        triggers = release.split('\non:\n', 1)[1].split('\nconcurrency:', 1)[0]
        self.assertNotIn('\n  pull_request:', '\n' + triggers)
        self.assertNotIn('dialgate_candidate_ref', release)
        values = {
            'github.repository': 'onesyue/yuelink-ci',
            'github.event.pull_request.head.repo.full_name': 'onesyue/yuelink-ci',
            'needs.dial_source_binding.result': 'success', 'needs.dial_gate.result': 'success',
            'needs.source_binding.result': 'success', 'needs.preflight.result': 'success',
            'needs.dial_gate_summary.result': 'success',
            'needs.dial_source_binding.outputs.ios_compile_mode': 'positive',
            'needs.dial_source_binding.outputs.verification_scope': 'full',
        }
        for event, ref, expected_verify, expected_release in (
            ('pull_request', 'refs/pull/46/merge', True, False),
            ('workflow_dispatch', 'refs/heads/review', True, False),
            ('workflow_dispatch', 'refs/tags/v1.3.175', False, False),
            ('push', 'refs/tags/v1.3.175', False, True),
            ('push', 'refs/heads/master', False, False),
            ('workflow_run', 'refs/heads/master', False, False),
        ):
            context = {**values, 'github.event_name': event, 'github.ref': ref}
            with self.subTest(event=event, ref=ref):
                self.assertEqual(admitted(verify, 'dial_source_binding', context), expected_verify)
                self.assertEqual(admitted(release, 'release', context), expected_release)
        fork = {**values, 'github.event_name': 'pull_request', 'github.ref': 'refs/pull/46/merge',
                'github.event.pull_request.head.repo.full_name': 'fork/repo'}
        self.assertFalse(admitted(verify, 'dial_source_binding', fork))
        for result in ('success', 'failure', 'skipped', 'cancelled'):
            context = {**values, 'needs.dial_source_binding.result': result}
            self.assertEqual(admitted(verify, 'dial_gate', context), result == 'success')
            self.assertEqual(admitted(verify, 'linux_client_tests', context), result == 'success')
            self.assertEqual(admitted(verify, 'android_kotlin_compile', context), result == 'success')
            self.assertEqual(admitted(verify, 'dial_gate_summary', context), result == 'success')
        negative = {**values, 'needs.dial_source_binding.result': 'success',
                    'needs.dial_source_binding.outputs.ios_compile_mode': 'negative'}
        for name in ('dial_gate', 'dial_gate_summary', 'linux_client_tests', 'android_kotlin_compile'):
            self.assertFalse(admitted(verify, name, negative))
        self.assertTrue(admitted(verify, 'ios_swift_compile', negative))
        self.assertTrue(admitted(verify, 'native_compile_summary', negative))
        for dependency in ('source_binding', 'preflight', 'dial_gate', 'dial_gate_summary'):
            context = {**values, 'github.event_name': 'push', 'github.ref': 'refs/tags/v1.3.175',
                       f'needs.{dependency}.result': 'failure'}
            self.assertFalse(admitted(release, 'release', context))

    def test_no_publication_authority_or_listener(self) -> None:
        workflow = (ROOT / '.github/workflows/dialgate-verify.yml').read_text()
        for forbidden in ('name: Build YueLink', 'pull_request_target:', 'workflow_run:',
                          'environment:', 'id-token:', 'attestations:', 'R2_', 'CLOUDFLARE_',
                          'workflow_call:', 'gh workflow', 'promote_in_pipeline', 'release.sh'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, workflow)
        self.assertEqual(set(re.findall(r'secrets\.([A-Z_]+)', workflow)), {'SRC_DEPLOY_KEY'})
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", workflow)
        self.assertIn("startsWith(github.ref, 'refs/heads/')", workflow)
        for action in re.findall(r'uses: (\S+)', workflow):
            self.assertRegex(action, r'^[A-Za-z0-9_./-]+@[0-9a-f]{40}$')
        for path in (ROOT / '.github/workflows').glob('*.yml'):
            text = path.read_text()
            for listener in re.findall(r'^  workflow_run:.*?(?=\n  \S|\npermissions:|\Z)', text, re.M | re.S):
                self.assertNotIn('YueLink dial verification', listener, path.name)
                self.assertNotIn('*', listener, path.name)

    def test_linux_client_job_uses_same_source_and_reviewed_sdk(self) -> None:
        workflow = (ROOT / '.github/workflows/dialgate-verify.yml').read_text()
        job = builder.job(workflow, 'linux_client_tests')
        canonical = (ROOT / '.github/workflows/build.yml').read_text()
        sdk = re.search(r"^  FLUTTER_VERSION: '([0-9.]+)'", canonical, re.M)
        assert sdk is not None
        self.assertIn(f"  FLUTTER_VERSION: '{sdk[1]}'", workflow)
        installers = set(re.findall(r'uses: (onesyue/yuelink-ci/\.github/actions/setup-flutter@[0-9a-f]{40})', canonical))
        self.assertEqual(len(installers), 1)
        self.assertIn('uses: ' + next(iter(installers)), job)
        self.assertIn('flutter-version: ${{ env.FLUTTER_VERSION }}', job)
        self.assertIn('ref: ${{ needs.dial_source_binding.outputs.candidate_commit }}', job)
        self.assertIn('dialgate-binding.py confirm --checkout .', job)
        self.assertLess(job.index('Confirm client test source identity'), job.index('flutter pub get'))
        self.assertIn('run: flutter pub get --enforce-lockfile', job)
        self.assertIn('      TZ: UTC', job)
        self.assertIn('        working-directory: candidate', job)
        self.assertNotIn('flutter build', job)
        self.assertNotIn('environment:', job)

    def test_linux_client_command_scope_and_failure_propagation(self) -> None:
        self.assert_linux_client_command((ROOT / '.github/workflows/dialgate-verify.yml').read_text())

    def test_added_client_targets_cannot_be_dropped(self) -> None:
        workflow = (ROOT / '.github/workflows/dialgate-verify.yml').read_text()
        targets = ('test/automation/workflow_guard_test.dart', 'test/core/api/recovery_probe_api_test.dart',
                   'test/modules/smart_select_cached_delay_test.dart', 'test/modules/smart_select_and_quota_test.dart',
                   'test/modules/smart_select_cross_protocol_test.dart', 'test/modules/dashboard',
                   'test/providers/proxy_groups_test.dart', 'test/modules/nodes/manual_selection_test.dart')
        for target in targets:
            with self.subTest(target=target):
                mutated = workflow.replace('            ' + target + ' ' + chr(92) + '\n', '', 1)
                with self.assertRaises(AssertionError):
                    self.assert_linux_client_command(mutated)

    def assert_linux_client_command(self, workflow: str) -> None:
        job = builder.job(workflow, 'linux_client_tests')
        step = job.split('- name: Run scoped Linux client tests\n', 1)[1].split('      - name:', 1)[0]
        script = textwrap.dedent(step.split('run: |\n', 1)[1])
        targets = ['test/modules/updater', 'test/services/update_checker_test.dart',
                   'test/automation/macos_manifest_floor_test.dart',
                   'test/automation/prepare_manifest_rollback_test.dart',
                   'test/modules/force_upgrade_dialog_test.dart',
                   'test/modules/force_upgrade_launch_contract_20260926_test.dart',
                   'test/services/update_manifest_compatibility_test.dart',
                   'test/shared/crash_frame_privacy_20261111_test.dart',
                   'test/shared/error_logger_test.dart', 'test/shared/error_logger_context_20261006_test.dart',
                   'test/infrastructure', 'test/core/kernel/config/config_parse_soft_fail_test.dart',
                   'test/services/config_template_test.dart',
                   'test/automation/workflow_guard_test.dart',
                   'test/core/api/recovery_probe_api_test.dart',
                   'test/modules/smart_select_cached_delay_test.dart',
                   'test/modules/smart_select_and_quota_test.dart',
                   'test/modules/smart_select_cross_protocol_test.dart',
                   'test/modules/dashboard',
                   'test/providers/proxy_groups_test.dart',
                   'test/modules/nodes/manual_selection_test.dart']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); candidate = root / 'candidate'; candidate.mkdir()
            fake = root / 'flutter'
            fake.write_text("#!/usr/bin/env python3\nimport json, os, pathlib, sys\n"
                            "pathlib.Path(os.environ['CALLS']).write_text(json.dumps({'args':sys.argv[1:], 'tz':os.environ.get('TZ'), 'cwd':os.getcwd()}))\n"
                            "print('test report')\nraise SystemExit(int(os.environ['FAKE_EXIT']))\n")
            fake.chmod(0o755)
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                   'RUNNER_TEMP': str(root), 'CALLS': str(root / 'calls'), 'TZ': 'UTC'}
            for code in (0, 17):
                result = subprocess.run(['bash', '-c', script], cwd=candidate,
                                        env={**env, 'FAKE_EXIT': str(code)}, capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stderr)
                call = json.loads((root / 'calls').read_text())
                self.assertEqual(call['args'], ['test', '--no-pub', '--concurrency=2', *targets, '--reporter', 'expanded'])
                self.assertEqual(call['tz'], 'UTC')
                self.assertEqual(Path(call['cwd']), candidate.resolve())
                self.assertEqual((root / 'linux-client-tests.log').read_text(), 'test report\n')

    def assert_go_regression_contract(self, workflow: str) -> None:
        job = builder.job(workflow, 'linux_client_tests')
        canonical = (ROOT / '.github/workflows/build.yml').read_text()
        installers = set(re.findall(r'uses: actions/setup-go@([0-9a-f]{40})', canonical))
        self.assertEqual(len(installers), 1)
        self.assertIn('uses: actions/setup-go@' + next(iter(installers)), job)
        self.assertIn('go-version: ${{ env.GO_VERSION }}', job)
        self.assertIn("  GO_VERSION: '1.27.4'", workflow)
        self.assertIn('ref: ${{ needs.dial_source_binding.outputs.candidate_commit }}', job)
        marker = '- name: Run exact-candidate dialgate Go regressions\n'
        self.assertEqual(job.count(marker), 1)
        self.assertLess(job.index('Confirm client test source identity'), job.index(marker))
        step = job.split(marker, 1)[1].split('      - name:', 1)[0]
        self.assertIn('working-directory: candidate/core', step)
        self.assertIn('GOTOOLCHAIN: local', step)
        script = textwrap.dedent(step.split('run: |\n', 1)[1])
        self.assertIn('${{ runner.temp }}/linux-dialgate-go-tests.log', job)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); core = root / 'candidate/core'; core.mkdir(parents=True)
            fake = root / 'go'
            fake.write_text("#!/usr/bin/env python3\nimport json, os, pathlib, sys\n"
                            "p=pathlib.Path(os.environ['CALLS']); calls=p.read_text().splitlines() if p.exists() else []\n"
                            "with p.open('a') as f: f.write(json.dumps({'args':sys.argv[1:], 'toolchain':os.environ.get('GOTOOLCHAIN'), 'cwd':os.getcwd()})+'\\n')\n"
                            "print('synthetic Go test report')\nraise SystemExit(17 if len(calls)+1==int(os.environ['FAKE_FAIL_AT']) else 0)\n")
            fake.chmod(0o755)
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                   'RUNNER_TEMP': str(root), 'CALLS': str(root / 'calls'), 'GOTOOLCHAIN': 'local'}
            expected = [
                ['mod', 'download', 'github.com/metacubex/gvisor'],
                ['test', '-p', '2', './dialgate/...'],
                ['test', '-race', '-p', '2', './recovery', '.'],
                ['test', '-tags', 'with_gvisor', '-p', '2', './dialgate/...'],
                ['test', '-tags', 'with_gvisor', '-race', '-p', '2', './recovery', '.'],
            ]
            for fail_at in range(6):
                (root / 'calls').unlink(missing_ok=True)
                (root / 'linux-dialgate-go-tests.log').unlink(missing_ok=True)
                result = subprocess.run(['bash', '-c', script], cwd=core,
                                        env={**env, 'FAKE_FAIL_AT': str(fail_at)}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 17 if fail_at else 0, result.stderr)
                calls = [json.loads(line) for line in (root / 'calls').read_text().splitlines()]
                self.assertEqual([call['args'] for call in calls], expected[:fail_at or 5])
                for call in calls:
                    self.assertEqual(call['toolchain'], 'local')
                    self.assertEqual(Path(call['cwd']), core.resolve())
                if fail_at == 1:
                    self.assertFalse((root / 'linux-dialgate-go-tests.log').exists())
                else:
                    self.assertEqual((root / 'linux-dialgate-go-tests.log').read_text(),
                                     'synthetic Go test report\n' * ((fail_at or 5) - 1))

    def test_go_regressions_use_exact_source_toolchain_scope_and_propagate_failure(self) -> None:
        self.assert_go_regression_contract((ROOT / '.github/workflows/dialgate-verify.yml').read_text())

    def test_go_regression_contract_negative_controls(self) -> None:
        workflow = (ROOT / '.github/workflows/dialgate-verify.yml').read_text()
        job = builder.job(workflow, 'linux_client_tests')
        for before, after in (
            ('Run exact-candidate dialgate Go regressions', 'Removed Go regression step'),
            ('working-directory: candidate/core', 'working-directory: candidate'),
            ('go test -p 2 ./dialgate/...', 'go test -p 2 ./...'),
            ('GOTOOLCHAIN: local', 'GOTOOLCHAIN: auto'),
            ('-tags with_gvisor', '-tags other_tag'),
            ('-race -p 2 ./recovery .', '-p 2 ./recovery .'),
            ('set -euo pipefail\n          # The default', 'set -eu\n          # The default'),
            ('ref: ${{ needs.dial_source_binding.outputs.candidate_commit }}', 'ref: master'),
        ):
            with self.subTest(mutation=after):
                mutated = workflow.replace(job, job.replace(before, after, 1), 1)
                with self.assertRaises(AssertionError):
                    self.assert_go_regression_contract(mutated)

    def test_generator_rejects_ambiguous_go_installer(self) -> None:
        canonical = (ROOT / '.github/workflows/build.yml').read_text()
        with self.assertRaisesRegex(ValueError, 'one reviewed Go installer'):
            builder.generate(canonical + '\n# uses: actions/setup-go@' + '0' * 40 + '\n')

    def test_summary_uses_this_runs_artifacts_and_trusted_identity_checker(self) -> None:
        workflow = (ROOT / '.github/workflows/dialgate-verify.yml').read_text()
        summary = builder.job(workflow, 'dial_gate_summary')
        self.assertIn('scripts/verify-dial-report-identity.py', summary)
        self.assertIn('ref: ${{ github.event.pull_request.head.sha || github.sha }}', summary)
        self.assertIn('persist-credentials: false', summary)
        self.assertIn('pattern: dial-gate-*', summary)
        self.assertNotIn('run-id:', summary)
        self.assertNotIn('repository:', summary)
        self.assertIn('needs.dial_source_binding.outputs.candidate_commit', summary)


    def test_scope_job_matrix_and_native_summary_execute_fail_closed(self) -> None:
        workflow = (ROOT / '.github/workflows/dialgate-verify.yml').read_text()
        summary = builder.job(workflow, 'native_compile_summary')
        self.assertIn("'iOS-only unsigned compilation summary' || 'Required native compilation summary'", summary)
        script = textwrap.dedent(summary.split('        run: |\n', 1)[1])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'summary'
            for scope in ('full', 'ios-only', '', 'unknown'):
                for mode in ('positive', 'negative', 'unknown'):
                    for binding in ('success', 'failure', 'skipped', 'cancelled'):
                        values = {'needs.dial_source_binding.result': binding,
                                  'needs.dial_source_binding.outputs.ios_compile_mode': mode,
                                  'needs.dial_source_binding.outputs.verification_scope': scope,
                                  'needs.dial_gate.result': 'success'}
                        expected = binding == 'success' and scope == 'full' and mode == 'positive'
                        for job in ('linux_client_tests', 'android_kotlin_compile', 'dial_gate', 'dial_gate_summary'):
                            self.assertEqual(admitted(workflow, job, values), expected)
                        # A failed binding never starts actual compilation. Scope
                        # validation itself occurs in that mandatory binding job.
                        self.assertEqual(admitted(workflow, 'ios_swift_compile', values), binding == 'success')
                        for ios in ('success', 'failure', 'skipped', 'cancelled'):
                            for android in ('success', 'failure', 'skipped', 'cancelled'):
                                output.unlink(missing_ok=True)
                                env = {**os.environ, 'BINDING': binding, 'SCOPE': scope, 'MODE': mode,
                                       'IOS': ios, 'ANDROID': android, 'SOURCE': 'a'*40,
                                       'BUILDER': 'b'*40, 'GITHUB_STEP_SUMMARY': str(output)}
                                result = subprocess.run(['bash', '-c', script], env=env, capture_output=True)
                                expected_pass = (binding == 'success' and mode == 'positive' and ios == 'success'
                                                 and ((scope == 'full' and android == 'success')
                                                      or (scope == 'ios-only' and android == 'skipped')))
                                self.assertEqual(result.returncode == 0, expected_pass, (scope, mode, binding, ios, android))
                                text = output.read_text() if output.exists() else ''
                                if expected_pass and scope == 'ios-only':
                                    self.assertIn('iOS-only unsigned compilation passed', text)
                                    self.assertIn('Android/Linux/dial were not run', text)
                                    self.assertIn('p95 results remain separate', text)
                                    self.assertNotIn('Bound Android and unsigned iOS compilation passed', text)
                                if expected_pass and scope == 'full':
                                    self.assertIn('Bound Android and unsigned iOS compilation passed', text)
                                if expected_pass:
                                    self.assertIn('source: '+'a'*40, text)
                                    self.assertIn('builder: '+'b'*40, text)
                                    self.assertIn('Verification scope: '+scope, text)

    def test_scope_policy_and_output_are_explicit_and_full_is_default(self) -> None:
        workflow = (ROOT / '.github/workflows/dialgate-verify.yml').read_text()
        self.assertIn('verification_scope: ${{ steps.inputs.outputs.verification_scope }}', workflow)
        inputs = workflow.split('      verification_scope:\n', 1)[1].split('      source_sha:', 1)[0]
        self.assertIn('default: full', inputs)
        self.assertIn('options: [full, ios-only]', inputs)
        manifest = json.loads((ROOT/'policy/dialgate-source.json').read_text())
        self.assertEqual(manifest['verificationScope'], 'ios-only')
        self.assertEqual(manifest['sourcePr'], 131)
        self.assertEqual(manifest['sourceCommit'], '0b5096da3ceeca50b2f0c5185147a62df078e7d0')
        self.assertEqual(manifest['iosCompileMode'], 'positive')
