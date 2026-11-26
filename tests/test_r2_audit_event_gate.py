"""Exercise the actual audit job expression without contacting R2."""
from __future__ import annotations

import ast
import itertools
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/nightly.yml'


def job_condition(workflow: str) -> str:
    job = workflow.split('\n  audit:\n', 1)[1].split('\n    steps:', 1)[0]
    match = re.search(r'\n    if: (?:>-?\n\s*)?\$\{\{(.*?)\}\}', job, re.S)
    if match is None:
        raise ValueError('audit job must have an explicit condition')
    return ' '.join(match.group(1).split())


def admitted(expression: str, event: str, source: str, conclusion: str, *,
             schedule: str = '', workflow_name: str = 'Build YueLink',
             version: str = '') -> bool:
    values = {
        'github.event_name': event,
        'github.event.schedule': schedule,
        'github.event.workflow_run.name': workflow_name,
        'github.event.workflow_run.event': source,
        'github.event.workflow_run.conclusion': conclusion,
        'inputs.version': version,
    }
    for key in sorted(values, key=len, reverse=True):
        expression = expression.replace(key, repr(values[key]))
    tree = ast.parse(expression.replace('&&', ' and ').replace('||', ' or '), mode='eval')
    # Only the boolean/equality subset used by this workflow is admitted.
    allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Compare,
               ast.Eq, ast.NotEq, ast.Constant, ast.Load)
    if any(not isinstance(node, allowed) for node in ast.walk(tree)):
        raise ValueError('unsupported job expression')
    return bool(eval(compile(tree, '<audit-job-if>', 'eval'), {'__builtins__': {}}, {}))


class R2AuditEventGateTests(unittest.TestCase):
    def test_source_event_and_conclusion_matrix(self) -> None:
        expression = job_condition(WORKFLOW.read_text())
        sources = ('push', 'workflow_dispatch', 'pull_request',
                   'pull_request_target', 'schedule', '', 'release')
        conclusions = ('success', 'failure', 'cancelled', 'skipped', 'timed_out', '')
        for source, conclusion in itertools.product(sources, conclusions):
            with self.subTest(source=source, conclusion=conclusion):
                self.assertEqual(
                    admitted(expression, 'workflow_run', source, conclusion),
                    source in ('push', 'workflow_dispatch') and conclusion == 'success',
                )
        # A tag build is a push event; there is no separate "tag" event.
        self.assertTrue(admitted(expression, 'schedule', '', '', schedule='30 18 * * *'))
        self.assertFalse(admitted(expression, 'schedule', '', '', schedule='17 2 * * *'))
        self.assertTrue(admitted(expression, 'workflow_dispatch', '', '', version=''))
        self.assertFalse(admitted(expression, 'workflow_dispatch', '', '', version='1.2.3'))
        self.assertFalse(admitted(
            expression, 'workflow_run', 'push', 'success', workflow_name='Source attestation'))

    def test_old_success_only_gate_exposes_regression(self) -> None:
        old = "github.event_name != 'workflow_run' || github.event.workflow_run.conclusion == 'success'"
        self.assertTrue(admitted(old, 'workflow_run', 'pull_request', 'success'))
        self.assertFalse(admitted(job_condition(WORKFLOW.read_text()),
                                  'workflow_run', 'pull_request', 'success'))

    def test_missing_job_gate_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            job_condition('jobs:\n  audit:\n    runs-on: ubuntu-24.04\n    steps:\n')

    def test_defense_in_depth_remains(self) -> None:
        workflow = WORKFLOW.read_text()
        self.assertIn('push|workflow_dispatch) ;;', workflow)
        self.assertIn('unexpected Build source event: $SOURCE_EVENT', workflow)
        self.assertIn('[ "$SOURCE_WORKFLOW_PATH" = .github/workflows/build.yml ]', workflow)


if __name__ == '__main__':
    unittest.main()
