"""Exercise release signature/version admission and drift failure classes offline."""
import json
import os
from pathlib import Path
import subprocess
import shutil
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


class OpenPGPTrustTests(unittest.TestCase):
    """Use separate keyrings so a valid signature does not imply owner trust."""

    git = SourceTagTests.git
    verify = SourceTagTests.verify

    def setUp(self):
        SourceTagTests.setUp(self)
        from unittest.mock import patch
        signing = self.repo / 'signing'
        verifying = self.repo / 'verifying'
        for home in (signing, verifying):
            home.mkdir(mode=0o700)
        self.addCleanup(subprocess.run, ['gpgconf', '--homedir', str(signing), '--kill', 'all'],
                        capture_output=True)
        self.addCleanup(subprocess.run, ['gpgconf', '--homedir', str(verifying), '--kill', 'all'],
                        capture_output=True)
        self.gpg(signing, '--passphrase', '', '--quick-generate-key',
                 'Gate Test <test@example.invalid>', 'ed25519', 'sign', '0')
        records = self.gpg(signing, '--with-colons', '--list-keys').stdout.decode()
        fingerprint = next(line.split(':')[9] for line in records.splitlines()
                           if line.startswith('fpr:'))
        self.fingerprint = fingerprint
        self.git('config', 'gpg.format', 'openpgp')
        self.git('config', 'user.signingkey', fingerprint)
        # Explicitly permissive local config must not weaken release admission.
        self.git('config', 'gpg.minTrustLevel', 'undefined')
        with patch.dict(os.environ, GNUPGHOME=str(signing)):
            self.git('tag', '-s', 'v1.2.3', '-m', 'release')
        public = self.gpg(signing, '--export', fingerprint).stdout
        self.gpg(verifying, '--import', input=public)
        self.verifying = verifying
        self.env_patch = patch.dict(os.environ, GNUPGHOME=str(verifying))
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def gpg(self, home, *args, input=None):
        return subprocess.run(['gpg', '--homedir', str(home), '--batch', '--pinentry-mode',
                               'loopback', *args], input=input, capture_output=True, check=True)

    def test_untrusted_openpgp_signature_fails_then_authorized_signer_passes(self):
        raw = subprocess.run(['git', 'verify-tag', '--raw', 'v1.2.3'], cwd=self.repo,
                             text=True, capture_output=True)
        self.assertEqual(raw.returncode, 0, raw.stderr)
        self.assertIn('TRUST_UNDEFINED', raw.stderr)
        result = self.verify('v1.2.3')
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('signature verification failed', result.stderr)
        self.gpg(self.verifying, '--import-ownertrust',
                 input=f'{self.fingerprint}:6:\n'.encode())
        self.assertEqual(self.verify('v1.2.3').returncode, 0)


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


class SourceServiceIntegrationTests(unittest.TestCase):
    def test_service_only_change_fails_source_admission_and_old_change_fails_drift(self):
        source = os.environ.get('SOURCE_VERSION_GATE_REPO')
        scripts = Path(source) / 'scripts' if source else ROOT / 'tests/fixtures/source-version-gate'
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)

            def git(*args, env=None):
                return subprocess.check_output(['git', *args], cwd=repo, env=env,
                                               stderr=subprocess.PIPE).decode().strip()

            git('init', '-q')
            for key, value in (('user.name', 'Test'), ('user.email', 'test@example.invalid'),
                               ('commit.gpgsign', 'false'), ('tag.gpgsign', 'false'),
                               ('core.hooksPath', '/dev/null')):
                git('config', key, value)
            (repo / 'scripts').mkdir()
            for name in ('check-version-bump.sh', 'version_gate.py'):
                shutil.copyfile(scripts / name, repo / 'scripts' / name)
            (repo / 'pubspec.yaml').write_text('version: 1.2.3+4\n')
            (repo / 'service').mkdir()
            service = repo / 'service/main.go'
            service.write_text('package main\n')
            git('add', '.')
            git('commit', '-qm', 'baseline')
            git('tag', 'v1.2.3')
            manifest = repo / 'manifest.json'
            manifest.write_text(json.dumps({'version': '1.2.3'}))
            gate = ['bash', str(repo / 'scripts/check-version-bump.sh'), '--ref', 'HEAD']
            drift = ['bash', str(ROOT / 'scripts/release-drift.sh'), str(repo),
                     '--no-fetch', '--manifest-file', str(manifest)]
            git('update-ref', 'refs/remotes/origin/master', 'HEAD')
            for command in (gate, drift):
                result = subprocess.run(command, cwd=repo, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            service.write_text('package main\nfunc main() {}\n')
            git('add', 'service/main.go')
            git('commit', '-qm', 'native service change', env=dict(
                os.environ, GIT_AUTHOR_DATE='2020-01-01T00:00:00Z',
                GIT_COMMITTER_DATE='2020-01-01T00:00:00Z'))
            git('update-ref', 'refs/remotes/origin/master', 'HEAD')
            result = subprocess.run(gate, cwd=repo, text=True, capture_output=True)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn('bump pubspec version', result.stderr)
            report = subprocess.check_output(gate + ['--report-json'], cwd=repo, text=True)
            self.assertEqual(json.loads(report)['first_shipped_at'], 1577836800)
            result = subprocess.run(drift, cwd=repo, text=True, capture_output=True)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn('more than 24h', result.stderr)


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
