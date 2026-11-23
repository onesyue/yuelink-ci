"""Regression tests for decisions made from the pinned scanner's JSON."""

import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/scan-lockfiles.py"
SCAN = runpy.run_path(SCRIPT)
PACKAGE = {"name": "example.test/core", "ecosystem": "Go", "version": "v1.2.3"}


def report(score, events, *, name=PACKAGE["name"], range_type="SEMVER"):
    return {"results": [{"packages": [{
        "package": PACKAGE,
        "groups": [{"ids": ["TEST-1"], "max_severity": score}],
        "vulnerabilities": [{
            "id": "TEST-1",
            "affected": [{
                "package": {"name": name, "ecosystem": "Go"},
                "ranges": [{"type": range_type, "events": events}],
            }],
        }],
    }]}]}


class LockfileScanTests(unittest.TestCase):
    def test_only_fixable_high_and_critical_findings_block(self):
        for score, level, expected in (
            ("9.0", "CRITICAL", 1), ("7.0", "HIGH", 1), ("6.9", "MEDIUM", 0),
            ("3.9", "LOW", 0), ("", "UNKNOWN", 0),
        ):
            with self.subTest(score=score):
                counts, blocking = SCAN["summarize"](report(score, [{"fixed": "1.2.4"}]))
                self.assertEqual(counts[level], 1)
                self.assertEqual(blocking, expected)

    def test_fix_must_be_a_newer_package_version(self):
        for kwargs, events in (
            ({}, [{"last_affected": "1.2.3"}]),
            ({}, [{"fixed": "1.2.2"}, {"introduced": "1.2.3"}]),
            ({"name": "example.test/other"}, [{"fixed": "1.3.0"}]),
            ({"range_type": "GIT"}, [{"fixed": "deadbeef"}]),
        ):
            with self.subTest(kwargs=kwargs, events=events):
                counts, blocking = SCAN["summarize"](report("9.8", events, **kwargs))
                self.assertEqual(counts["CRITICAL"], 1)
                self.assertEqual(blocking, 0)

    @mock.patch.object(subprocess, "run")
    def test_go_sbom_scans_effective_versions_of_tidy_requirements(self, run):
        requirements = [
            {"Path": "example.test/core", "Version": "v1.2.3"},
            {"Path": "example.test/forked", "Version": "v1.0.0", "Indirect": True},
            {"Path": "example.test/local", "Version": "v0.0.0"},
        ]
        run.side_effect = [
            subprocess.CompletedProcess([], 0, stdout=""),
            subprocess.CompletedProcess([], 0, stdout=json.dumps({"Require": requirements})),
            subprocess.CompletedProcess(
                [], 0, stdout="example.test/core\tv1.2.3\nexample.test/fork\tv1.1.0\n",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            core = Path(directory)
            (core / "go.sum").write_text(
                "example.test/core v1.2.3 h1:module\n"
                "example.test/core v1.2.3/go.mod h1:manifest\n"
                "example.test/core v1.2.2 h1:old-version\n"
                "example.test/unimported v1.0.0/go.mod h1:graph-only\n"
            )
            components = SCAN["go_sbom"](core)["components"]
        self.assertEqual(
            [item["purl"] for item in components],
            ["pkg:golang/example.test/core@v1.2.3", "pkg:golang/example.test/fork@v1.1.0"],
        )
        self.assertEqual(run.call_args_list[0].args[0], ["go", "mod", "tidy", "-diff"])
        self.assertEqual(run.call_args_list[1].args[0], ["go", "mod", "edit", "-json"])
        args = run.call_args_list[2].args[0]
        self.assertIn("-mod=readonly", args)
        self.assertEqual(args[-3:], [item["Path"] for item in requirements])

    @mock.patch.object(subprocess, "run")
    def test_go_inventory_drift_leaves_the_gate_unverified(self, run):
        run.side_effect = subprocess.CalledProcessError(1, ["go", "mod", "tidy"])
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            core = source / "core"
            core.mkdir()
            (core / "go.sum").touch()
            self.assertEqual(SCAN["main"]("osv-scanner", source), 2)
        run.assert_called_once()

    def test_clean_scan_is_reported_without_blocking(self):
        counts, blocking = SCAN["summarize"](json.loads('{"results": []}'))
        self.assertEqual(sum(counts.values()), 0)
        self.assertEqual(blocking, 0)

    def test_pub_build_suffixes_are_ordered_and_go_build_metadata_is_ignored(self):
        version_key = SCAN["version_key"]
        self.assertGreater(version_key("1.2.3+2", "Pub"), version_key("1.2.3+1", "Pub"))
        self.assertEqual(version_key("v2.0.0+incompatible", "Go"), version_key("v2.0.0", "Go"))
