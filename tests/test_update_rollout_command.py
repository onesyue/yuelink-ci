"""Execute real plan-write shell against offline command boundaries and failures."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

AWS_STUB = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
root=pathlib.Path(os.environ['FIXTURE_ROOT']); args=sys.argv[1:]
def arg(name): return args[args.index(name)+1]
operation=args[1]; key=arg('--key'); bucket=arg('--bucket')
with (root/'calls').open('a') as log:
 log.write(json.dumps({'operation':operation,'key':key,'bucket':bucket,'identity':os.environ['AWS_ACCESS_KEY_ID'],'args':args})+'\n')
if operation=='get-object':
 target=pathlib.Path(args[args.index('--key')+2])
 if bucket=='fixture-plan-bucket':
  if not (root/'remote-plan').exists():
   code=os.environ.get('GET_ERROR','NoSuchKey')
   print('An error occurred ('+code+') when calling the GetObject operation: fixture',file=sys.stderr);sys.exit(254)
  target.write_bytes((root/'remote-plan').read_bytes())
  print(json.dumps({'ETag':'"'+'e'*32+'"'}))
 else:
  if key.endswith('/promotion-binding.json'):
   target.write_text(json.dumps({'buildRunId':'123','builderCommit':'a'*40,'sourceCommit':'c'*40,'sourceAttestationRunId':'456'}))
  else: target.write_text('{}')
  print('{}')
elif operation=='put-object':
 assert bucket=='fixture-plan-bucket' and key=='stable/network-ip.json'
 expected=['--if-none-match','*'] if os.environ['EXPECTED_PLAN_ETAG']=='absent' else ['--if-match','"'+'e'*32+'"']
 assert arg(expected[0])==expected[1]
 if os.environ.get('CAS_FAIL')=='1': sys.exit(12)
 if os.environ.get('NULL_PUT')=='1': print('null');sys.exit(0)
 (root/'remote-plan').write_bytes(pathlib.Path(arg('--body')).read_bytes())
 if os.environ.get('BAD_READBACK')=='1': (root/'remote-plan').write_text('changed')
 print(json.dumps({'ETag':'"'+'b'*32+'"'}))
else: sys.exit(90)
'''


class RolloutCommandTests(unittest.TestCase):
    def execute(self, *, existing=False, changes=None):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); scripts=root/'scripts'; scripts.mkdir(); bin_dir=root/'bin';bin_dir.mkdir()
            shutil.copyfile(ROOT/'scripts/promote-update-rollout.sh',scripts/'promote-update-rollout.sh')
            (bin_dir/'aws').write_text(AWS_STUB);(bin_dir/'aws').chmod(0o755)
            (scripts/'gh-read-retry.py').write_text("import json,sys\nassert sys.argv[1:]==['api','repos/onesyue/yuelink-ci/actions/runs/123']\nprint('{}')\n")
            (scripts/'prepare-update-rollout.py').write_text(
                "import os,pathlib,sys\n"
                "args=sys.argv[1:]; root=pathlib.Path(os.environ['FIXTURE_ROOT'])\n"
                "assert args[args.index('--promotion-builder')+1]=='b'*40\n"
                "assert args[args.index('--promotion-run')+1]=='789'\n"
                "(root/'prepared').write_text('yes')\n"
                "if os.environ.get('ADMISSION_FAIL')=='1':sys.exit(19)\n"
                "(pathlib.Path(args[args.index('--directory')+1])/'next-plan.json').write_text('{\"fixture\":\"admitted plan\"}')\n")
            (scripts/'verify-source-attestation.sh').write_text('''#!/usr/bin/env bash
set -eu
[ "$1" = v1.3.173 ] && [ "$2" = "$(printf 'c%.0s' {1..40})" ] && [ "$3" = "$(printf 'a%.0s' {1..40})" ] && [ "$4" = 456 ]
printf checked > "$FIXTURE_ROOT/source-checked"
[ "${SOURCE_FAIL:-0}" != 1 ]
''')
            (scripts/'verify-release-closure.py').write_text(
                "import os,pathlib,sys\nroot=pathlib.Path(os.environ['FIXTURE_ROOT'])\n"
                "assert (root/'source-checked').exists()\n"
                "(root/'closure-checked').write_text('yes')\n"
                "raise SystemExit(17 if os.environ.get('CLOSURE_FAIL')=='1' else 0)\n")
            if existing:(root/'remote-plan').write_text('{}')
            env={**os.environ,'PATH':str(bin_dir)+os.pathsep+os.environ['PATH'],'FIXTURE_ROOT':str(root),
                 'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REF':'refs/heads/master','GITHUB_SHA':'b'*40,'GITHUB_RUN_ID':'789',
                 'GITHUB_STEP_SUMMARY':str(root/'summary'),'ROLLOUT_CONFIGURED':'network-ip-reviewed',
                 'NETWORK_IP_ACKNOWLEDGEMENT':'network IPs are not installations','ROLLOUT_VERSION':'1.3.173',
                 'ROLLOUT_BASELINE_VERSION':'1.3.172','ROLLOUT_PERCENT':'10','EXPECTED_PLAN_ETAG':'e'*32 if existing else 'absent',
                 'ROLLOUT_PLAN_BUCKET':'fixture-plan-bucket','ROLLOUT_ARCHIVE_ACCESS_KEY_ID':'archive-read-fixture',
                 'ROLLOUT_ARCHIVE_SECRET_ACCESS_KEY':'synthetic-not-a-credential','ROLLOUT_PLAN_ACCESS_KEY_ID':'plan-write-fixture',
                 'ROLLOUT_PLAN_SECRET_ACCESS_KEY':'synthetic-not-a-credential',
                 'ROLLOUT_ARCHIVE_ENDPOINT':'https://'+'1'*32+'.r2.cloudflarestorage.com',
                 'ROLLOUT_PLAN_ENDPOINT':'https://'+'2'*32+'.r2.cloudflarestorage.com',**(changes or {})}
            result=subprocess.run(['bash',str(scripts/'promote-update-rollout.sh')],env=env,capture_output=True,text=True)
            calls=[json.loads(line) for line in (root/'calls').read_text().splitlines()] if (root/'calls').exists() else []
            summary=(root/'summary').read_text() if (root/'summary').exists() else ''
            return result,calls,summary

    def test_real_shell_only_writes_plan_after_original_proofs(self):
        for existing in (False,True):
            result,calls,summary=self.execute(existing=existing)
            self.assertEqual(result.returncode,0,result.stderr)
            writes=[call for call in calls if call['operation']=='put-object']
            self.assertEqual(len(writes),1)
            self.assertEqual((writes[0]['bucket'],writes[0]['key'],writes[0]['identity']),
                             ('fixture-plan-bucket','stable/network-ip.json','plan-write-fixture'))
            for call in calls:
                if call['bucket']=='yuelink-dist':
                    self.assertEqual(call['operation'],'get-object');self.assertEqual(call['identity'],'archive-read-fixture')
            self.assertIn('not an installation percentage',summary)
            self.assertIn('Original archive build run: 123; source-attestation run: 456',summary)
            self.assertIn('Independent plan promotion run: 789',summary)

    def test_invalid_configuration_or_mode_never_reaches_storage(self):
        for changes in ({'GITHUB_REF':'refs/tags/v1.3.173'},{'GITHUB_EVENT_NAME':'push'},
                        {'ROLLOUT_CONFIGURED':''},{'NETWORK_IP_ACKNOWLEDGEMENT':''},
                        {'ROLLOUT_PERCENT':'101'},{'ROLLOUT_VERSION':'master'},
                        {'ROLLOUT_PLAN_BUCKET':'yuelink-dist'},
                        {'ROLLOUT_PLAN_ACCESS_KEY_ID':'archive-read-fixture'},
                        {'ROLLOUT_ARCHIVE_SECRET_ACCESS_KEY':'','R2_APP_KEY':'legacy-must-not-be-used'}):
            with self.subTest(changes=changes):
                result,calls,_=self.execute(changes=changes)
                self.assertNotEqual(result.returncode,0);self.assertEqual(calls,[])

    def test_unknown_missing_state_and_failed_proofs_never_write(self):
        for changes in ({'GET_ERROR':'AccessDenied'},{'ADMISSION_FAIL':'1'},
                        {'SOURCE_FAIL':'1'},{'CLOSURE_FAIL':'1'}):
            with self.subTest(changes=changes):
                result,calls,summary=self.execute(changes=changes)
                self.assertNotEqual(result.returncode,0);self.assertEqual(summary,'')
                self.assertFalse(any(call['operation']=='put-object' for call in calls))
        result,calls,_=self.execute(existing=True,changes={'EXPECTED_PLAN_ETAG':'f'*32})
        self.assertNotEqual(result.returncode,0);self.assertEqual(len(calls),1)

    def test_failed_cas_null_receipt_and_readback_are_never_success_or_retried(self):
        for changes in ({'CAS_FAIL':'1'},{'NULL_PUT':'1'},{'BAD_READBACK':'1'}):
            with self.subTest(changes=changes):
                result,calls,summary=self.execute(existing=True,changes=changes)
                self.assertNotEqual(result.returncode,0);self.assertEqual(summary,'')
                self.assertEqual(sum(call['operation']=='put-object' for call in calls),1)

    def test_manual_workflow_has_no_signing_build_or_legacy_credentials(self):
        text=(ROOT/'.github/workflows/promote-update-rollout.yml').read_text()
        for forbidden in ('pull_request:','push:','UPDATE_MANIFEST','secrets.R2_KEY_ID','secrets.R2_APP_KEY',
                          'id-token:','packages:','flutter','promote_in_pipeline','promote-root','wrangler deploy'):
            self.assertNotIn(forbidden,text)
        self.assertIn("github.ref == 'refs/heads/master'",text)
        self.assertIn('environment: yuelink-rollout',text)
        self.assertIn('actions: read',text)
        self.assertEqual(text.count('        required: true'),5)
        self.assertIn('bash scripts/promote-update-rollout.sh',text)


if __name__=='__main__':unittest.main()
