"""Simulate the workflow tools checkout layout, not just YAML string presence."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))
from test_mihomo_upgrade_compat import build_fork, populated_source

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/source-attestation.yml"
POLICY = ROOT / ".github/workflows/policy-ci.yml"
COMMAND = (
    "python3 .mihomo-upgrade-compat-tools/scripts/"
    "check-mihomo-upgrade-compat.py --source-root ."
)
TOOLS_REPOSITORY = "repository: onesyue/yuelink-ci"
TOOLS_REF = "ref: ${{ github.sha }}"
SCRIPT_RELATIVE = "scripts/check-mihomo-upgrade-compat.py"
FLOOR_RELATIVE = "tests/fixtures/mihomo-upgrade-compat/reviewed-patch-floor.json"
REQUIRED_TOOLS_PATHS = (SCRIPT_RELATIVE, FLOOR_RELATIVE)


def tools_checkout_step(workflow_text: str) -> str:
    parts = workflow_text.split("Checkout mihomo upgrade-compat tools")
    if len(parts) != 2:
        raise AssertionError(
            f"expected exactly one upgrade-compat tools checkout, found {len(parts) - 1}"
        )
    return parts[1].split("\n      - name:", 1)[0]


def sparse_checkout_paths(step: str) -> tuple[str, ...]:
    """Return the exact file whitelist from a tools checkout step."""
    lines = step.splitlines()
    paths: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("sparse-checkout:"):
            rest = stripped.split(":", 1)[1].strip()
            step_indent = len(line) - len(line.lstrip(" "))
            if rest in {"|", ">", "|-", ">-", "|+", ">+"}:
                index += 1
                while index < len(lines):
                    raw = lines[index]
                    if raw.strip() == "":
                        index += 1
                        continue
                    indent = len(raw) - len(raw.lstrip(" "))
                    if indent <= step_indent:
                        break
                    paths.append(raw.strip())
                    index += 1
                continue
            if rest:
                paths.append(rest)
        index += 1
    if not paths:
        raise AssertionError("tools checkout has no sparse-checkout whitelist")
    return tuple(paths)


def assert_minimal_tools_checkout(test: unittest.TestCase, step: str) -> tuple[str, ...]:
    test.assertIn("persist-credentials: false", step)
    test.assertNotIn("ssh-key:", step)
    test.assertNotIn("token:", step)
    test.assertNotIn("secrets.", step)
    test.assertIn(TOOLS_REPOSITORY, step)
    test.assertIn(TOOLS_REF, step)
    test.assertIn("sparse-checkout-cone-mode: false", step)
    paths = sparse_checkout_paths(step)
    test.assertEqual(set(paths), set(REQUIRED_TOOLS_PATHS), paths)
    test.assertEqual(len(paths), len(set(paths)))
    return paths


def materialize_tools_checkout(dest: Path, paths: tuple[str, ...], *, source: Path = ROOT) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for relative in paths:
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise AssertionError(f"refusing unsafe sparse-checkout path {relative}")
        src = source / relative
        if not src.is_file():
            raise AssertionError(f"reviewed tools commit is missing {relative}")
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)


def official_checker_command() -> list[str]:
    return ["python3", *COMMAND.split()[1:]]


def listed_tools_files(tools: Path) -> list[str]:
    return sorted(
        path.relative_to(tools).as_posix()
        for path in tools.rglob("*")
        if path.is_file()
    )


def discover_yuelink() -> Path | None:
    candidates = []
    override = os.environ.get("YUELINK_SOURCE")
    if override:
        candidates.append(Path(override))
    candidates.extend(
        (
            Path("/Users/j/Projects/yueto/yuelink"),
            Path.home() / "Projects/yueto/yuelink",
        )
    )
    seen: set[Path] = set()
    for path in candidates:
        path = path.resolve()
        if path in seen:
            continue
        seen.add(path)
        if (path / "docs/governance/mihomo-fork-ledger.json").is_file() and (
            path / "core/mihomo"
        ).exists():
            return path
    return None


def attach_mihomo_history(workspace: Path, donor: Path) -> None:
    destination = workspace / "core/mihomo"
    source = donor / "core/mihomo"
    if destination.exists() and destination.is_dir() and not any(destination.iterdir()):
        destination.rmdir()
    if destination.exists() or destination.is_symlink():
        raise AssertionError(f"cannot attach mihomo history over {destination}")
    destination.symlink_to(source.resolve(), target_is_directory=True)


def add_yuelink_workspace(workspace: Path, donor: Path) -> None:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(donor),
            "worktree",
            "add",
            "--detach",
            str(workspace),
            "HEAD",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    attach_mihomo_history(workspace, donor)


def remove_yuelink_workspace(workspace: Path, donor: Path) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(donor),
            "worktree",
            "remove",
            "--force",
            str(workspace),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def plant_untrusted_source_floor(workspace: Path) -> None:
    planted = workspace / FLOOR_RELATIVE
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(
        json.dumps(
            {
                "schema": 1,
                "patch_ids": ["0" * 40],
                "notes": "untrusted source-root floor",
            }
        ),
        encoding="utf-8",
    )


class ToolsCheckoutWhitelistTests(unittest.TestCase):
    def test_attestation_and_policy_share_the_reviewed_script_and_floor(self) -> None:
        attestation_paths = assert_minimal_tools_checkout(
            self, tools_checkout_step(WORKFLOW.read_text(encoding="utf-8"))
        )
        policy_paths = assert_minimal_tools_checkout(
            self, tools_checkout_step(POLICY.read_text(encoding="utf-8"))
        )
        self.assertEqual(attestation_paths, policy_paths)
        self.assertEqual(set(attestation_paths), set(REQUIRED_TOOLS_PATHS))

    def test_tools_come_from_workflow_sha_not_the_source_under_test(self) -> None:
        attestation = WORKFLOW.read_text(encoding="utf-8")
        tools = tools_checkout_step(attestation)
        assert_minimal_tools_checkout(self, tools)
        live = [
            line.strip()
            for line in tools.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertIn(TOOLS_REF, live)
        self.assertFalse(any("inputs.source_sha" in line for line in live))
        header = attestation.split("\non:\n", 1)[1].split("\njobs:", 1)[0]
        self.assertIn("workflow_dispatch:", header)
        self.assertNotIn("pull_request:", header)
        self.assertNotIn("push:", header)
        policy = POLICY.read_text(encoding="utf-8")
        job = policy.split("source_version:", 1)[1]
        self.assertIn(
            "if: github.event_name == 'workflow_dispatch' && inputs.yuelink_sha != ''",
            job.split("steps:", 1)[0],
        )
        policy_tools = tools_checkout_step(job)
        assert_minimal_tools_checkout(self, policy_tools)
        policy_live = [
            line.strip()
            for line in policy_tools.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertIn(TOOLS_REF, policy_live)
        self.assertFalse(any("inputs.yuelink_sha" in line for line in policy_live))
        source_checkout = job.split("Checkout requested YueLink commit and release tags", 1)[1].split(
            "\n      - name:", 1
        )[0]
        self.assertIn("repository: onesyue/yuelink", source_checkout)
        self.assertIn("ref: ${{ inputs.yuelink_sha }}", source_checkout)


class ToolsCheckoutLayoutSimulationTests(unittest.TestCase):
    def test_script_only_layout_fail_closes_and_ignores_a_source_floor(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = populated_source(root, build_fork(root))
            plant_untrusted_source_floor(workspace)
            tools = workspace / ".mihomo-upgrade-compat-tools"
            materialize_tools_checkout(tools, (SCRIPT_RELATIVE,))
            self.assertEqual(listed_tools_files(tools), [SCRIPT_RELATIVE])
            result = subprocess.run(
                official_checker_command(),
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("reviewed patch floor is unreadable", result.stderr)
            self.assertNotIn("PASS:", result.stdout)

    def test_extracted_official_floor_is_applied_and_cannot_be_replaced_by_source(self) -> None:
        paths = assert_minimal_tools_checkout(
            self, tools_checkout_step(WORKFLOW.read_text(encoding="utf-8"))
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = populated_source(root, build_fork(root))
            plant_untrusted_source_floor(workspace)
            tools = workspace / ".mihomo-upgrade-compat-tools"
            materialize_tools_checkout(tools, paths)
            self.assertEqual(set(listed_tools_files(tools)), set(paths))
            result = subprocess.run(
                official_checker_command(),
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("reviewed floor patch-id", result.stderr)
            self.assertNotIn("reviewed patch floor is unreadable", result.stderr)
            self.assertNotIn("PASS:", result.stdout)

    def test_extracted_workflow_layout_runs_the_reviewed_floor_on_live_yuelink(self) -> None:
        donor = discover_yuelink()
        if donor is None:
            self.skipTest(
                "no local YueLink checkout with ledger and mihomo history; "
                "policy-ci runners do not ship one"
            )
        paths = assert_minimal_tools_checkout(
            self, tools_checkout_step(WORKFLOW.read_text(encoding="utf-8"))
        )
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw) / "workspace"
            add_yuelink_workspace(workspace, donor)
            try:
                tools = workspace / ".mihomo-upgrade-compat-tools"
                materialize_tools_checkout(tools, paths)
                self.assertEqual(set(listed_tools_files(tools)), set(paths))
                result = subprocess.run(
                    official_checker_command(),
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(
                    "PASS: mihomo ledger is internally consistent and patch-preserving",
                    result.stdout,
                )
            finally:
                remove_yuelink_workspace(workspace, donor)


if __name__ == "__main__":
    unittest.main()
