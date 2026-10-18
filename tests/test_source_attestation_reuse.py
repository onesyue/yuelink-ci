from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE, BUILDER, RUN, TAG = 'a' * 40, 'b' * 40, '123', 'v1.3.139'
GATES = ['source-master-ancestry', 'android-release-signing-contract', 'unreleased-dart-format',
         'flutter-analyze', 'architecture-imports', 'cocoapods-residue', 'workflow-policy',
         'security-scan', 'wintun-bundle', 'release-metadata', 'manifest-schema',
         'flutter-tests-floor-2044', 'gitleaks-full-history', 'govulncheck-core-service',
         'macos-integration', 'windows-durability', 'windows-native-core-runtime']


class SharedVerifierBehaviorTests(unittest.TestCase):
    """Actual shell/jq/read-policy consumer; the remote GH service is controlled."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.bin = self.root / 'bin'; self.bin.mkdir()
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        FIXTURE=str(self.root), PYTHON=sys.executable)
        self.run_metadata = dict(id=123, workflow_id=789, name='Source attestation',
                        display_title='Source attestation ' + SOURCE,
                        path='.github/workflows/source-attestation.yml', event='workflow_dispatch',
                        status='completed', conclusion='success', head_sha=BUILDER,
                        head_branch='master', repository={'full_name': 'onesyue/yuelink-ci'}, run_attempt=1)
        self.proof = dict(schemaVersion=2, sourceRepository='onesyue/yuelink', sourceSha=SOURCE,
                          releaseTag=TAG, builderRepository='onesyue/yuelink-ci', workflowSha=BUILDER,
                          runId=RUN, runAttempt='1', gates=GATES,
                          reuse={'schemaVersion': 1, **{
                              kind: dict(schemaVersion=1, kind=kind, sourceSha=SOURCE,
                                         workflowSha=BUILDER, runId=RUN, runAttempt='1',
                                         gates=gates, toolchain={'version': 'fixture'})
                              for kind, gates in [('flutter', ['flutter-analyze', 'flutter-tests-coverage']),
                                                  ('service', ['service-go-race'])]}})
        self.write_json('workflow.json', dict(id=789, name='Source attestation',
            path='.github/workflows/source-attestation.yml', state='active'))
        self.write_json('run.json', self.run_metadata); self.write_json('proof.json', self.proof)
        self.env['SIGNED_DIGEST'] = hashlib.sha256((self.root / 'proof.json').read_bytes()).hexdigest()
        for name in ['verify-source-attestation.sh', 'gh-read-retry.py']:
            shutil.copyfile(ROOT / 'scripts' / name, self.root / name)
        gh = self.bin / 'gh'
        gh.write_text('#!' + sys.executable + '\n' + r'''
import hashlib,json,os,pathlib,shutil,sys
p=pathlib.Path(os.environ['FIXTURE']); a=sys.argv[1:]
if a[:1]==['api']:
    name='workflow.json' if '/workflows/' in a[1] else 'run.json'
    print((p/name).read_text()); sys.exit(0)
if a[:2]==['run','download']:
    shutil.copyfile(p/'proof.json', pathlib.Path(a[a.index('--dir')+1])/'source-attestation.json')
    sys.exit(0)
if a[:2]==['attestation','verify']:
    (p/'crypto-args.json').write_text(json.dumps(a))
    expected={'--repo':'onesyue/yuelink-ci',
      '--cert-oidc-issuer':'https://token.actions.githubusercontent.com',
      '--predicate-type':'https://slsa.dev/provenance/v1',
      '--signer-workflow':'onesyue/yuelink-ci/.github/workflows/source-attestation.yml',
      '--source-ref':'refs/heads/master','--source-digest':'b'*40,'--signer-digest':'b'*40}
    okay=all(k in a and a[a.index(k)+1]==v for k,v in expected.items())
    okay=okay and '--deny-self-hosted-runners' in a and os.environ.get('SIGNATURE_FAIL')!='1'
    okay=okay and hashlib.sha256(pathlib.Path(a[2]).read_bytes()).hexdigest()==os.environ['SIGNED_DIGEST']
    if not okay: print('signature verification failed',file=sys.stderr)
    sys.exit(0 if okay else 1)
sys.exit(9)
''', encoding='utf-8'); gh.chmod(0o755)

    def write_json(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding='utf-8')

    def verify(self, *, export=True, builder=BUILDER, source=SOURCE):
        output = self.root / 'export.json'
        output.unlink(missing_ok=True)
        command = ['bash', str(self.root / 'verify-source-attestation.sh'), TAG, source, builder, RUN]
        if export: command += ['--export-proof', str(output)]
        return subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=20)

    def test_exact_v2_proof_is_exported_only_after_signature_validation(self):
        result = self.verify(); self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root/'export.json').read_bytes(), (self.root/'proof.json').read_bytes())
        self.assertTrue((self.root/'crypto-args.json').exists())

    def test_wrong_source_builder_and_failed_run_never_export(self):
        for kwargs in [{'source':'c'*40}, {'builder':'c'*40}]:
            with self.subTest(kwargs=kwargs): self.assertNotEqual(self.verify(**kwargs).returncode,0)
        for field, value in [('conclusion','failure'), ('conclusion','cancelled'),
                             ('status','in_progress'), ('head_branch','other'),
                             ('workflow_id',790), ('run_attempt',2), ('event','push')]:
            with self.subTest(field=field,value=value):
                run=copy.deepcopy(self.run_metadata); run[field]=value; self.write_json('run.json',run)
                self.assertNotEqual(self.verify().returncode,0)
                self.assertFalse((self.root/'export.json').exists())
        self.write_json('run.json',self.run_metadata)

    def test_bad_gate_schema_or_missing_runtime_facts_never_export(self):
        for mutation in ['missing', 'extra', 'empty_toolchain', 'wrong_attempt']:
            with self.subTest(mutation=mutation):
                proof=copy.deepcopy(self.proof)
                item=proof['reuse']['service']
                if mutation=='missing': item['gates']=[]
                elif mutation=='extra': item['gates'].append('core-race')
                elif mutation=='empty_toolchain': item['toolchain']={}
                else: item['runAttempt']='2'
                self.write_json('proof.json',proof)
                self.assertNotEqual(self.verify().returncode,0)
                self.assertFalse((self.root/'export.json').exists())

    def test_valid_legacy_identity_can_be_verified_but_never_reused(self):
        proof=copy.deepcopy(self.proof); proof['schemaVersion']=1; del proof['reuse']
        self.write_json('proof.json',proof)
        self.env['SIGNED_DIGEST']=hashlib.sha256((self.root/'proof.json').read_bytes()).hexdigest()
        self.assertEqual(self.verify(export=False).returncode,0)
        self.assertNotEqual(self.verify(export=True).returncode,0)
        self.assertFalse((self.root/'export.json').exists())

    def test_tampered_subject_and_signature_rejection_never_export(self):
        self.env['SIGNATURE_FAIL']='1'
        self.assertNotEqual(self.verify().returncode,0)
        self.assertFalse((self.root/'export.json').exists())
        self.env.pop('SIGNATURE_FAIL')
        with (self.root/'proof.json').open('a') as stream: stream.write('\n')
        self.assertNotEqual(self.verify().returncode,0)
        self.assertFalse((self.root/'export.json').exists())

    def test_wrong_issuer_or_workflow_policy_is_not_an_approved_read(self):
        verifier=self.root/'verify-source-attestation.sh'; original=verifier.read_text()
        for old,new in [('https://token.actions.githubusercontent.com','https://wrong.example'),
                        ('WORKFLOW_IDENTITY=onesyue/yuelink-ci/.github/workflows/source-attestation.yml',
                         'WORKFLOW_IDENTITY=onesyue/yuelink-ci/.github/workflows/build.yml'),
                        ('--deny-self-hosted-runners','--allow-self-hosted-runners')]:
            with self.subTest(new=new):
                verifier.write_text(original.replace(old,new))
                self.assertNotEqual(self.verify().returncode,0)
                self.assertFalse((self.root/'export.json').exists())
        verifier.write_text(original)


class ReuseWorkflowTests(unittest.TestCase):
    def test_receipts_follow_real_gates_and_are_required_before_signing(self):
        workflow=(ROOT/'.github/workflows/source-attestation.yml').read_text(encoding='utf-8')
        self.assertLess(workflow.index('coverage_ratchet.dart --check'),
                        workflow.index('attested_preflight.py capture --kind flutter'))
        self.assertLess(workflow.index('CGO_ENABLED=1 go test -mod=readonly -race ./...'),
                        workflow.index('attested_preflight.py capture --kind service'))
        attest=workflow[workflow.index('  attest:'):]
        for name in ['source-reuse-flutter-', 'source-reuse-service-']:
            self.assertIn(name,attest)
        self.assertLess(attest.index('Read executed service race receipt'),
                        attest.index('Sign source proof provenance'))
        self.assertIn('reuse: {schemaVersion: 1, flutter: $flutter[0], service: $service[0]}',attest)


if __name__ == '__main__': unittest.main()
