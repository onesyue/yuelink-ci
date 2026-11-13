"""The public dial job may restore a kernel, and it still runs the host dial."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / '.github/workflows/build.yml'
DIAL = ROOT / '.github/workflows/dial-gate.yml'

_JOB = re.compile(r'(?m)^(?=  [A-Za-z0-9_-]+:\s*$)')
_STEP = re.compile(r'(?m)^(?=      - )')


def dial_job(workflow):
    for job in _JOB.split(workflow):
        if job.startswith('  dial_gate:\n'):
            return job
    return ''


def dial_step_job(workflow):
    for job in _JOB.split(workflow):
        if (
            job.startswith('  dial_gate:\n')
            and 'Dial on the runner' in job
        ):
            return job
    return ''


def step_named(job, name):
    for step in _STEP.split(job):
        if step.startswith(f'      - name: {name}\n'):
            return step
    return ''


def dial_kernel_cache_issues(workflow):
    """The kernel cache is optional. The host dial command still runs."""
    issues = []
    job = dial_step_job(workflow)
    if not job:
        return ['dial_gate job is missing']
    caller = dial_job(workflow)
    if 'uses: ./.github/workflows/dial-gate.yml' in caller:
        needs = re.search(r'(?m)^    needs:\s*\[([^\]]*)\]', caller)
        items = []
        if needs:
            items = [item.strip() for item in needs.group(1).split(',') if item.strip()]
        if items != ['plan']:
            issues.append('dial_gate does not need the plan that binds source')
        if 'needs.plan.outputs.source_commit' not in caller:
            issues.append('dial_gate does not pass the attested source commit')
    if 'value: ${{ jobs.source_binding.outputs.source_commit }}' not in workflow:
        issues.append('plan does not publish the attested source commit')
    host = step_named(job, 'Dial on the runner')
    if 'run: bash candidate/core/dialgate/ci/run.sh host\n' not in host:
        issues.append('host dial command is missing')
    if 'cache-hit' in host:
        issues.append('host dial is conditional on the kernel cache')
    key_step = step_named(job, 'Dial kernel cache key')
    if 'set +e\n' not in key_step or 'run.sh cache-key' not in key_step:
        issues.append('a missing cache-key command fails the dial job')
    cache = step_named(job, 'Restore dial kernels')
    if 'continue-on-error: true' not in cache:
        issues.append('dial kernel cache outage fails the dial job')
    if 'restore-keys' in cache:
        issues.append('dial kernel cache has restore-keys')
    if 'path: dial-out/kernel-cache' not in cache:
        issues.append('dial kernel cache path is not the kernel directory')
    if ('steps.dial_kernel.outputs.key' not in cache
            or 'env.GO_VERSION' not in cache):
        issues.append('dial kernel cache key is not bound to the identity and Go')
    if "if: steps.dial_kernel.outputs.key != ''" not in cache:
        issues.append('dial kernel cache runs without a key')
    pins = set(re.findall(r'uses: actions/cache@([0-9a-f]{40})', workflow))
    step_pins = set(re.findall(r'uses: actions/cache@([0-9a-f]{40})', cache))
    if len(step_pins) != 1 or not step_pins <= pins:
        issues.append('dial kernel cache does not use the workflow actions/cache pin')
    return issues


def caller_source_issues(workflow):
    """The build caller still binds the attested source and calls the shared gate."""
    job = dial_job(workflow)
    if not job:
        return ['dial_gate job is missing']
    issues = []
    needs = re.search(r'(?m)^    needs:\s*\[([^\]]*)\]', job)
    items = []
    if needs:
        items = [item.strip() for item in needs.group(1).split(',') if item.strip()]
    if items != ['plan']:
        issues.append('dial_gate does not need the plan that binds source')
    if 'needs.plan.outputs.source_commit' not in job:
        issues.append('dial_gate does not pass the attested source commit')
    if 'uses: ./.github/workflows/dial-gate.yml' not in job:
        issues.append('dial_gate does not call the shared workflow')
    return issues


class DialKernelCacheTests(unittest.TestCase):
    def test_host_dial_survives_a_missing_kernel_cache(self):
        from scripts.check_public_build import composed_workflow

        caller = BUILD.read_text(encoding='utf-8')
        workflow = composed_workflow(BUILD)
        self.assertEqual(caller_source_issues(caller), [])
        self.assertEqual(dial_kernel_cache_issues(workflow), [])
        without_bind = caller.replace(
            '  dial_gate:\n    name: Dial gate\n    needs: [plan]\n',
            '  dial_gate:\n    name: Dial gate\n    needs: [prepare]\n',
            1,
        )
        self.assertNotEqual(without_bind, caller)
        self.assertIn(
            'dial_gate does not need the plan that binds source',
            caller_source_issues(without_bind))
        without_ref = caller.replace(
            '|| needs.plan.outputs.source_commit }}',
            '|| github.sha }}',
            1,
        )
        self.assertIn(
            'dial_gate does not pass the attested source commit',
            caller_source_issues(without_ref))
        job = dial_step_job(workflow)
        without_host = workflow.replace(
            'run: bash candidate/core/dialgate/ci/run.sh host\n',
            'run: echo skipped\n', 1)
        self.assertIn(
            'host dial command is missing', dial_kernel_cache_issues(without_host))
        without_open = workflow.replace(
            job, job.replace('          set +e\n', '', 1), 1)
        self.assertIn(
            'a missing cache-key command fails the dial job',
            dial_kernel_cache_issues(without_open))
        with_prefix = workflow.replace(
            '          path: dial-out/kernel-cache\n',
            '          path: dial-out/kernel-cache\n          restore-keys: yue-dialkernel-v1-\n',
            1)
        self.assertIn(
            'dial kernel cache has restore-keys',
            dial_kernel_cache_issues(with_prefix))
        job = dial_step_job(workflow)
        without_continue = workflow.replace(
            job,
            job.replace(
                '      - name: Restore dial kernels\n'
                '        id: dial_kernel_cache\n'
                "        if: steps.dial_kernel.outputs.key != ''\n"
                '        continue-on-error: true\n',
                '      - name: Restore dial kernels\n'
                '        id: dial_kernel_cache\n'
                "        if: steps.dial_kernel.outputs.key != ''\n",
                1),
            1)
        self.assertIn(
            'dial kernel cache outage fails the dial job',
            dial_kernel_cache_issues(without_continue))


if __name__ == '__main__':
    unittest.main()
