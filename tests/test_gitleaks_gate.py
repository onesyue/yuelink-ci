"""The yuelink-ci secret gate scans this tree and pull-request commits."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_YUEOPS_FINGERPRINT = "7ba416b81d34"
ALLOWLIST_PATHS = (
    r"""^tests/fixtures/flutter-coverage-singletons-[^/]+\.json$""",
    r"""^tests/fixtures/flutter-coverage-groups-[^/]+\.json$""",
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONSTANTS = _load("gitleaks_gate_constants", ROOT / "scripts" / "constants.py")
SCAN = _load("gitleaks_gate_scan", ROOT / "scripts" / "gitleaks_scan.py")


class GitleaksGateTests(unittest.TestCase):
    def _git(self, root: Path, *args: str) -> str:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "user.email=gitleaks@example.com",
                "-c",
                "user.name=gitleaks",
                *args,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=CONSTANTS.GH_COMMAND_TIMEOUT_SECONDS,
        )
        return proc.stdout.strip()

    def _repo(self, root: Path) -> None:
        (root / ".gitleaks.toml").write_text(
            (ROOT / ".gitleaks.toml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self._git(root, "init", "-b", "master")
        self._git(root, "add", ".gitleaks.toml")
        self._git(root, "commit", "-m", "init")

    def _stub(self, root: Path) -> tuple[Path, Path]:
        log = root / "stub.log"
        stub = root / "gitleaks-stub"
        stub.write_text(
            "#!/bin/sh\n"
            'printf \'%s\\n\' "$*" >> "$GITLEAKS_STUB_LOG"\n'
            'if [ "$1" = version ]; then\n'
            '  printf \'%s\\n\' "$GITLEAKS_STUB_VERSION"\n'
            "  exit 0\n"
            "fi\n"
            'if [ -n "${GITLEAKS_STUB_FAIL:-}" ]; then\n'
            "  exit 1\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        return stub, log

    def _run(self, root: Path, env: dict[str, str]) -> int:
        stub, log = self._stub(root)
        full = {
            "GITLEAKS_BIN": str(stub),
            "GITLEAKS_ROOT": str(root),
            "GITLEAKS_STUB_LOG": str(log),
            "GITLEAKS_STUB_VERSION": CONSTANTS.GITLEAKS_VERSION,
            "GITHUB_EVENT_NAME": "",
            "GITHUB_EVENT_PATH": "",
            **env,
        }
        with mock.patch.dict(os.environ, full, clear=False):
            return SCAN.main()

    def _lines(self, root: Path) -> list[str]:
        return (root / "stub.log").read_text(encoding="utf-8").splitlines()

    def test_tree_scan_does_not_walk_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".gitleaks.toml").write_text("title = \"fixture\"\n", encoding="utf-8")
            self.assertEqual(self._run(root, {}), 0)
            commands = [line.split()[0] for line in self._lines(root)]
            self.assertEqual(commands, ["version", "dir"])
            self.assertNotIn("--all", "\n".join(self._lines(root)))

    def test_pull_request_scans_only_its_commits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            base = self._git(root, "rev-parse", "HEAD")
            (root / "note.txt").write_text("fixture\n", encoding="utf-8")
            self._git(root, "add", "note.txt")
            self._git(root, "commit", "-m", "note")
            head = self._git(root, "rev-parse", "HEAD")
            payload = root / "event.json"
            payload.write_text(
                json.dumps({"pull_request": {"base": {"sha": base}}}),
                encoding="utf-8",
            )
            self.assertEqual(
                self._run(
                    root,
                    {
                        "GITHUB_EVENT_NAME": "pull_request",
                        "GITHUB_EVENT_PATH": str(payload),
                    },
                ),
                0,
            )
            joined = "\n".join(self._lines(root))
            self.assertIn(f"--log-opts {base}..{head}", joined)
            self.assertNotIn("--all", joined)

    def test_push_without_a_parent_stays_on_the_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            payload = root / "event.json"
            payload.write_text(
                json.dumps({"before": CONSTANTS.GITLEAKS_ABSENT_PUSH_BEFORE}),
                encoding="utf-8",
            )
            self.assertEqual(
                self._run(
                    root,
                    {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": str(payload)},
                ),
                0,
            )
            commands = [line.split()[0] for line in self._lines(root)]
            self.assertEqual(commands, ["version", "dir"])

    def test_push_scans_commits_unique_to_the_push(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            before = self._git(root, "rev-parse", "HEAD")
            (root / "note.txt").write_text("fixture\n", encoding="utf-8")
            self._git(root, "add", "note.txt")
            self._git(root, "commit", "-m", "note")
            head = self._git(root, "rev-parse", "HEAD")
            payload = root / "event.json"
            payload.write_text(json.dumps({"before": before}), encoding="utf-8")
            self.assertEqual(
                self._run(
                    root,
                    {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": str(payload)},
                ),
                0,
            )
            self.assertIn(
                f"--log-opts {before}..{head}",
                "\n".join(self._lines(root)),
            )

    def test_finding_fails_the_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".gitleaks.toml").write_text("title = \"fixture\"\n", encoding="utf-8")
            self.assertEqual(self._run(root, {"GITLEAKS_STUB_FAIL": "1"}), 1)

    def test_bad_push_parent_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            payload = root / "event.json"
            payload.write_text(json.dumps({"before": "not-a-commit"}), encoding="utf-8")
            self.assertEqual(
                self._run(
                    root,
                    {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": str(payload)},
                ),
                2,
            )

    def test_allowlist_keeps_the_reviewed_fixture_paths(self) -> None:
        text = (ROOT / ".gitleaks.toml").read_text(encoding="utf-8")
        self.assertNotIn(HISTORICAL_YUEOPS_FINGERPRINT, text)
        self.assertEqual(text.count("[[allowlists]]"), 2)
        self.assertEqual(text.count('targetRules = ["generic-api-key"]'), 2)
        for path in ALLOWLIST_PATHS:
            self.assertIn(path, text)
        self.assertNotIn("regexes", text)

    def test_policy_job_runs_the_scan_on_pull_request_and_master(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "policy-ci.yml").read_text(encoding="utf-8")
        attestation = (ROOT / ".github" / "workflows" / "source-attestation.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("pull_request: {}", workflow)
        self.assertIn("branches: [master]", workflow)
        self.assertIn("name: Updater manifest trust contract", workflow)
        self.assertIn("python3 scripts/gitleaks_scan.py", workflow)
        self.assertNotIn("gitleaks git", workflow)
        self.assertIn(f"GITLEAKS_VERSION: '{CONSTANTS.GITLEAKS_VERSION}'", attestation)
        self.assertIn(
            f"GITLEAKS_LINUX_X64_SHA256: '{CONSTANTS.GITLEAKS_LINUX_X64_SHA256}'",
            attestation,
        )
        self.assertIn(
            "gitleaks git --config .gitleaks.toml --redact --verbose .",
            attestation,
        )


if __name__ == "__main__":
    unittest.main()
