"""Keep every policy case reachable by the workflow's stdlib test runner."""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def undiscovered_cases(directory: Path) -> list[str]:
    """unittest ignores module functions and classes without TestCase ancestry."""
    errors: list[str] = []
    for path in sorted(directory.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    errors.append(f"{path.name}:{node.lineno}: {node.name}")
            elif isinstance(node, ast.ClassDef):
                cases = [
                    child
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name.startswith("test_")
                ]
                if cases and not any(
                    isinstance(base, ast.Attribute)
                    and isinstance(base.value, ast.Name)
                    and base.value.id == "unittest"
                    and base.attr == "TestCase"
                    for base in node.bases
                ):
                    errors.append(f"{path.name}:{node.lineno}: {node.name}")
    return errors


class PolicyDiscoveryContractTests(unittest.TestCase):
    def test_all_policy_cases_are_unittest_discoverable(self) -> None:
        directory = ROOT / "tests"
        self.assertGreaterEqual(len(list(directory.glob("test_*.py"))), 15)
        self.assertEqual(undiscovered_cases(directory), [])
        workflow = (ROOT / ".github/workflows/policy-ci.yml").read_text()
        self.assertIn("python3 -m unittest discover -s tests -p 'test_*.py'", workflow)

    def test_missing_suite_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(globals(), ROOT=Path(tmp)):
                with self.assertRaises(AssertionError):
                    self.test_all_policy_cases_are_unittest_discoverable()

    def test_rejects_plain_functions_and_unregistered_classes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory / "test_example.py"
            path.write_text(
                "def test_hidden():\n    pass\n"
                "class HiddenTests:\n    def test_hidden(self):\n        pass\n"
            )
            self.assertEqual(len(undiscovered_cases(directory)), 2)
            path.write_text(
                "import unittest\n"
                "class VisibleTests(unittest.TestCase):\n"
                "    def test_visible(self):\n        pass\n"
            )
            self.assertEqual(undiscovered_cases(directory), [])
