"""Reject mutable/fork/release inputs at the verification-only source boundary."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/dialgate-binding.py'
SPEC = importlib.util.spec_from_file_location('dialgate_binding', SCRIPT)
assert SPEC and SPEC.loader
binding = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(binding)
SHA = 'a' * 40


class DialgateSourceBindingTests(unittest.TestCase):
    def event(self) -> dict:
        return {'repository': {'full_name': 'onesyue/yuelink-ci'},
                'pull_request': {'head': {'sha': 'b' * 40, 'repo': {'full_name': 'onesyue/yuelink-ci'}}},
                'inputs': {'source_pr': '113', 'source_sha': SHA}}

    def manifest(self) -> dict:
        return {'sourceRepository': 'onesyue/yuelink', 'sourcePr': 113, 'sourceCommit': SHA, 'iosCompileMode': 'positive'}

    def test_pr_uses_reviewed_private_commit_not_builder_head(self) -> None:
        self.assertEqual(binding.source_input('pull_request', self.event(), 'refs/pull/46/merge', self.manifest()), (113, SHA, 'positive'))

    def test_modes_are_explicit_fail_closed_and_event_specific(self) -> None:
        event = self.event()
        event['inputs']['ios_compile_mode'] = 'negative'
        for mode in ('positive', 'negative'):
            manifest = {**self.manifest(), 'iosCompileMode': mode}
            self.assertEqual(binding.source_input('pull_request', event, '', manifest), (113, SHA, mode))
        self.assertEqual(binding.source_input('workflow_dispatch', event, 'refs/heads/review', None), (113, SHA, 'negative'))
        for invalid in (None, True, 1, '', 'Negative', 'negative\nsource_pr=1', [], {}):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                binding.source_input('pull_request', event, '', {**self.manifest(), 'iosCompileMode': invalid})
            event['inputs']['ios_compile_mode'] = invalid
            with self.assertRaises(ValueError):
                binding.source_input('workflow_dispatch', event, 'refs/heads/review', None)
        manifest = self.manifest()
        del manifest['iosCompileMode']
        with self.assertRaises(ValueError):
            binding.source_input('pull_request', self.event(), '', manifest)

    def test_dispatch_admits_exact_branch_source_only(self) -> None:
        self.assertEqual(binding.source_input('workflow_dispatch', self.event(), 'refs/heads/review', None), (113, SHA, 'positive'))
        for ref in ('refs/tags/v1.3.175', 'master', '', 'refs/heads/'):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                binding.source_input('workflow_dispatch', self.event(), ref, None)

    def test_rejects_invalid_source_manifest_and_fork(self) -> None:
        for manifest in (None, {}, {**self.manifest(), 'sourceRepository': 'other/repo'},
                         {**self.manifest(), 'sourcePr': True}, {**self.manifest(), 'sourcePr': 0},
                         {**self.manifest(), 'sourceCommit': 'master'},
                         {**self.manifest(), 'sourceCommit': 'A' * 40},
                         {**self.manifest(), 'extra': 'override'}):
            with self.subTest(manifest=manifest), self.assertRaises(ValueError):
                binding.source_input('pull_request', self.event(), 'refs/pull/46/merge', manifest)
        event = self.event(); event['pull_request']['head']['repo']['full_name'] = 'fork/repo'
        with self.assertRaises(ValueError):
            binding.source_input('pull_request', event, 'refs/pull/46/merge', self.manifest())
        for name in ('push', 'pull_request_target', 'workflow_run'):
            with self.subTest(event=name), self.assertRaises(ValueError):
                binding.source_input(name, self.event(), 'refs/heads/master', self.manifest())

    def test_cli_reads_dispatch_event_and_writes_exact_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); event = root / 'event.json'; output = root / 'output'
            event.write_text(json.dumps(self.event()))
            env = {**os.environ, 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                   'GITHUB_REF': 'refs/heads/review', 'GITHUB_EVENT_PATH': str(event),
                   'GITHUB_OUTPUT': str(output)}
            result = subprocess.run([sys.executable, str(SCRIPT), 'inputs'], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(), f'source_pr=113\ncandidate_commit={SHA}\nios_compile_mode=positive\n')
            output.unlink(); env['GITHUB_REF'] = 'refs/tags/v1.3.175'
            result = subprocess.run([sys.executable, str(SCRIPT), 'inputs'], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())

    def test_checkout_confirmation_rejects_changed_head_and_persisted_auth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
            def git(*args: str) -> str:
                return subprocess.check_output(['git', '-C', str(path), *args], env=env, stderr=subprocess.DEVNULL, text=True).strip()
            git('init', '-q'); git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.invalid')
            (path / 'fixture.txt').write_text('fixture')
            git('add', 'fixture.txt'); git('commit', '-qm', 'fixture')
            actual = git('rev-parse', 'HEAD')
            binding.confirm_checkout(path, actual)
            with self.assertRaises(ValueError):
                binding.confirm_checkout(path, SHA)
            git('config', 'core.sshCommand', 'false')
            with self.assertRaises(ValueError):
                binding.confirm_checkout(path, actual)
