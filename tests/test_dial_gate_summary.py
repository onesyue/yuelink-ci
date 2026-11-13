"""Execute the release summary against complete and malformed report sets."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ('windows', 'macos', 'linux', 'android', 'ios')
PROTOCOLS = ('vless-reality-tfo', 'vless-reality', 'vless-ws-tls', 'hysteria2')


class DialGateSummaryTests(unittest.TestCase):
    def reports(self) -> dict:
        return {platform: {'platform': platform, 'pass': True, 'rows': [
            {'protocol': protocol, 'pass': True,
             'baseline_samples': 20, 'candidate_samples': 20,
             'baseline_ok': 20, 'candidate_ok': 20,
             'baseline_success_rate': 1, 'candidate_success_rate': 1,
             'baseline_p95_ms': 5, 'candidate_p95_ms': 5}
            for protocol in PROTOCOLS]} for platform in PLATFORMS}

    def run_summary(self, reports: dict, invalid_identity: str = "") -> subprocess.CompletedProcess:
        workflow = (ROOT / '.github/workflows/build.yml').read_text()
        body = workflow.split('- name: Write the platform table\n', 1)[1]
        script = textwrap.dedent(body.split("python3 - <<'PY'\n", 1)[1].split('\n          PY', 1)[0])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, report in reports.items():
                path = root / 'dial-reports' / ('dial-gate-' + name)
                path.mkdir(parents=True)
                (path / 'gate.json').write_text(json.dumps(report))
                for role, revision in (("baseline", "3937d3807609974e82daee1421cadb6003298613"),
                                       ("candidate", "41dc7b09c1a68dc84e3201bcd2d890e51a1d6e87")):
                    raw = {"revision": revision, "role": role, "platform": name}
                    if name == "ios" and role == "candidate" and invalid_identity:
                        if invalid_identity == "missing":
                            continue
                        raw[invalid_identity] = "wrong"
                    (path / f"{role}.json").write_text(json.dumps(raw))
            return subprocess.run([sys.executable, '-c', script], cwd=root,
                                  env={**os.environ, 'GITHUB_STEP_SUMMARY': str(root / 'summary')},
                                  capture_output=True, text=True, check=False)

    def test_complete_twenty_protocol_rows_pass(self) -> None:
        reports = self.reports()
        # The historical Windows TFO baseline is legitimately all failures.
        row = reports['windows']['rows'][0]
        row.update(baseline_ok=0, baseline_success_rate=0, baseline_p95_ms=None)
        result = self.run_summary(reports)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('| pass |'), 21)  # header plus 20 rows

    def test_incomplete_or_malformed_evidence_fails(self) -> None:
        cases = []
        for rows in ([], self.reports()['linux']['rows'][:-1]):
            reports = self.reports(); reports['linux']['rows'] = rows
            cases.append(('missing protocols', reports))
        reports = self.reports(); del reports['ios']; cases.append(('missing platform', reports))
        reports = self.reports(); reports['linux']['rows'][1] = copy.deepcopy(reports['linux']['rows'][0]); cases.append(('duplicate protocol', reports))
        reports = self.reports(); reports['linux']['rows'][0]['protocol'] = 'unknown'; cases.append(('unknown protocol', reports))
        reports = self.reports(); reports['linux']['platform'] = 'windows'; cases.append(('wrong artifact platform', reports))
        for field, value in (('pass', 'true'), ('candidate_samples', 0), ('baseline_samples', True), ('candidate_samples', 19)):
            reports = self.reports(); reports['linux']['rows'][0][field] = value
            cases.append((field, reports))
        reports = self.reports(); reports['linux']['pass'] = 'true'; cases.append(('nonboolean verdict', reports))
        reports = self.reports(); reports['linux']['rows'][0]['pass'] = False; cases.append(('failed row', reports))
        for name, reports in cases:
            with self.subTest(case=name):
                self.assertNotEqual(self.run_summary(reports).returncode, 0)

    def test_control_rejects_wrong_or_missing_source_identity(self) -> None:
        for field in ("revision", "role", "platform", "missing"):
            with self.subTest(field=field):
                self.assertNotEqual(self.run_summary(self.reports(), field).returncode, 0)
