"""Exercise real workflow admission expressions across refs and event types."""
from __future__ import annotations

import ast
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github/workflows'


def expression(text, *, workflow='Build YueLink', ref='refs/heads/master',
               event='workflow_dispatch', repository='onesyue/yuelink-ci',
               preflight='success', shards='success'):
    text = text.strip().removeprefix('${{').removesuffix('}}').strip()
    text = re.sub(r'!(?!=)', ' not ', text).replace('&&', ' and ').replace('||', ' or ').strip()
    text = re.sub(r'\btrue\b', 'True', text)
    text = re.sub(r'\bfalse\b', 'False', text)
    tree = ast.parse(text, mode='eval')
    allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not,
               ast.Compare, ast.Eq, ast.NotEq, ast.Call, ast.Name, ast.Load,
               ast.Attribute, ast.Constant)
    if any(not isinstance(node, allowed) for node in ast.walk(tree)):
        raise ValueError('unsupported workflow expression')
    return eval(compile(tree, '<workflow expression>', 'eval'), {'__builtins__': {}}, {
        'github': SimpleNamespace(workflow=workflow, ref=ref, event_name=event, repository=repository),
        'inputs': SimpleNamespace(source_sha='fixture-source'),
        'needs': SimpleNamespace(preflight=SimpleNamespace(result=preflight),
                                 coverage_shard=SimpleNamespace(result=shards)),
        'startsWith': lambda value, prefix: value.startswith(prefix),
        'cancelled': lambda: False,
    })


def concurrency(text):
    block = re.search(r'(?ms)^concurrency:\n(.*?)(?=^\S|\Z)', text)
    if not block:
        raise ValueError('missing concurrency group')
    return tuple(re.search(rf'(?m)^  {key}: (.+)$', block[1])[1]
                 for key in ('group', 'cancel-in-progress'))


def render(template, **context):
    return re.sub(r'\$\{\{(.*?)\}\}', lambda match: str(expression(match[1], **context)), template)


class WorkflowQueueIsolationTests(unittest.TestCase):
    def test_groups_isolate_workflow_and_ref(self):
        for name in ('build', 'source-attestation', 'policy-ci', 'warm-release-caches'):
            with self.subTest(workflow=name):
                group, _ = concurrency((WORKFLOWS / f'{name}.yml').read_text())
                contexts = [dict(workflow=name, ref=ref) for ref in (
                    'refs/tags/v1.2.3', 'refs/tags/v1.2.4', 'refs/heads/master', 'refs/pull/7/merge')]
                groups = [render(group, **context) for context in contexts]
                self.assertEqual(len(set(groups)), len(contexts))
                self.assertNotEqual(groups[2], render(group, workflow='Another workflow'))

    def test_only_superseded_pull_requests_cancel_in_progress(self):
        for name in ('build', 'policy-ci'):
            _, cancel = concurrency((WORKFLOWS / f'{name}.yml').read_text())
            for event, ref, expected in [('pull_request', 'refs/pull/7/merge', True),
                                          ('push', 'refs/tags/v1.2.3', False),
                                          ('push', 'refs/heads/master', False),
                                          ('workflow_dispatch', 'refs/tags/v1.2.3', False),
                                          ('workflow_dispatch', 'refs/heads/master', False)]:
                with self.subTest(workflow=name, event=event, ref=ref):
                    self.assertEqual(expression(cancel, event=event, ref=ref), expected)
        for name in ('source-attestation', 'warm-release-caches'):
            _, cancel = concurrency((WORKFLOWS / f'{name}.yml').read_text())
            self.assertFalse(expression(cancel, event='workflow_run'))

    def test_tag_reuse_does_not_allocate_empty_coverage_jobs(self):
        workflow = (WORKFLOWS / 'validate.yml').read_text()
        for job in ('coverage_shard', 'coverage_join'):
            block = workflow.split(f'\n  {job}:\n', 1)[1]
            condition = re.search(r'(?m)^    if: (.+)$', block)[1]
            self.assertFalse(expression(condition, event='push', ref='refs/tags/v1.2.3', shards='skipped'))
            self.assertFalse(expression(condition, event='pull_request', ref='refs/pull/7/merge'))
            self.assertTrue(expression(condition))
            self.assertTrue(expression(condition, repository='fixture/private', ref='refs/tags/v1.2.3'))
            if job == 'coverage_join':
                self.assertFalse(expression(condition, preflight='failure'))
                self.assertFalse(expression(condition, shards='failure'))
                self.assertFalse(expression(condition, shards='skipped'))

    def test_old_release_wide_group_and_unconditional_cancel_are_detected(self):
        old = "${{ github.workflow }}-${{ github.event_name == 'pull_request' && github.ref || 'release' }}"
        self.assertEqual(render(old, ref='refs/tags/v1.2.3'), render(old, ref='refs/heads/master'))
        self.assertTrue(expression('true', event='push', ref='refs/tags/v1.2.3'))


if __name__ == '__main__':
    unittest.main()
