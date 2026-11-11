"""Archive admission keeps signature trust and original build identity separate."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('rollout_admission', ROOT / 'scripts/prepare-update-rollout.py')
assert SPEC and SPEC.loader
ADMISSION = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(ADMISSION)
NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)


class RolloutAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.baseline_raw = (ROOT / 'tests/fixtures/update-manifest-v1.3.172.json').read_bytes()
        self.candidate_raw = (ROOT / 'tests/fixtures/update-manifest-v1.3.173.json').read_bytes()
        self.baseline = json.loads(self.baseline_raw)
        self.candidate = json.loads(self.candidate_raw)

    def test_existing_verifier_accepts_real_historical_signature_pair(self):
        baseline, candidate = ADMISSION.verified_pair(self.baseline_raw, self.candidate_raw, NOW)
        self.assertEqual((baseline['version'], candidate['version']), ('1.3.172', '1.3.173'))
        for raw in (self.candidate_raw.replace(b'1.3.173', b'1.3.175'), b'{}'):
            with self.assertRaises(ValueError):
                ADMISSION.verified_pair(self.baseline_raw, raw, NOW)
        with self.assertRaisesRegex(ValueError, 'expired'):
            ADMISSION.verified_pair(self.baseline_raw, self.candidate_raw, datetime(2027, 1, 1, tzinfo=timezone.utc))

    def test_percentage_fields_and_ios_force_are_rejected(self):
        for extra in (
            {'rollout': {'salt': 'synthetic', 'percent': 10}},
            {'percent': 10},
        ):
            with self.assertRaisesRegex(ValueError, 'rollout percentage'):
                ADMISSION.policy(dict(self.candidate, **extra), NOW)
        ADMISSION.policy(self.candidate, NOW)
        with self.assertRaisesRegex(ValueError, 'non-iOS'):
            ADMISSION.policy(dict(
                self.candidate, force='hard', forceReason='security',
                forcePlatforms=['ios', 'windows-amd64-setup'],
            ), NOW)
        with self.assertRaisesRegex(ValueError, 'forceDeadline'):
            ADMISSION.policy(dict(
                self.candidate, force='soft', forceDeadline='2026-12-01T00:00:00Z',
                forceReason='security',
            ), NOW)
        with self.assertRaisesRegex(ValueError, 'non-iOS'):
            ADMISSION.policy(dict(
                self.candidate, force='soft',
                forcePlatforms=['ios', 'windows-amd64-setup'],
            ), NOW)
        names = sorted(name for name in self.candidate['platforms'] if not str(name).startswith('ios'))
        with self.assertRaisesRegex(ValueError, 'soft promotion rejects a platform-force manifest'):
            ADMISSION.policy(dict(
                self.candidate, force='soft', platformForce='hard',
                forceReason='security', forcePlatforms=names,
            ), NOW)
        with self.assertRaisesRegex(ValueError, 'global with no forcePlatforms'):
            ADMISSION.policy(self.candidate, NOW, force='hard', force_reason='security')
        with self.assertRaisesRegex(ValueError, 'global with no forcePlatforms'):
            ADMISSION.policy(dict(
                self.candidate, force='soft', platformForce='hard', forceReason='security',
                forcePlatforms=['windows-amd64-setup'],
            ), NOW, force='hard', force_reason='security')
        hard = dict(
            self.candidate, force='soft', platformForce='hard',
            forceReason='incompatible', forcePlatforms=names,
        )
        ADMISSION.policy(hard, NOW, force='hard', force_reason='incompatible')
        global_hard = dict(self.candidate, force='hard', forceReason='security')
        ADMISSION.policy(global_hard, NOW, force='hard', force_reason='security')

    def synthetic_binding(self):
        # These synthetic checksum bytes do NOT claim a production signature.
        # Actual Ed25519 admission is independently exercised above. The plan
        # state tests mock only verified_pair, never the binding/identity logic.
        entries = {asset['url'].rsplit('/', 1)[1]: asset['sha256'] for asset in self.candidate['platforms'].values()}
        for platform in ('android', 'ios', 'linux', 'macos', 'windows'):
            for suffix in ('INSTALL-NOTICE.txt', 'RELEASE.json'):
                entries[f'YueLink-1.3.173-{platform}-{suffix}'] = hashlib.sha256((platform + suffix).encode()).hexdigest()
        payloads = [{'name': key, 'sha256': value} for key, value in entries.items()]
        sums = ''.join(f"{row['sha256']}  {row['name']}\n" for row in payloads).encode()
        self.candidate['sha256SumsSha256'] = ADMISSION.digest(sums)
        self.candidate['promotionEvidence']['sha256SumsSha256'] = ADMISSION.digest(sums)
        evidence = self.candidate['promotionEvidence']
        return {'schemaVersion': 2, 'mode': 'in-pipeline', 'version': '1.3.173', 'tag': 'v1.3.173',
                'sourceCommit': self.candidate['sourceCommit'], 'signedArchiveSha256': ADMISSION.digest(self.candidate_raw),
                'candidateSha256': evidence['candidateSha256'], 'sha256SumsSha256': evidence['sha256SumsSha256'],
                'payloadBundleSha256': evidence['payloadBundleSha256'], 'payloadCount': 19, 'payloads': payloads,
                'builderCommit': 'a' * 40, 'buildRunId': '123', 'sourceAttestationRunId': '456'}

    def arguments(self) -> dict[str, Any]:
        binding = self.synthetic_binding()
        return dict(baseline_raw=self.baseline_raw, candidate_raw=self.candidate_raw, binding_raw=json.dumps(binding).encode(),
                    build_run={'id': 123, 'head_sha': 'a' * 40, 'repository': {'full_name': 'onesyue/yuelink-ci'},
                               'path': '.github/workflows/build.yml', 'event': 'push', 'head_branch': 'v1.3.173',
                               'status': 'completed', 'conclusion': 'success'},
                    version='1.3.173', baseline_version='1.3.172', force='soft',
                    force_reason=None, previous=None, initial_root=self.baseline_raw,
                    promotion_builder='b' * 40, promotion_run='789', now=NOW)

    def prepare(self, arguments):
        with mock.patch.object(ADMISSION.VERIFIER, 'verify_historical_archive', return_value=self.baseline), \
             mock.patch.object(ADMISSION.VERIFIER, 'verify', return_value=self.candidate):
            return ADMISSION.prepare(**arguments)

    def test_archive_identity_stays_stable_for_repeat_soft_admission(self):
        args = self.arguments(); first = self.prepare(args)
        self.assertEqual(first['mode'], 'root-manifest')
        self.assertEqual(first['force'], 'soft')
        self.assertEqual(first['iosForce'], 'soft')
        self.assertEqual(first['legacyClientForce'], 'soft')
        self.assertNotIn('percent', first)
        self.assertNotIn('salt', first)
        self.assertNotIn('rollout', first)
        self.assertEqual(first['archiveIdentity']['buildRunId'], '123')
        self.assertEqual(first['promotion']['runId'], '789')
        result = self.prepare({**args, 'previous': first, 'initial_root': None, 'promotion_run': '790'})
        self.assertEqual(result['archiveIdentity'], first['archiveIdentity'])
        self.assertEqual(result['archiveBindingSha256'], first['archiveBindingSha256'])
        self.assertEqual(result['force'], 'soft')

    def test_archive_identity_drift_and_failed_build_are_rejected(self):
        args = self.arguments()
        for field, value in (('builderCommit', 'b' * 40), ('buildRunId', '789'), ('sourceCommit', '0' * 40),
                             ('sourceAttestationRunId', ''), ('signedArchiveSha256', '0' * 64), ('payloadCount', 9)):
            binding = json.loads(args['binding_raw']); binding[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.prepare({**args, 'binding_raw': json.dumps(binding).encode()})
        for field, value in (('conclusion', 'failure'), ('status', 'in_progress'), ('event', 'pull_request'),
                             ('head_branch', 'master'), ('path', '.github/workflows/promote-update-rollout.yml')):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.prepare({**args, 'build_run': {**args['build_run'], field: value}})
        binding = json.loads(args['binding_raw']); binding['payloads'].pop()
        with self.assertRaises(ValueError):
            self.prepare({**args, 'binding_raw': json.dumps(binding).encode()})

    def test_initial_root_identity_and_force_binding_are_required(self):
        args = self.arguments(); first = self.prepare(args)
        for changes in ({'force': 'wide'}, {'force_reason': 'security'},
                        {'initial_root': b'other'}, {'version': '1.3.175'}, {'promotion_run': 'not-a-run'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.prepare({**args, **changes})
        for field, value in (('archiveBindingSha256', '0' * 64), ('baseline', {}), ('archiveIdentity', {}),
                             ('percent', True), ('mode', 'network-ip')):
            previous = copy.deepcopy(first); previous[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.prepare({**args, 'previous': previous})
        previous = copy.deepcopy(first); previous['candidate']['version'] = '1.3.170'
        with self.assertRaisesRegex(ValueError, 'does not match this archive'):
            self.prepare({**args, 'previous': previous})

    def test_global_hard_admission_forces_every_platform(self):
        self.candidate['force'] = 'hard'
        self.candidate['forceReason'] = 'security'
        self.candidate.pop('forcePlatforms', None)
        self.candidate.pop('platformForce', None)
        args = self.arguments()
        result = self.prepare({**args, 'force': 'hard', 'force_reason': 'security'})
        self.assertEqual(result['force'], 'hard')
        self.assertEqual(result['iosForce'], 'hard')
        self.assertEqual(result['legacyClientForce'], 'hard')
        self.assertEqual(result['forceReason'], 'security')
        self.assertNotIn('forcePlatforms', result)
        self.assertNotIn('platformForce', result)

    def test_selective_platform_force_keeps_ios_a_prompt(self):
        names = sorted(name for name in self.candidate['platforms'] if not str(name).startswith('ios'))
        self.candidate['force'] = 'soft'
        self.candidate['platformForce'] = 'hard'
        self.candidate['forceReason'] = 'security'
        self.candidate['forcePlatforms'] = names
        args = self.arguments()
        result = self.prepare({**args, 'force': 'hard', 'force_reason': 'security'})
        self.assertEqual(result['force'], 'hard')
        self.assertEqual(result['iosForce'], 'soft')
        self.assertEqual(result['legacyClientForce'], 'soft')
        self.assertEqual(result['platformForce'], 'hard')
        self.assertEqual(result['forceReason'], 'security')
        self.assertEqual(result['forcePlatforms'], names)
        self.assertNotIn('ios', result['forcePlatforms'])

    def test_duplicate_binding_keys_rejected(self):
        with self.assertRaises(ValueError):
            ADMISSION.read_json(b'{"buildRunId":"123","buildRunId":"789"}')


if __name__ == '__main__':
    unittest.main()
