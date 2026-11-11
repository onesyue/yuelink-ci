"""Evaluate the actual live-read condition; tests never contact production."""
from __future__ import annotations

import ast
import itertools
from pathlib import Path
import re
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/policy-ci.yml'
MARKER = '      - name: Verify current live signed manifest\n'


def condition(workflow: str) -> str:
    if workflow.count(MARKER) != 1:
        raise ValueError('expected exactly one live-read step')
    step = workflow.split(MARKER, 1)[1].split('        run:', 1)[0]
    match = re.fullmatch(r'        if: ([^\n]+)\n', step)
    if match is None:
        raise ValueError('live-read step requires an explicit event condition')
    return match.group(1)


def admitted(expression: str, event: str, ref: str) -> bool:
    expression = expression.replace('github.event_name', repr(event)).replace('github.ref', repr(ref))
    tree = ast.parse(expression.replace('&&', ' and ').replace('||', ' or '), mode='eval')
    allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Compare,
               ast.Eq, ast.NotEq, ast.Constant, ast.Load)
    if any(not isinstance(node, allowed) for node in ast.walk(tree)):
        raise ValueError('unsupported condition')
    return bool(eval(compile(tree, '<live-read-if>', 'eval'), {'__builtins__': {}}, {}))


class PolicyLiveReadGateTests(unittest.TestCase):
    def test_actual_condition_event_and_ref_matrix(self) -> None:
        expression = condition(WORKFLOW.read_text())
        for event, ref in itertools.product(
            ('pull_request', 'pull_request_target', 'push', 'workflow_dispatch', 'schedule', 'workflow_run', ''),
            ('refs/pull/51/merge', 'refs/heads/master', 'refs/heads/topic', 'refs/tags/v1.3.174', ''),
        ):
            with self.subTest(event=event, ref=ref):
                self.assertEqual(admitted(expression, event, ref),
                                 event in ('push', 'workflow_dispatch') and ref == 'refs/heads/master')

    def test_original_unguarded_step_is_rejected(self) -> None:
        workflow = WORKFLOW.read_text()
        original = workflow.replace('        if: ' + condition(workflow) + '\n', '', 1)
        with self.assertRaises(ValueError):
            condition(original)

    def test_weakened_condition_exposes_pr_read(self) -> None:
        workflow = WORKFLOW.read_text()
        weakened = workflow.replace(condition(workflow), "github.ref != ''", 1)
        self.assertTrue(admitted(condition(weakened), 'pull_request', 'refs/pull/51/merge'))
        self.assertFalse(admitted(condition(workflow), 'pull_request', 'refs/pull/51/merge'))


if __name__ == '__main__':
    unittest.main()
