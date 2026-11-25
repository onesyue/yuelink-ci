"""The kernel build tag and Go runtime prefix stay today's bytes."""

from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
