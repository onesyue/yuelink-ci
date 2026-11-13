"""Reject a repository-wide dial-gate concurrency group.

GitHub keeps one running job and one pending job for a group, and cancels
the older pending job when another run queues. A constant group name therefore
cancels a dial leg that has not started. The macOS leg has a run-scoped group. Every other
platform keeps a separate group, and every group name includes this run.
"""

from __future__ import annotations

import hashlib
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
        re.DOTALL,
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
            "macos must use its run-scoped macos-26-dial group"
        )
    if "{2}" in macos_template:
        raise DialConcurrencyError(
            "macos must use one group inside the run"
        )
    other_template = _format_template(right, "other-platform group", "matrix.name")
    if "{2}" not in other_template:
        raise DialConcurrencyError(
            "other platforms must stay on separate groups"
        )
    macos_rows = re.findall(r"(?m)^            os: macos-26$", job)
    if len(macos_rows) != 1:
        raise DialConcurrencyError(
            f"expected one macos-26 dial leg, found {len(macos_rows)}"
        )


def _single_job(workflow: str, name: str) -> str:
    jobs = re.findall(
        rf"(?m)^  {re.escape(name)}:\n(.*?)(?=^  [A-Za-z0-9_]+:\n|\Z)",
        workflow,
        re.DOTALL,
    )
    if len(jobs) != 1:
        raise DialConcurrencyError(f"expected one {name} job, found {len(jobs)}")
    return jobs[0]


def _checkout_ref(job: str, name: str) -> str:
    steps = re.findall(
        rf"(?m)^      - name: {re.escape(name)}\n(.*?)(?=^      - |\Z)",
        job,
        re.DOTALL,
    )
    if len(steps) != 1:
        raise DialConcurrencyError(f"expected one {name} checkout")
    if not re.search(r"(?m)^        uses: actions/checkout@[0-9a-f]{40}(?: #.*)?$", steps[0]):
        raise DialConcurrencyError(f"{name} is not a pinned checkout step")
    inputs = re.findall(
        r"(?m)^        with:\n(.*?)(?=^        [A-Za-z_][A-Za-z0-9_-]*:|\Z)",
        steps[0],
        re.DOTALL,
    )
    if len(inputs) != 1:
        raise DialConcurrencyError(f"expected one with block in {name}")
    refs = re.findall(r"(?m)^          ref: (.+)$", inputs[0])
    if len(refs) != 1:
        raise DialConcurrencyError(f"expected one ref in {name}")
    repositories = re.findall(r"(?m)^          repository: (.+)$", inputs[0])
    if repositories != ["onesyue/yuelink"]:
        raise DialConcurrencyError(f"{name} must read the client repository")
    return refs[0].strip()


def validate_pull_request_dial_ref(caller: str, dial: str) -> str:
    """Validate the input binding, returning this caller's declared PR ref.

    The workflow owns the PR reference, including exact-source controls. Tests
    must follow that value instead of freezing a client branch or source SHA.
    Dispatch overrides and release-plan authority remain separate required arms.
    """
    if "feat/real-dial-gate-20261111" in caller or "feat/real-dial-gate-20261111" in dial:
        raise DialConcurrencyError("dial still names the deleted feature branch")
    call = _single_job(caller, "dial_gate")
    if "    uses: ./.github/workflows/dial-gate.yml\n" not in call:
        raise DialConcurrencyError("caller must invoke the shared dial workflow")
    bindings = re.findall(r"(?m)^      candidate_ref: (.+)$", call)
    if len(bindings) != 1:
        raise DialConcurrencyError("expected one caller candidate_ref input")
    expression = re.fullmatch(
        r"\$\{\{\s*github\.event\.inputs\.dialgate_candidate_ref\s*\|\|\s*"
        r"\(github\.event_name\s*==\s*'pull_request'\s*&&\s*'([^']+)'\)\s*\|\|\s*"
        r"needs\.plan\.outputs\.source_commit\s*\}\}",
        bindings[0],
    )
    if expression is None:
        raise DialConcurrencyError("candidate must bind dispatch, PR ref and planned source in order")
    reference = expression.group(1)
    if (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", reference) is None
        or any(part in reference for part in ("..", "//"))
        or reference.endswith(("/", ".", ".lock"))
    ):
        raise DialConcurrencyError("invalid declared client ref")
    for job_name, checkout in (
        ("dial_gate", "Checkout dial-gate candidate"),
        ("linux_auth_storage", "Checkout candidate"),
        ("ios_unsigned_compile", "Checkout candidate"),
    ):
        job = _single_job(dial, job_name)
        if _checkout_ref(job, checkout) != "${{ inputs.candidate_ref || inputs.source_sha }}":
            raise DialConcurrencyError(f"{job_name} candidate checkout bypasses source inputs")
    baseline = _checkout_ref(
        _single_job(dial, "dial_gate"), "Checkout frozen promoted dial baseline"
    )
    if baseline != "${{ needs.baseline_binding.outputs.baseline_source }}":
        raise DialConcurrencyError("dial baseline is not the frozen promoted source")
    return reference



class DialGateConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.caller = WORKFLOW.read_text(encoding="utf-8")
        self.workflow = (ROOT / ".github" / "workflows" / "dial-gate.yml").read_text(
            encoding="utf-8"
        )
        self.assertGreater(len(self.caller), 10000)
        self.assertGreater(len(self.workflow), 5000)

    def test_live_workflow_keys_each_run(self) -> None:
        validate_dial_gate_concurrency(self.workflow)
        validate_pull_request_dial_ref(self.caller, self.workflow)
        self.assertEqual(self.workflow.count(MACOS_ARM), 1)
        self.assertEqual(self.workflow.count(OTHER_ARM), 1)
        self.assertNotIn(MACOS_ARM, self.caller)
        self.assertNotIn("'yuelink-ci-macos-26-dial'", self.workflow)
        self.assertNotIn("feat/real-dial-gate-20261111", self.workflow)
        self.assertNotIn("feat/real-dial-gate-20261111", self.caller)

    def _with_pr_ref(self, reference: str) -> str:
        current = validate_pull_request_dial_ref(self.caller, self.workflow)
        needle = f"github.event_name == 'pull_request' && '{current}'"
        self.assertEqual(self.caller.count(needle), 1)
        return self.caller.replace(
            needle, f"github.event_name == 'pull_request' && '{reference}'", 1
        )

    def test_deleted_dial_branch_is_rejected(self) -> None:
        mutated = self._with_pr_ref("feat/real-dial-gate-20261111")
        with self.assertRaises(DialConcurrencyError):
            validate_pull_request_dial_ref(mutated, self.workflow)

    def test_source_changes_follow_the_workflows_own_reference(self) -> None:
        # Source identities are derived from changing fixture bytes, never a
        # production SHA. Both old and new exact-source controls are legitimate.
        sources = [hashlib.sha1(data).hexdigest() for data in (b"source before", b"source after")]
        self.assertNotEqual(*sources)
        for source in sources:
            with self.subTest(source=source):
                caller = self._with_pr_ref(source)
                self.assertEqual(validate_pull_request_dial_ref(caller, self.workflow), source)
                stale = self.workflow.replace(
                    "ref: ${{ inputs.candidate_ref || inputs.source_sha }}",
                    f"ref: {sources[0]}", 1,
                )
                with self.assertRaises(DialConcurrencyError):
                    validate_pull_request_dial_ref(caller, stale)
        branch = "refs/heads/" + sources[1][:12]
        self.assertEqual(
            validate_pull_request_dial_ref(self._with_pr_ref(branch), self.workflow), branch
        )

    def test_missing_duplicate_or_bypassed_caller_bindings_are_rejected(self) -> None:
        call = _single_job(self.caller, "dial_gate")
        binding_match = re.search(r"(?m)^      candidate_ref: .+$", call)
        assert binding_match is not None, "live caller candidate_ref fixture is missing"
        binding = binding_match.group(0)
        changes = (
            self.caller.replace("  dial_gate:\n", "  removed_dial_gate:\n", 1),
            self.caller + "\n  dial_gate:\n" + call,
            self.caller.replace(binding, "", 1),
            self.caller.replace(binding, binding + "\n" + binding, 1),
            self.caller.replace("github.event.inputs.dialgate_candidate_ref || ", "", 1),
            self.caller.replace("|| needs.plan.outputs.source_commit", "|| github.sha", 1),
            self.caller.replace("uses: ./.github/workflows/dial-gate.yml", "uses: ./other.yml", 1),
        )
        for index, caller in enumerate(changes):
            with self.subTest(index=index), self.assertRaises(DialConcurrencyError):
                validate_pull_request_dial_ref(caller, self.workflow)

    def test_each_candidate_checkout_must_consume_both_source_inputs(self) -> None:
        for name in ("dial_gate", "linux_auth_storage", "ios_unsigned_compile"):
            job = _single_job(self.workflow, name)
            reference = "ref: ${{ inputs.candidate_ref || inputs.source_sha }}"
            self.assertEqual(job.count(reference), 1)
            for replacement in (
                "ref: ${{ github.sha }}", "ref: ${{ inputs.candidate_ref }}",
                "ref: ${{ inputs.source_sha }}", "", reference + "\n          " + reference,
            ):
                with self.subTest(job=name, replacement=replacement):
                    workflow = self.workflow.replace(job, job.replace(reference, replacement, 1), 1)
                    with self.assertRaises(DialConcurrencyError):
                        validate_pull_request_dial_ref(self.caller, workflow)
            checkout_name = "Checkout dial-gate candidate" if name == "dial_gate" else "Checkout candidate"
            for old, new in (
                (f"- name: {checkout_name}\n", f"- name: Removed {checkout_name}\n"),
                ("repository: onesyue/yuelink", "repository: onesyue/yuelink-ci"),
            ):
                changed = job.replace(old, new, 1)
                self.assertNotEqual(changed, job)
                with self.subTest(job=name, boundary=old), self.assertRaises(DialConcurrencyError):
                    validate_pull_request_dial_ref(
                        self.caller, self.workflow.replace(job, changed, 1)
                    )
            with self.subTest(job=name, removed=True), self.assertRaises(DialConcurrencyError):
                validate_pull_request_dial_ref(
                    self.caller, self.workflow.replace(f"  {name}:\n", f"  removed_{name}:\n", 1)
                )

    def test_checkout_inputs_must_belong_to_one_with_block(self) -> None:
        for name, checkout in (
            ("dial_gate", "Checkout dial-gate candidate"),
            ("linux_auth_storage", "Checkout candidate"),
            ("ios_unsigned_compile", "Checkout candidate"),
        ):
            job = _single_job(self.workflow, name)
            step_match = re.search(
                rf"(?m)^      - name: {re.escape(checkout)}\n.*?(?=^      - |\Z)",
                job, re.DOTALL,
            )
            assert step_match is not None, f"live checkout fixture is missing: {checkout}"
            step = step_match.group(0)
            reference = "          ref: ${{ inputs.candidate_ref || inputs.source_sha }}\n"
            self.assertEqual(step.count("        with:\n"), 1)
            self.assertEqual(step.count(reference), 1)
            mutations = {
                "missing with": step.replace("        with:\n", "", 1),
                "renamed with": step.replace("        with:\n", "        env:\n", 1),
                "ref moved to env": step.replace(reference, "", 1) + "        env:\n" + reference,
                "duplicate with": step + "        with:\n" + reference,
            }
            for case, changed in mutations.items():
                workflow = self.workflow.replace(job, job.replace(step, changed, 1), 1)
                with self.subTest(job=name, case=case), self.assertRaises(DialConcurrencyError):
                    validate_pull_request_dial_ref(self.caller, workflow)

    def test_malformed_declared_reference_is_rejected(self) -> None:
        for reference in ("", " ", "../old", "refs//heads/old", "ref.lock"):
            with self.subTest(reference=reference), self.assertRaises(DialConcurrencyError):
                validate_pull_request_dial_ref(self._with_pr_ref(reference), self.workflow)

    def test_dial_baseline_binding_is_rejected_when_replaced_with_a_tag(self) -> None:
        mutated = self._replace_once(
            "          ref: ${{ needs.baseline_binding.outputs.baseline_source }}",
            "          ref: v1.3.174",
        )
        with self.assertRaises(DialConcurrencyError):
            validate_pull_request_dial_ref(self.caller, mutated)

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

    def test_missing_macos_leg_is_rejected(self) -> None:
        needle = "            os: macos-26\n"
        self.assertEqual(self.workflow.count(needle), 1)
        mutated = self.workflow.replace(needle, "            os: macos-14\n", 1)
        self.assertEqual(mutated.count(needle), 0)
        with self.assertRaises(DialConcurrencyError):
            validate_dial_gate_concurrency(mutated)

    def test_missing_concurrency_block_is_rejected(self) -> None:
        mutated = self._replace_once(
            "    concurrency:\n      group:",
            "    queue:\n      group:",
        )
        with self.assertRaises(DialConcurrencyError):
            validate_dial_gate_concurrency(mutated)
