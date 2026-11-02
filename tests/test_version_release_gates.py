"""Exercise release signature/version admission and drift failure classes offline."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SourceTagTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        self.git('config', 'core.hooksPath', '/dev/null')
        # Ephemeral SSH signing key; real git verify-tag, no developer keys.
        self.key = self.repo / 'test_key'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(self.key)], check=True)
        allowed = self.repo / 'allowed_signers'
        allowed.write_text('test@example.invalid ' + self.key.with_suffix('.pub').read_text())
        self.git('config', 'gpg.format', 'ssh')
        self.git('config', 'user.signingkey', str(self.key))
        self.git('config', 'gpg.ssh.allowedSignersFile', str(allowed))
        (self.repo / 'pubspec.yaml').write_text('version: 1.2.3+4\n')
        self.git('add', 'pubspec.yaml')
        self.git('commit', '-qm', 'fixture')
        self.sha = self.git('rev-parse', 'HEAD')

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo, stderr=subprocess.PIPE).decode().strip()

    def verify(self, tag, sha=None):
        return subprocess.run(['bash', str(ROOT / 'scripts/verify-source-version.sh'),
                               str(self.repo), tag, sha or self.sha], text=True, capture_output=True)

    def test_signed_matching_tag_passes(self):
        self.git('tag', '-s', 'v1.2.3', '-m', 'release')
        self.assertEqual(self.verify('v1.2.3').returncode, 0)

    def test_signed_wrong_version_and_wrong_sha_fail(self):
        self.git('tag', '-s', 'v1.2.4', '-m', 'release')
        self.assertIn('differs from pubspec', self.verify('v1.2.4').stderr)
        self.git('tag', '-s', 'v1.2.3', '-m', 'release')
        self.assertIn('attested source SHA', self.verify('v1.2.3', '0' * 40).stderr)

    def test_unsigned_and_lightweight_tags_fail(self):
        for tag, flags in (('v1.2.3', ['-a', '-m', 'unsigned']), ('v1.2.4', ['--no-sign'])):
            self.git('tag', *flags, tag)
            result = self.verify(tag)
            self.assertEqual(result.returncode, 1)
            self.assertIn('signature verification failed', result.stderr)

    def test_missing_trust_fails(self):
        self.git('tag', '-s', 'v1.2.3', '-m', 'release')
        (self.repo / 'allowed_signers').unlink()
        self.assertEqual(self.verify('v1.2.3').returncode, 1)


class DriftTests(unittest.TestCase):
    def run_drift(self, *, age=None, channel='1.2.3', broken=None):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / 'scripts').mkdir()
            data = dict(sha='a' * 40, tag='v1.2.3', version='1.2.4',
                        first_shipped_at=None if age is None else int(time.time()) - age)
            helper = repo / 'scripts/check-version-bump.sh'
            helper.write_text("#!/bin/bash\n[[ \"$*\" == '--ref refs/remotes/origin/master --report-json' ]] || exit 2\n" +
                              ("exit 1\n" if broken == 'history' else "cat <<'JSON'\n" + json.dumps(data) + '\nJSON\n'))
            manifest = repo / 'manifest.json'
            manifest.write_text('invalid json' if broken == 'manifest' else json.dumps({'version': channel}))
            return subprocess.run(['bash', str(ROOT / 'scripts/release-drift.sh'), str(repo),
                                   '--no-fetch', '--manifest-file', str(manifest)], text=True, capture_output=True)

    def test_clean_or_recent_source_passes(self):
        for age in (None, 3600):
            result = self.run_drift(age=age)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('yuelink master sha:', result.stdout)
            self.assertIn('pubspec version: 1.2.4', result.stdout)

    def test_old_source_fails(self):
        result = self.run_drift(age=86401)
        self.assertEqual(result.returncode, 1)
        self.assertIn('more than 24h', result.stderr)

    def test_channel_mismatch_fails(self):
        result = self.run_drift(channel='1.2.2')
        self.assertEqual(result.returncode, 1)
        self.assertIn('channel differs', result.stderr)

    def test_missing_history_or_malformed_channel_is_unknown(self):
        for broken in ('history', 'manifest'):
            result = self.run_drift(broken=broken)
            self.assertEqual(result.returncode, 1)
            self.assertIn('UNKNOWN', result.stderr)

    def test_network_failure_is_unknown(self):
        # Exercise main with a failed transport without contacting production.
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('release_drift', ROOT / 'scripts/release_drift.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = subprocess.CompletedProcess([], 0, json.dumps(dict(
            sha='a' * 40, tag='v1.2.3', version='1.2.3', first_shipped_at=None)))
        with patch('sys.argv', ['release_drift', '/unused', '--no-fetch']), \
             patch.object(module.subprocess, 'run', return_value=result), \
             patch.object(module.urllib.request, 'urlopen', side_effect=OSError('offline')):
            self.assertEqual(module.main(), 1)


class WiringTests(unittest.TestCase):
    def test_admission_precedes_public_tag_creation(self):
        source = (ROOT / 'release.sh').read_text()
        self.assertLess(source.index('bash scripts/verify-source-version.sh'), source.index('git tag -s'))

    def test_existing_validation_workflows_invoke_source_gate(self):
        for filename in ('policy-ci.yml', 'source-attestation.yml'):
            workflow = (ROOT / '.github/workflows' / filename).read_text()
            self.assertIn('run: bash scripts/check-version-bump.sh --ref HEAD', workflow)
            self.assertIn('fetch-depth: 0', workflow)


if __name__ == '__main__':
    unittest.main()
