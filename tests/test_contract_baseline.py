"""Bounded Git and subprocess tests for the trusted contract baseline path."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError("test module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASELINE = load("baseline", "contract-baseline.py")
RUNNER = load("runner", "run-flutter-coverage.py")


class ContractBaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "负责人")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", "/dev/null")
        (self.root / "contract").write_text("v1\n")
        self.git("add", "contract")
        self.git("commit", "-qm", "baseline")
        self.baseline = self.git("rev-parse", "HEAD")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, text=True,
                                       stderr=subprocess.PIPE).strip()

    def candidate(self):
        (self.root / "contract").write_text("v1\nv2\n")
        self.git("commit", "-qam", "candidate")

    def test_signed_reviewed_floor_selects_exact_source(self):
        path = ROOT / "tests/fixtures/update-manifest-v1.json"
        selected = BASELINE.select_baseline(path)
        self.assertEqual(selected, json.loads(path.read_text())["sourceCommit"])

    def test_missing_floor_fails(self):
        with self.assertRaises(FileNotFoundError):
            BASELINE.select_baseline(self.root / "missing.json")

    def test_tampered_signed_floor_fails(self):
        data = json.loads((ROOT / "tests/fixtures/update-manifest-v1.json").read_text())
        data["sourceCommit"] = self.baseline
        path = self.root / "tampered.json"
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(BASELINE.VERIFIER.ManifestError, "signature is invalid"):
            BASELINE.select_baseline(path)

    def test_legal_candidate_succeeds_and_worker_receives_baseline(self):
        self.candidate()
        BASELINE.validate_baseline(self.root, self.baseline)
        # The same function used for every Flutter invocation launches a real
        # subprocess; no Flutter SDK, full suite, or additional worktree needed.
        with patch.dict(os.environ, {"YUELINK_CONTRACT_BASELINE": self.baseline}):
            with (self.root / "worker.log").open("w") as log:
                result = RUNNER.run_flutter_worker([
                    sys.executable, "-c",
                    "import os,subprocess; b=os.environ['YUELINK_CONTRACT_BASELINE']; "
                    "subprocess.run(['git','cat-file','-e',b+'^{commit}'],check=True); print(b)",
                ], self.root, log)
        self.assertEqual(result.returncode, 0)
        self.assertEqual((self.root / "worker.log").read_text().strip(), self.baseline)

    def test_missing_baseline_object_fails(self):
        self.candidate()
        with self.assertRaises(subprocess.CalledProcessError):
            BASELINE.validate_baseline(self.root, "a" * 40)

    def test_candidate_itself_fails(self):
        with self.assertRaisesRegex(ValueError, "candidate itself"):
            BASELINE.validate_baseline(self.root, self.baseline)

    def test_different_commit_same_tree_fails(self):
        self.git("commit", "--allow-empty", "-qm", "same tree")
        with self.assertRaisesRegex(ValueError, "same tree"):
            BASELINE.validate_baseline(self.root, self.baseline)

    def test_non_ancestor_fails(self):
        self.candidate()
        future = self.git("rev-parse", "HEAD")
        self.git("checkout", "--detach", self.baseline)
        with self.assertRaises(subprocess.CalledProcessError):
            BASELINE.validate_baseline(self.root, future)

    def test_workflow_binds_selection_fetch_validation_and_worker(self):
        workflow = (ROOT / ".github/workflows/source-attestation.yml").read_text()
        selection = workflow.index("- name: Select signed reviewed contract baseline")
        fetch = workflow.index("- name: Fetch exact private contract baseline")
        validation = workflow.index("- name: Import and validate contract baseline object")
        receipt = workflow.index("- name: Capture executed Flutter gate identities")
        self.assertLess(selection, fetch)
        self.assertLess(fetch, validation)
        self.assertLess(validation, receipt)
        self.assertIn("ref: ${{ github.sha }}", workflow[:selection])
        self.assertIn("ref: ${{ steps.contract_baseline.outputs.sha }}", workflow[fetch:validation])
        self.assertIn("contract-baseline.py --source .", workflow[validation:receipt])
        self.assertIn("YUELINK_CONTRACT_BASELINE=%s", workflow[validation:receipt])
        self.assertIn('"$GITHUB_ENV"', workflow[validation:receipt])
        self.assertNotIn("- name: Run complete Flutter suite", workflow)


if __name__ == "__main__":
    unittest.main()
