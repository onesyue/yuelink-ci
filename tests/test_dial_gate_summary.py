"""Execute the release summary against complete and malformed report sets."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ("windows", "macos", "linux", "android", "ios")
PROTOCOLS = ("vless-reality-tfo", "vless-reality", "vless-ws-tls", "hysteria2")


class DialGateSummaryTests(unittest.TestCase):
    def reports(self) -> dict:
        return {
            platform: {
                "platform": platform,
                "pass": True,
                "schema_version": 2,
                "rounds": 5,
                "samples_per_round": 20,
                "warmup": 1,
                "baseline": {"revision": "a" * 40, "core_revision": "b" * 40},
                "candidate": {"revision": "c" * 40, "core_revision": "d" * 40},
                "harness_revision": "c" * 40,
                "go_version": "go1.27.4",
                "build_tags": "with_gvisor",
                "rows": [
                    {
                        "protocol": protocol,
                        "pass": True,
                        "baseline_samples": 100,
                        "candidate_samples": 100,
                        "baseline_ok": 100,
                        "candidate_ok": 100,
                        "rounds": [
                            {
                                "round": r,
                                "baseline_samples": 20,
                                "candidate_samples": 20,
                                "success_pass": True,
                            }
                            for r in range(5)
                        ],
                        "baseline_success_rate": 1,
                        "candidate_success_rate": 1,
                        "baseline_p95_ms": 5,
                        "candidate_p95_ms": 5,
                    }
                    for protocol in PROTOCOLS
                ],
            }
            for platform in PLATFORMS
        }

    def run_summary(
        self,
        reports: dict,
        expected_source: str = "",
        require_source: bool = False,
        expected_baseline: str = "a" * 40,
    ) -> subprocess.CompletedProcess:
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        body = workflow.split("- name: Write the platform table\n", 1)[1]
        script = textwrap.dedent(
            body.split("python3 - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, report in reports.items():
                path = root / "dial-reports" / ("dial-gate-" + name)
                path.mkdir(parents=True)
                (path / "gate.json").write_text(json.dumps(report))
            return subprocess.run(
                [sys.executable, "-c", script],
                cwd=root,
                env={
                    **os.environ,
                    "GITHUB_STEP_SUMMARY": str(root / "summary"),
                    "GO_VERSION": "1.27.4",
                    "EXPECTED_SOURCE_COMMIT": expected_source,
                    "EXPECTED_BASELINE_SOURCE": expected_baseline,
                    "REQUIRE_SOURCE_COMMIT": "true" if require_source else "false",
                },
                capture_output=True,
                text=True,
                check=False,
            )

    def test_complete_twenty_protocol_rows_pass(self) -> None:
        reports = self.reports()
        # The historical Windows TFO baseline is legitimately all failures.
        row = reports["windows"]["rows"][0]
        row.update(baseline_ok=0, baseline_success_rate=0, baseline_p95_ms=None)
        result = self.run_summary(reports)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("| pass |"), 21)  # header plus 20 rows

    def test_incomplete_or_malformed_evidence_fails(self) -> None:
        cases = []
        for rows in ([], self.reports()["linux"]["rows"][:-1]):
            reports = self.reports()
            reports["linux"]["rows"] = rows
            cases.append(("missing protocols", reports))
        reports = self.reports()
        del reports["ios"]
        cases.append(("missing platform", reports))
        reports = self.reports()
        reports["linux"]["rows"][1] = copy.deepcopy(reports["linux"]["rows"][0])
        cases.append(("duplicate protocol", reports))
        reports = self.reports()
        reports["linux"]["rows"][0]["protocol"] = "unknown"
        cases.append(("unknown protocol", reports))
        reports = self.reports()
        reports["linux"]["platform"] = "windows"
        cases.append(("wrong artifact platform", reports))
        for field, value in (
            ("pass", "true"),
            ("candidate_samples", 0),
            ("baseline_samples", True),
            ("candidate_samples", 19),
        ):
            reports = self.reports()
            reports["linux"]["rows"][0][field] = value
            cases.append((field, reports))
        reports = self.reports()
        reports["linux"]["pass"] = "true"
        cases.append(("nonboolean verdict", reports))
        reports = self.reports()
        reports["linux"]["rows"][0]["pass"] = False
        cases.append(("failed row", reports))
        for name, reports in cases:
            with self.subTest(case=name):
                self.assertNotEqual(self.run_summary(reports).returncode, 0)

    def test_fixed_round_metadata_and_identities_fail_closed(self) -> None:
        changes = [
            lambda d: d.pop("schema_version"),
            lambda d: d.update(rounds=True),
            lambda d: d.update(samples_per_round=19),
            lambda d: d.update(warmup=0),
            lambda d: d.update(harness_revision="e" * 40),
            lambda d: d.update(go_version="go1.27.3"),
            lambda d: d.update(build_tags=""),
            lambda d: d["baseline"].update(revision="e" * 40),
            lambda d: d["candidate"].update(core_revision="e" * 40),
            lambda d: d["candidate"].update(revision="invalid"),
            lambda d: d["rows"][0].update(candidate_samples=99),
            lambda d: d["rows"][0].update(candidate_samples=101),
            lambda d: d["rows"][0]["rounds"].pop(),
            lambda d: d["rows"][0]["rounds"][0].update(round=1),
            lambda d: d["rows"][0]["rounds"][0].update(round=True),
            lambda d: d["rows"][0]["rounds"][0].update(candidate_samples=19),
            lambda d: d["rows"][0]["rounds"][0].update(baseline_samples=True),
            lambda d: d["rows"][0]["rounds"][0].update(success_pass=False),
            lambda d: d["rows"][0]["rounds"][0].update(success_pass=1),
        ]
        for index, change in enumerate(changes):
            with self.subTest(case=index):
                reports = self.reports()
                change(reports["linux"])
                self.assertNotEqual(self.run_summary(reports).returncode, 0)

    def test_release_candidate_must_match_the_planned_source(self) -> None:
        self.assertEqual(self.run_summary(self.reports(), "c" * 40, True).returncode, 0)
        self.assertNotEqual(
            self.run_summary(self.reports(), "e" * 40, True).returncode, 0
        )

    def test_release_requires_valid_planned_identity(self) -> None:
        for expected in ("", "master", "G" * 40, "c" * 39):
            with self.subTest(expected=expected):
                self.assertNotEqual(
                    self.run_summary(self.reports(), expected, True).returncode, 0
                )

    def test_frozen_baseline_binding_is_required_for_every_event(self) -> None:
        for require_source in (False, True):
            for expected in ("", "master", "G" * 40, "e" * 40):
                with self.subTest(require_source=require_source, expected=expected):
                    self.assertNotEqual(
                        self.run_summary(
                            self.reports(), "c" * 40, require_source, expected
                        ).returncode,
                        0,
                    )
