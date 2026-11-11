"""Check every raw and summary report against one frozen source pair."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('dial_identity', ROOT / 'scripts/verify-dial-report-identity.py')
assert SPEC and SPEC.loader
identity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(identity)
CANDIDATE = 'a' * 40
BASELINE = 'b' * 40


class DialReportIdentityTests(unittest.TestCase):
    def write_reports(self, root: Path) -> None:
        for platform in identity.PLATFORMS:
            folder = root / f'dial-gate-{platform}'; folder.mkdir()
            for role, revision in (('candidate', CANDIDATE), ('baseline', BASELINE)):
                (folder / f'{role}.json').write_text(json.dumps({
                    'platform': platform, 'role': role, 'revision': revision,
                    'proxies': dict.fromkeys(identity.PROTOCOLS, {}),
                }))
            (folder / 'gate.json').write_text(json.dumps({
                'platform': platform, 'candidate_revision': CANDIDATE, 'baseline_revision': BASELINE,
            }))

    def test_one_exact_pair_across_all_platforms_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.write_reports(root)
            identity.verify(root, CANDIDATE, BASELINE)

    def test_wrong_or_missing_report_identity_is_rejected(self) -> None:
        for filename, field, value in (
            ('candidate.json', 'revision', 'c' * 40), ('baseline.json', 'revision', CANDIDATE),
            ('candidate.json', 'revision', None), ('candidate.json', 'role', 'baseline'),
            ('candidate.json', 'platform', 'windows'), ('candidate.json', 'proxies', {}),
            ('gate.json', 'candidate_revision', 'c' * 40), ('gate.json', 'baseline_revision', None),
        ):
            with self.subTest(filename=filename, field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); self.write_reports(root)
                path = root / 'dial-gate-linux' / filename
                report = json.loads(path.read_text()); report[field] = value; path.write_text(json.dumps(report))
                with self.assertRaises(ValueError):
                    identity.verify(root, CANDIDATE, BASELINE)

    def test_missing_extra_platform_and_missing_file_are_rejected(self) -> None:
        for mutation in ('extra', 'missing', 'missing_file', 'invalid_sha'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); self.write_reports(root)
                if mutation == 'extra':
                    (root / 'dial-gate-extra').mkdir()
                elif mutation == 'missing':
                    folder = root / 'dial-gate-ios'
                    for path in folder.iterdir(): path.unlink()
                    folder.rmdir()
                elif mutation == 'missing_file':
                    (root / 'dial-gate-linux/candidate.json').unlink()
                with self.assertRaises((ValueError, OSError)):
                    identity.verify(root, 'master' if mutation == 'invalid_sha' else CANDIDATE, BASELINE)
