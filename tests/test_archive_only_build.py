"""Run the actual final signing step and mirror overlay without signing or R2."""
from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/build.yml'
SPEC = importlib.util.spec_from_file_location('archive_overlay', ROOT / 'scripts/apply-archive-only-overlay.py')
assert SPEC and SPEC.loader
OVERLAY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OVERLAY)


class ArchiveOnlyBuildTests(unittest.TestCase):
    def step(self) -> str:
        return WORKFLOW.read_text().split('- name: Sign immutable release archive\n', 1)[1].split('\n  # ', 1)[0]

    def run_step(self, legacy: bool = False, *, code: int = 0, good_receipt: bool = True):
        body = textwrap.dedent(self.step().split('run: |\n', 1)[1])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'scripts/ci/promote_in_pipeline.sh'; script.parent.mkdir(parents=True)
            # The legacy private entrypoint validates $1 as a version before
            # credential access or external side effects. The sentinel models
            # the forbidden continuation, not a signing implementation.
            if legacy:
                script.write_text('''#!/usr/bin/env bash
set -Eeuo pipefail
version="${1:-}"
if ! [[ "$version" =~ ^[0-9]+\\.[0-9]+\\.[0-9]+$ ]]; then
  exit 2
fi
touch forbidden-root-write
''')
            else:
                script.write_text('''#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\\n' "$@" > calls
[ "$#" -eq 2 ] && [ "$1" = archive-only ] && [ "$2" = 9.8.7 ]
exit "${FAKE_ARCHIVE_EXIT}"
''')
            receipt = root / 'receipt.json'
            receipt.write_text(json.dumps({'tag': 'v9.8.7', 'source_commit': 'a' * 40,
                                          'repository': 'onesyue/yuelink-ci',
                                          'downloaded_archive_and_sidecar_verified': good_receipt}))
            result = subprocess.run(['bash', '-c', body], cwd=root, capture_output=True, text=True,
                                    env={**os.environ, 'UPDATE_MANIFEST_ED25519_PRIVATE_KEY_B64': 'fixture-not-a-key',
                                         'NATIVE_MEDIA_SOURCE_RECEIPT': str(receipt), 'APP_VERSION': '9.8.7',
                                         'TAG': 'v9.8.7', 'ATTESTED_SOURCE_COMMIT': 'a' * 40,
                                         'FAKE_ARCHIVE_EXIT': str(code)})
            calls = (root / 'calls').read_text().splitlines() if (root / 'calls').exists() else []
            return result, calls, (root / 'forbidden-root-write').exists()

    def test_real_step_uses_mode_first_and_propagates_archive_failure(self):
        for code in (0, 17):
            result, calls, wrote = self.run_step(code=code)
            self.assertEqual(result.returncode, code, result.stderr)
            self.assertEqual(calls, ['archive-only', '9.8.7'])
            self.assertFalse(wrote)

    def test_historical_entrypoint_rejects_before_root_side_effect(self):
        result, _, wrote = self.run_step(legacy=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(wrote)

    def test_source_receipt_still_blocks_archive_and_incomplete_candidate_is_skipped(self):
        result, calls, wrote = self.run_step(good_receipt=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
        self.assertFalse(wrote)
        self.assertIn("if: env.SKIP_MANIFEST != '1' && needs.preflight.outputs.tag_kind == 'stable'", self.step())

    def test_build_has_no_automatic_pr_trigger(self):
        def require_release_events(text):
            events = text.split('\non:\n', 1)[1].split('\nconcurrency:', 1)[0]
            self.assertEqual(set(re.findall(r'^  (\w+):', events, re.M)), {'push', 'workflow_dispatch'})
        workflow = WORKFLOW.read_text()
        require_release_events(workflow)
        with self.assertRaises(AssertionError):
            require_release_events(workflow.replace('\non:\n', '\non:\n  pull_request:\n', 1))
        policy = (ROOT / '.github/workflows/policy-ci.yml').read_text()
        self.assertIn('  pull_request:', policy)

    def test_archive_overlay_preserves_toolchain_identity(self):
        for version in ('1.27.3', '1.27.4'):
            source = "env:\n  GO_VERSION: '" + version + "'\n" + OVERLAY.LEGACY_STEP + '\n' + OVERLAY.LEGACY_CALL + '\n'
            self.assertIn("GO_VERSION: '" + version + "'", OVERLAY.transform(source))

    def test_real_overlay_preserves_generated_bytes_and_rejects_anchor_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'build.yml'
            generated = WORKFLOW.read_text()
            canonical = generated.replace(OVERLAY.ARCHIVE_CALL, OVERLAY.LEGACY_CALL).replace(OVERLAY.ARCHIVE_STEP, OVERLAY.LEGACY_STEP)
            path.write_text(canonical)
            OVERLAY.apply(path)
            self.assertEqual(path.read_text(), generated)
            OVERLAY.apply(path)
            self.assertEqual(path.read_text(), generated)
            for changed in (canonical.replace(OVERLAY.LEGACY_CALL, ''),
                            canonical + '\n' + OVERLAY.LEGACY_CALL + '\n',
                            canonical.replace(OVERLAY.LEGACY_STEP, '      - name: Unknown archive policy'),
                            canonical.replace(OVERLAY.LEGACY_STEP, OVERLAY.ARCHIVE_STEP + ' and promote'),
                            canonical.replace(OVERLAY.LEGACY_CALL, OVERLAY.LEGACY_CALL + ' promote-root')):
                path.write_text(changed)
                with self.assertRaises(ValueError):
                    OVERLAY.apply(path)
                self.assertEqual(path.read_text(), changed)
        self.assertIn('python3 "$SCRIPT_DIR/scripts/apply-archive-only-overlay.py" "$GENERATED"',
                      (ROOT / 'sync-build.sh').read_text())


if __name__ == '__main__':
    unittest.main()
