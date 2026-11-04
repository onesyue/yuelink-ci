"""Exercise runner admission at YAML, release entry and jobs API boundaries."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ASSERT = ROOT / 'scripts/assert-self-hosted-runs-on.sh'


class SelfHostedAdmissionTests(unittest.TestCase):
    def assert_yaml(self, text: str, accepted: bool) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'workflow.yml'
            path.write_text(text)
            result = subprocess.run(['bash', str(ASSERT), str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, accepted, result.stderr)

    def test_release_gates_accept_and_current_build_rejects(self):
        for name, accepted in [('source-attestation.yml', True), ('policy-ci.yml', True),
                               ('build.yml', False)]:
            with self.subTest(workflow=name):
                self.assert_yaml((ROOT / '.github/workflows' / name).read_text(), accepted)

    def test_literal_scalars_lists_and_yaml_flow(self):
        for value in ['self-hosted', '[self-hosted, Linux, X64, yue-local-release]',
                      "['self-hosted', macOS, ARM64, yuelink-ci-mac]",
                      '\n      - self-hosted\n      - Linux',
                      '[\n      self-hosted,\n      Linux\n    ]']:
            with self.subTest(value=value):
                self.assert_yaml('jobs:\n  gate:\n    runs-on: ' + value + '\n', True)
        self.assert_yaml('jobs: {gate: {"runs-on": [self-hosted, Linux]}}', True)
        self.assert_yaml('jobs:\n  a:\n    runs-on: &labels [self-hosted, Linux]\n'
                         '  b:\n    runs-on: *labels\n', True)

    def test_hosted_and_unprovable_labels_reject(self):
        for value in ['ubuntu-24.04', 'windows-latest', 'macos-26', 'custom-latest',
                      'Linux', '[Linux, X64]', '[self-hosted, ubuntu-24.04]',
                      '${{ matrix.os }}', "${{ 'self-hosted' }}",  # no expression evaluator
                      '[self-hosted, "${{ inputs.runner }}"]',
                      '{group: release, labels: self-hosted}', 'null', '[]', 'true']:
            with self.subTest(value=value):
                self.assert_yaml('jobs:\n  gate:\n    runs-on: ' + value + '\n', False)

    def test_missing_malformed_duplicate_and_merge_reject(self):
        for text in ['', 'jobs: {}', 'jobs: {gate: {steps: []}}',
                     'jobs: {gate: {uses: owner/reusable@main}}',
                     'jobs: {gate: {runs-on: [self-hosted}}',
                     'jobs: {gate: {runs-on: ubuntu-24.04, runs-on: self-hosted}}',
                     'jobs: {gate: {runs-on: self-hosted}, gate: {runs-on: ubuntu-24.04}}',
                     'base: &base {runs-on: ubuntu-24.04}\njobs: {gate: {<<: *base, runs-on: self-hosted}}']:
            with self.subTest(text=text):
                self.assert_yaml(text, False)
        for args in [[], ['/nonexistent/n24-workflow.yml']]:
            result = subprocess.run(['bash', str(ASSERT), *args], capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_missing_yaml_dependency_rejects(self):
        result = subprocess.run(['python3', '-S', str(ASSERT.with_suffix('.py')),
                                 str(ROOT / '.github/workflows/source-attestation.yml')],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires PyYAML', result.stderr)

    def test_release_assert_precedes_dispatch_push_and_remote_reads(self):
        source = (ROOT / 'release.sh').read_text()
        guard = source.index('if ! bash "$SCRIPT_DIR/scripts/assert-self-hosted-runs-on.sh"')
        for marker in ['gh workflow run', 'git push origin', 'gh_read gh secret list']:
            self.assertLess(guard, source.index(marker))
        preflight = source[guard:source.index('\ncommand -v gh')]
        self.assertIn('source-attestation.yml', preflight)
        self.assertIn('build.yml', preflight)
        result = subprocess.run(['bash', str(ROOT / 'release.sh'), 'v9.8.7'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('::error::发布门禁拒绝', result.stderr)

    def test_workspace_cleaning_rejects_root_before_find(self):
        import yaml

        for name in ['source-attestation.yml', 'policy-ci.yml']:
            workflow = yaml.safe_load((ROOT / '.github/workflows' / name).read_text())
            for job in workflow['jobs'].values():
                step = job['steps'][0]
                self.assertEqual(step['name'], 'Clean self-hosted workspace')
                with tempfile.TemporaryDirectory() as tmp:
                    for workspace in ['', '/', '//', '/tmp/..', '/nonexistent/n24', tmp]:
                        with self.subTest(workflow=name, workspace=workspace):
                            result = subprocess.run(
                                ['bash', '-c', 'find() { echo FIND_CALLED; }\n' + step['run']],
                                env={**os.environ, 'GITHUB_WORKSPACE': workspace},
                                capture_output=True, text=True)
                            self.assertEqual(result.returncode == 0, workspace == tmp)
                            self.assertEqual('FIND_CALLED' in result.stdout, workspace == tmp)

    def test_jobs_api_runner_labels_and_pagination(self):
        source = (ROOT / 'release.sh').read_text()
        start = source.index('verify_attestation_runners() {')
        end = source.index('\nverify_attestation_runners || exit 1', start)
        function = source[start:end]
        self.assertLess(end, source.index('bash scripts/verify-source-attestation.sh'))
        good = {'conclusion': 'success', 'labels': ['self-hosted', 'Linux']}
        hosted = {'conclusion': 'success', 'labels': ['ubuntu-24.04']}
        cases = [([good], True), ([hosted], False),
                 ([{'conclusion': 'success'}], False),
                 ([{'conclusion': 'success', 'labels': []}], False),
                 ([{'conclusion': 'success', 'labels': ['self-hosted', 'macos-26']}], False),
                 ([good, {'conclusion': 'skipped', 'labels': []}], True),
                 ([], False), ([good] * 100 + [hosted], False),
                 ([good] * 101, True)]
        for jobs, accepted in cases:
            with self.subTest(count=len(jobs), accepted=accepted), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for page in range(max(1, (len(jobs) + 99) // 100)):
                    (root / f'{page + 1}.json').write_text(json.dumps({
                        'total_count': len(jobs), 'jobs': jobs[page * 100:(page + 1) * 100]}))
                script = 'set -euo pipefail\nATTESTATION_RUN_ID=123\n' + r'''
gh_read() {
  local page="${3##*&page=}"
  cat "$FIXTURE/$page.json"
}
''' + function + '\nverify_attestation_runners\n'
                result = subprocess.run(['bash', '-c', script], capture_output=True, text=True,
                                        env={**os.environ, 'FIXTURE': tmp})
                self.assertEqual(result.returncode == 0, accepted, result.stderr)
        script = 'set -euo pipefail\nATTESTATION_RUN_ID=123\ngh_read() { return 1; }\n' + function
        result = subprocess.run(['bash', '-c', script + '\nverify_attestation_runners'], capture_output=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
