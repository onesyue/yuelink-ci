"""Standalone component contract using shared root-generated policy fixtures."""
import base64
import copy
import importlib.util
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
OPS = (ROOT / 'scripts/verify-yuelink-update-manifest.py').is_file()
VERIFIER = ROOT / 'scripts' / ('verify-yuelink-update-manifest.py' if OPS else 'verify-update-manifest.py')
SPEC = importlib.util.spec_from_file_location('component_compatibility_verifier', VERIFIER)
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)
FIXTURE = ROOT / 'tests/fixtures/client-support-policy-golden.json'
INVALID = ROOT / 'tests/fixtures/client-support-policy-invalid.json'
NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


class ClientSupportPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        cls.private = cls.directory / 'isolated.pem'
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(cls.private)], check=True, capture_output=True)
        public = subprocess.check_output(['openssl', 'pkey', '-in', str(cls.private), '-pubout', '-outform', 'DER'])
        cls.public = base64.b64encode(public[-32:]).decode()
        cls.policy = json.loads(FIXTURE.read_text())['compatibility']

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def payload(self, policy=True):
        extensions = (V.PLATFORM_EXTENSIONS if OPS else {name: '.' + artifact.split('.', 1)[1] for name, artifact in V.EXPECTED_PLATFORM_ARTIFACTS.items()})
        artifacts = {name: (name.removesuffix('-appimage') + ext) for name, ext in extensions.items()}
        value = {'schemaVersion': 1, 'keyId': V.CURRENT_KEY_ID, 'version': '9.9.9', 'channel': 'stable',
                 'publishedAt': '2026-10-15T00:00:00Z', 'expiresAt': '2026-11-14T00:00:00Z',
                 'releaseUrl': 'https://yue.to/download.html', 'notes': 'Isolated policy schema fixture',
                 'platforms': {name: {'url': f'https://yuetong.app/v9.9.9/YueLink-9.9.9-{artifact}', 'sha256': 'a'*64}
                               for name, artifact in artifacts.items()}}
        if policy: value['compatibility'] = copy.deepcopy(self.policy)
        return value

    def seal(self, value):
        payload = self.directory / 'payload'
        payload.write_bytes(b'yueto:update-manifest:v1\n' + json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
        signature = subprocess.check_output(['openssl', 'pkeyutl', '-sign', '-inkey', str(self.private), '-rawin', '-in', str(payload)])
        return json.dumps({**value, 'sig': 'ed25519:' + base64.b64encode(signature).decode()}).encode()

    def verify(self, raw):
        if OPS:
            return V.verify_manifest_bytes(raw, now=NOW, trusted_public_keys_b64={V.CURRENT_KEY_ID: self.public})
        with mock.patch.object(V, 'CURRENT_PUBLIC_KEY_B64', self.public):
            result = V._verify_signed_manifest(raw)
            V._validate_manifest_policy(result, now=NOW)
            return result

    def test_legacy_and_valid_policy_remain_authenticated(self):
        for present in [False, True]:
            with self.subTest(policy=present):
                self.assertEqual('compatibility' in self.verify(self.seal(self.payload(present))), present)

    def test_shared_malformed_policy_corpus_reaches_signed_consumer(self):
        cases = json.loads(INVALID.read_text())
        self.assertTrue(cases['test_only'])
        self.assertGreaterEqual(len(cases['cases']), 32)
        for case in cases['cases']:
            with self.subTest(case=case['name']):
                value = self.payload(); value['compatibility'] = case['compatibility']
                with self.assertRaisesRegex(V.ManifestError, 'compatibility'): self.verify(self.seal(value))

    def test_policy_signature_covers_new_fields(self):
        raw = self.seal(self.payload())
        with self.assertRaisesRegex(V.ManifestError, 'signature'):
            self.verify(raw.replace(b'"transitionDays": 90', b'"transitionDays": 89'))

    def test_invalid_policy_cannot_be_laundered_as_unknown_optional_field(self):
        value = self.payload(); value['compatibility_typo'] = value.pop('compatibility')
        with self.assertRaises(V.ManifestError): self.verify(self.seal(value))

    def test_snapshot_revision_and_release_date_are_immutable(self):
        previous = self.verify(self.seal(self.payload()))['compatibility']
        current = copy.deepcopy(previous); current['effectiveAt'] = '2026-07-18T00:00:00Z'
        with self.assertRaises(ValueError): V._COMPATIBILITY.validate_transition(current, previous)
        current['policyRevision'] += 1
        V._COMPATIBILITY.validate_transition(current, previous)
        current['releases'][0]['releasedAt'] = '2026-07-18T00:00:00Z'
        with self.assertRaises(ValueError): V._COMPATIBILITY.validate_transition(current, previous)


if __name__ == '__main__':
    unittest.main()
