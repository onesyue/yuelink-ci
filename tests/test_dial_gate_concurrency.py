"""Reject a repository-wide dial-gate concurrency group.

GitHub keeps one running job and one pending job for a group, and cancels
the older pending job when another run queues. A constant group name therefore
cancels a dial leg that has not started. The two macOS legs of one run still
share a group so iOS waits for the single macos-26 runner. Every other
platform keeps a separate group, and every group name includes this run.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"

MACOS_ARM = (
    "format('{0}-{1}-macos-26-dial', github.workflow, github.run_id)"
)
OTHER_ARM = (
    "format('{0}-{1}-dial-{2}', github.workflow, github.run_id, matrix.name)"
)


class DialConcurrencyError(Exception):
    """The dial-gate queue can cancel a leg outside this run."""


def dial_gate_job(workflow: str) -> str:
    match = re.search(
        r"(?m)^  dial_gate:\n(.*?)(?=^  [A-Za-z0-9_]+:\n|\Z)",
        workflow,
        re.S,
    )
    if not match:
        raise DialConcurrencyError("dial_gate job is missing")
    job = match.group(0)
    if len(job) < 500:
        raise DialConcurrencyError("dial_gate job was not actually read")
    return job


def concurrency_group(job: str) -> str:
    match = re.search(
        r"(?m)^    concurrency:\n      group: (.+)\n      cancel-in-progress: (true|false)\n",
        job,
    )
    if not match:
        raise DialConcurrencyError(
            "dial_gate concurrency group is missing or split across lines"
        )
    group, cancel = match.group(1), match.group(2)
    if cancel != "false":
        raise DialConcurrencyError(
            "cancel-in-progress must stay false so the waiting macOS leg remains queued"
        )
    return group


def _format_template(arm: str, label: str, extra: str | None) -> str:
    extra_pattern = "" if extra is None else rf",\s*{extra}"
    matched = re.fullmatch(
        rf"format\('([^']*)',\s*github\.workflow,\s*github\.run_id{extra_pattern}\)",
        arm.strip(),
    )
    if not matched:
        raise DialConcurrencyError(
            f"{label} must be format() of github.workflow and github.run_id"
        )
    template = matched.group(1)
    if "{0}" not in template or "{1}" not in template:
        raise DialConcurrencyError(
            f"{label} drops the workflow or run id from the group name"
        )
    return template


def validate_dial_gate_concurrency(workflow: str) -> None:
    job = dial_gate_job(workflow)
    group = concurrency_group(job)
    wrapped = re.fullmatch(r"\$\{\{\s*(.+)\s*\}\}", group.strip())
    if not wrapped:
        raise DialConcurrencyError("group must be one expression")
    left, separator, right = wrapped.group(1).partition(" || ")
    if separator != " || " or " || " in right:
        raise DialConcurrencyError(
            "group must be one macos-26 branch or one per-platform branch"
        )
    prefix, amp, macos = left.partition(" && ")
    if prefix.strip() != "matrix.os == 'macos-26'" or amp != " && ":
        raise DialConcurrencyError(
            "only the macos-26 legs share a group"
        )
    macos_template = _format_template(macos, "macos-26 group", None)
    if "macos-26-dial" not in macos_template:
        raise DialConcurrencyError(
            "macos and ios must share a macos-26-dial group"
        )
    if "{2}" in macos_template:
        raise DialConcurrencyError(
            "macos and ios must share one group inside the run"
        )
    other_template = _format_template(right, "other-platform group", "matrix.name")
    if "{2}" not in other_template:
        raise DialConcurrencyError(
            "other platforms must stay on separate groups"
        )
    macos_rows = re.findall(r"(?m)^            os: macos-26$", job)
    if len(macos_rows) < 2:
        raise DialConcurrencyError(
            f"expected at least two macos-26 dial legs, found {len(macos_rows)}"
        )


class DialGateConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertGreater(len(self.workflow), 10000)

    def test_live_workflow_keys_each_run(self) -> None:
        validate_dial_gate_concurrency(self.workflow)
        self.assertEqual(self.workflow.count(MACOS_ARM), 1)
        self.assertEqual(self.workflow.count(OTHER_ARM), 1)
        self.assertNotIn("'yuelink-ci-macos-26-dial'", self.workflow)

    def _replace_once(self, old: str, new: str) -> str:
        count = self.workflow.count(old)
        self.assertEqual(count, 1, old)
        return self.workflow.replace(old, new, 1)

    def test_repository_wide_macos_literal_is_rejected(self) -> None:
        mutated = self._replace_once(MACOS_ARM, "'yuelink-ci-macos-26-dial'")
        with self.assertRaises(DialConcurrencyError):
            validate_dial_gate_concurrency(mutated)

    def test_unused_run_arguments_are_rejected(self) -> None:
        mutated = self._replace_once(
            MACOS_ARM,
            "format('yuelink-ci-macos-26-dial', github.workflow, github.run_id)",
        )
        with self.assertRaises(DialConcurrencyError):
            validate_dial_gate_concurrency(mutated)

    def test_workflow_name_without_run_id_is_rejected(self) -> None:
        mutated = self._replace_once(
            MACOS_ARM,
            "format('{0}-macos-26-dial', github.workflow)",
        )
        with self.assertRaises(DialConcurrencyError):
            validate_dial_gate_concurrency(mutated)

    def test_repository_wide_other_platform_group_is_rejected(self) -> None:
        mutated = self._replace_once(
            OTHER_ARM,
            "format('yuelink-ci-dial-{0}', matrix.name)",
        )
        with self.assertRaises(DialConcurrencyError):
            validate_dial_gate_concurrency(mutated)

    def test_one_macos_leg_is_rejected(self) -> None:
        needle = "            os: macos-26\n"
        self.assertEqual(self.workflow.count(needle), 2)
        mutated = self.workflow.replace(needle, "            os: macos-14\n", 1)
        self.assertEqual(mutated.count(needle), 1)
        with self.assertRaises(DialConcurrencyError):
            validate_dial_gate_concurrency(mutated)

    def test_missing_concurrency_block_is_rejected(self) -> None:
        mutated = self._replace_once(
            "    concurrency:\n      group:",
            "    queue:\n      group:",
        )
        with self.assertRaises(DialConcurrencyError):
            validate_dial_gate_concurrency(mutated)
