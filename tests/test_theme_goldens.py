"""The developer golden job binds pixels to a reviewed SHA without release power."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/theme-goldens.yml'
REQUIRED = (
    'runs-on: ubuntu-24.04',
    '[[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]]',
    '[[ "$SOURCE_BRANCH" = product/w2-link || "$SOURCE_BRANCH" = master ]]',
    'test "$(git rev-parse HEAD)" = "$SOURCE_SHA"',
    'git merge-base --is-ancestor "$SOURCE_SHA" "refs/remotes/origin/$SOURCE_BRANCH"',
    'persist-credentials: false',
    'flutter-version: \'3.47.5\'',
    'flutter pub get --enforce-lockfile',
    '--update-goldens --dart-define=YUELINK_THEME_GOLDENS=true',
    '--machine --dart-define=YUELINK_THEME_GOLDENS=true',
    "assert {p.name for p in images} == expected",
    "assert events[-1].get('success') is True",
    'assert len(passed) >= 29',
    "'sourceSha': os.environ['SOURCE_SHA']",
    'hashlib.sha256(p.read_bytes()).hexdigest()',
    'subject-path: evidence/theme-goldens.json',
)

def issues(text):
    out = [marker for marker in REQUIRED if marker not in text]
    if text.count('persist-credentials: false') != 1:
        out.append('checkout inventory')
    capture, provenance = text.split('  provenance:', 1)
    if 'id-token: write' in capture or 'attestations: write' in capture:
        out.append('candidate code must not receive provenance permissions')
    if 'SRC_DEPLOY_KEY' in provenance or 'actions/checkout' in provenance:
        out.append('provenance job must consume only this run evidence')
    for forbidden in ('R2_ACCESS', 'R2_SECRET', 'rclone', 'release.sh', 'update.json -', 'environment: yuelink-build'):
        if forbidden in text:
            out.append(forbidden)
    return out

class ThemeGoldenContract(unittest.TestCase):
    def test_contract(self):
        self.assertEqual(issues(WORKFLOW.read_text()), [])

    def test_every_deleted_identity_or_evidence_gate_is_rejected(self):
        text = WORKFLOW.read_text()
        self.assertGreaterEqual(len(REQUIRED), 15)
        for marker in REQUIRED:
            with self.subTest(marker=marker):
                self.assertTrue(issues(text.replace(marker, '', 1)))

    def test_release_authority_cannot_leak_into_visual_job(self):
        text = WORKFLOW.read_text()
        self.assertTrue(issues(text.replace('  capture:\n', '  capture:\n    permissions:\n      id-token: write\n', 1)))
        self.assertTrue(issues(text + '\n# R2_SECRET\n'))

if __name__ == '__main__':
    unittest.main()
