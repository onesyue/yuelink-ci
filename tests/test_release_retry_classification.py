"""Exercise the real retry loops with deterministic verifier/API and sleep stubs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

from test_release_remote_tag_behavior import (
    BUILDER, MESSAGE, TAG, TAG_OBJECT, shell_function,
)

ROOT = Path(__file__).resolve().parents[1]


class RetryClassificationTests(unittest.TestCase):
    def run_loop(self, script, stub, payloads):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for index, payload in enumerate(payloads, 1):
                (path / str(index)).write_text(json.dumps(payload))
            (path / 'count').write_text('0')
            prelude = r'''
set -euo pipefail
next_payload() {
  n=$(cat "$STATE/count")
  n=$((n + 1))
  echo "$n" > "$STATE/count"
  if [ "$n" -gt "$LAST" ]; then n=$LAST; fi
  cat "$STATE/$n"
}
sleep() { echo "$1" >> "$STATE/sleeps"; }
'''
            result = subprocess.run(
                ['/bin/bash', '-c', prelude + stub + '\n' + script],
                env={**os.environ, 'STATE': directory, 'LAST': str(len(payloads)),
                     'TAG': TAG, 'REF_NAME': TAG, 'BUILDER_COMMIT': BUILDER,
                     'TRIGGER_COMMIT': BUILDER, 'PUBLIC_TAG_MESSAGE': MESSAGE,
                     'REPOSITORY': 'onesyue/yuelink-ci', 'tag_object_sha': TAG_OBJECT,
                     'APP_VERSION': '9.8.7'},
                capture_output=True, text=True, timeout=10,
            )
            sleeps = path / 'sleeps'
            return result, int((path / 'count').read_text()), (
                sleeps.read_text().splitlines() if sleeps.exists() else []
            )

    def candidate(self, payloads, status=1):
        from scripts.check_public_build import composed_text

        workflow = composed_text(ROOT / '.github/workflows/build.yml')
        start = workflow.index('          set -euo pipefail', workflow.index(
            '- name: Verify immutable candidate and all release artifacts'))
        end = workflow.index('      # In-pipeline promotion', start)
        stub = '''
dart() {
  next_payload
  if [ "$(cat "$STATE/count")" -ge "$LAST" ] && [ "$SUCCESS" = 1 ]; then return 0; fi
  return STATUS
}
'''.replace('STATUS', str(status)).replace('$SUCCESS', '1' if len(payloads) > 1 and payloads[-1] == [] else '0')
        return self.run_loop(textwrap.dedent(workflow[start:end]), stub, payloads)

    def tag(self, payloads, workflow=False):
        source = (ROOT / 'release.sh').read_text()
        if workflow:
            from scripts.check_public_build import composed_text

            source = (ROOT / "scripts/resolve-release-tag.sh").read_text()
            start = source.index("tag_json=''\n")
            end = source.index("tag_message=", start)
            script = textwrap.dedent(source[start:end])
        else:
            script = '\n'.join(shell_function(source, name) for name in (
                'inspect_remote_public_tag', 'wait_for_verified_remote_public_tag'))
            script += '\nwait_for_verified_remote_public_tag\n'
        stub = '''
gh() {
  case "$*" in
    *git/ref/tags/*) printf '{"object":{"sha":"%s","type":"tag"}}' "TAG_OBJECT" ;;
    *) next_payload ;;
  esac
}
'''.replace('TAG_OBJECT', TAG_OBJECT)
        return self.run_loop(script, stub, payloads)

    @staticmethod
    def proof(reason='valid', **changes):
        result = {'tag': TAG, 'object': {'type': 'commit', 'sha': BUILDER},
                  'message': MESSAGE,
                  'verification': {'verified': reason == 'valid', 'reason': reason}}
        result.update(changes)
        return result

    def test_candidate_404_then_success(self):
        pending = [{'check': 'binary:app.apk', 'ok': False,
                    'detail': 'HTTP 404, content-length=0'}]
        result, calls, sleeps = self.candidate([pending, []])
        self.assertEqual((result.returncode, calls, sleeps), (0, 2, ['15']), result.stderr)

    def test_candidate_integrity_or_unknown_failure_never_sleeps(self):
        for check, detail in [('manifest:signature-schema', 'untrusted'),
                              ('manifest:update.json', 'version mismatch'),
                              ('sidecar:app.sha256', 'mismatch or missing sidecar'),
                              ('binary:app.apk', 'HTTP 403, content-length=0')]:
            with self.subTest(check=check):
                payload = [{'check': check, 'ok': False, 'detail': detail},
                           {'check': 'binary:other.apk', 'ok': False,
                            'detail': 'HTTP 404, content-length=0'}]
                result, calls, sleeps = self.candidate([payload])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((calls, sleeps), (1, []))
        for payload in ([], {}, [{'ok': False}], 'invalid'):
            result, calls, sleeps = self.candidate([payload], status=2)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((calls, sleeps), (1, []))

    def test_candidate_exhaustion_has_seven_sleeps(self):
        result, calls, sleeps = self.candidate([[{'check': 'binary:app.apk',
            'ok': False, 'detail': 'HTTP 404, content-length=-1'}]])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((calls, sleeps), (8, ['15'] * 7))

    def test_both_tag_loops_pending_then_valid(self):
        for workflow in (False, True):
            for reason in ('gpgverify_unavailable', 'gpgverify_error'):
                result, calls, sleeps = self.tag([self.proof(reason), self.proof()], workflow)
                self.assertEqual((result.returncode, calls, sleeps), (0, 2, ['5']), result.stderr)

    def test_both_tag_loops_reject_content_and_identity_first_round(self):
        bad = [self.proof(reason) for reason in (
            'invalid', 'unsigned', 'unknown_key', 'bad_email', 'malformed_signature',
            'unverified_email', 'expired_key', 'unknown', 'pending')]
        bad += [self.proof('gpgverify_error', tag='v0.0.0'),
                self.proof('gpgverify_error', object={'type': 'commit', 'sha': 'c' * 40}),
                self.proof('gpgverify_error', object={'type': 'tag', 'sha': BUILDER})]
        for workflow in (False, True):
            for payload in bad:
                with self.subTest(workflow=workflow, payload=payload):
                    result, calls, sleeps = self.tag([payload], workflow)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual((calls, sleeps), (1, []))

    def test_both_tag_loops_exhaustion_has_eleven_sleeps(self):
        for workflow in (False, True):
            result, calls, sleeps = self.tag([self.proof('gpgverify_unavailable')], workflow)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((calls, sleeps), (12, ['5'] * 11))

    def test_release_api_only_retries_explicit_404(self):
        source = (ROOT / 'release.sh').read_text()
        script = '\n'.join(shell_function(source, name) for name in (
            'inspect_remote_public_tag', 'wait_for_verified_remote_public_tag'))
        script += '\nwait_for_verified_remote_public_tag\n'
        stub = "gh() { next_payload; return 1; }\n"
        for status, expected_calls, expected_sleeps in (
            ('404', 12, ['5'] * 11), ('403', 1, []), ('500', 1, []),
        ):
            result, calls, sleeps = self.run_loop(script, stub, [{'status': status}])
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((calls, sleeps), (expected_calls, expected_sleeps))

    def test_sync_overlay_reconstructs_workflow(self):
        # The retry classification now lives in the called workflows.
        # Reverse then reapply the overlay in a temporary directory.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = ('resolve-release-tag.sh', 'publish.yml')
            for name in names:
                (root / name).write_bytes(
                    (ROOT / ('scripts' if name.endswith('.sh') else '.github/workflows') / name).read_bytes())
            patch = (ROOT / 'scripts/public-release-retry.patch').read_bytes()
            for direction in ('--reverse', '--forward'):
                subprocess.run(
                    ['patch', '--batch', direction, '-p0'],
                    input=patch, cwd=root, capture_output=True, check=True)
            for name in names:
                self.assertEqual(
                    (root / name).read_bytes(),
                    (ROOT / ('scripts' if name.endswith('.sh') else '.github/workflows') / name).read_bytes())
            self.assertIn(
                'gpgverify_unavailable',
                (ROOT / 'scripts/resolve-release-tag.sh').read_text())


if __name__ == '__main__':
    unittest.main()
