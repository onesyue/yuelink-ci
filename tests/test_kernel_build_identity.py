"""The kernel build tag and Go runtime prefix stay today's bytes."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "scripts" / "constants.py"
    spec = importlib.util.spec_from_file_location("ci_constants_kernel", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class KernelBuildIdentityTests(unittest.TestCase):
    def test_tag_and_prefix_match_today(self) -> None:
        constants = _load()
        self.assertEqual(constants.GO_KERNEL_BUILD_TAGS, b"with_gvisor".decode("ascii"))
        self.assertEqual(constants.GO_RUNTIME_VERSION_PREFIX, b"go".decode("ascii"))

    def test_workflow_expands_the_constant_names(self) -> None:
        workflow = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
        self.assertIn('os.environ["GO_VERSION"]', workflow)
        self.assertIn('os.environ["GO_KERNEL_BUILD_TAGS"]', workflow)
        self.assertIn('os.environ["GO_RUNTIME_VERSION_PREFIX"]', workflow)
        self.assertNotIn('"with_gvisor"', workflow)
        compile_script = (ROOT / "scripts" / "ios-unsigned-compile.py").read_text(encoding="utf-8")
        self.assertNotIn('"with_gvisor"', compile_script)

    @unittest.skipIf(shutil.which("shellcheck") is None, "shellcheck not installed")
    def test_workflow_constant_assignments_are_sc2155_clean(self) -> None:
        workflow = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
        names = ("GO_KERNEL_BUILD_TAGS", "GO_RUNTIME_VERSION_PREFIX")
        lines = []
        for raw in workflow.splitlines():
            stripped = raw.strip()
            if any(
                stripped.startswith(f"{name}=") or stripped == f"export {name}"
                for name in names
            ):
                lines.append(stripped)
        self.assertEqual(
            lines,
            [
                'GO_KERNEL_BUILD_TAGS="$(python3 scripts/constants.py GO_KERNEL_BUILD_TAGS)"',
                "export GO_KERNEL_BUILD_TAGS",
                'GO_RUNTIME_VERSION_PREFIX="$(python3 scripts/constants.py GO_RUNTIME_VERSION_PREFIX)"',
                "export GO_RUNTIME_VERSION_PREFIX",
            ],
        )
        for name in names:
            self.assertNotIn(f'export {name}="$', workflow)
        script = "#!/usr/bin/env bash\nset -euo pipefail\n" + "\n".join(lines) + "\n"
        proc = subprocess.run(
            [shutil.which("shellcheck"), "--severity=warning", "--format=gcc", "-"],
            input=script,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("SC2155", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
