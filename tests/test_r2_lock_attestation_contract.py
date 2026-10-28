from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts/create-r2-lock-attestation.sh"
WORKFLOW = ROOT / ".github/workflows/r2-lock-attestation.yml"
SOURCE = "a" * 40
BUILDER = "b" * 40
CONTROL = BUILDER
CHALLENGE = "d" * 64
RUN_ID = "32512971575"


class R2LockAttestationContractTest(unittest.TestCase):
    def test_workflow_is_manual_pinned_and_minimally_privileged(self) -> None:
        text = WORKFLOW.read_text()
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("pull_request:", text)
        self.assertNotIn("push:", text)
        self.assertIn("runs-on: ubuntu-24.04", text)
        self.assertNotIn("self-hosted", text)
        self.assertIn(
            "actions/attest-build-provenance@a2bbfa25375fe432b6a289bc6b6cd05ecd0c4c32",
            text,
        )
        self.assertIn(
            "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
            text,
        )
        self.assertIn(
            "CLOUDFLARE_R2_CONFIG_TOKEN: ${{ secrets.CLOUDFLARE_R2_CONFIG_TOKEN }}",
            text,
        )
        self.assertNotIn("R2_KEY_ID", text)
        self.assertNotIn("R2_APP_KEY", text)
        self.assertNotIn("UPDATE_MANIFEST_ED25519_PRIVATE_KEY_B64", text)

    def _fixture(self, *, valid_lock: bool = True, run_success: bool = True):
        stack = tempfile.TemporaryDirectory()
        root = Path(stack.name)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        candidate = {
            "schemaVersion": 1,
            "version": "1.3.14",
            "channel": "stable",
            "sourceCommit": SOURCE,
            "platforms": {f"platform-{index}": {} for index in range(9)},
        }
        candidate_bytes = json.dumps(candidate, separators=(",", ":")).encode()
        digest = hashlib.sha256(candidate_bytes).hexdigest()
        call_log = root / "curl.log"

        curl = bin_dir / "curl"
        curl.write_text(
            f"#!{sys.executable}\n" + r"""
import json
import os
import pathlib
import sys
args = sys.argv[1:]
root = pathlib.Path(os.environ["RUNNER_TEMP"])
clock = root / "clock"
now = int(clock.read_text()) if clock.exists() else 0
kind = "lock" if "api.cloudflare.com" in args[-1] else "candidate"
count_file = root / (kind + ".count")
count = int(count_file.read_text()) if count_file.exists() else 0
count_file.write_text(str(count + 1))
with open(os.environ["FAKE_CURL_LOG"], "a") as log:
    log.write(" ".join(args) + "\n")
assert args[args.index("--retry") + 1] == "0"
timeout = float(args[args.index("--max-time") + 1])
assert 0 < timeout <= (10 if kind == "lock" else 15)
plan = json.loads(os.environ.get("FAKE_PLAN", "{}"))
steps = plan.get(kind, [])
step = steps[count] if count < len(steps) else {}
elapsed = step.get("elapsed", 0)
rc = step.get("rc", 0)
status = step.get("status", "200")
if elapsed > timeout * 1000:
    elapsed = round(timeout * 1000)
    rc, status = 28, "000"
clock.write_text(str(now + elapsed))
output = pathlib.Path(args[args.index("--output") + 1])
output.write_text(os.environ["FAKE_LOCK_JSON" if kind == "lock" else "FAKE_CANDIDATE_JSON"])
print(status + " " + args[-1], end="")
sys.exit(rc)
"""
        )
        # Only the generator's clock command resolves this shim. Mock curl uses
        # the absolute interpreter above, so virtual time needs no real waits.
        clock_command = bin_dir / "python3"
        clock_command.write_text(
            '#!/bin/sh\ncat "$RUNNER_TEMP/clock"\n'
        )
        (root / "clock").write_text("0")
        sleep = bin_dir / "sleep"
        sleep.write_text(
            f"#!{sys.executable}\n" + r"""
import os
import pathlib
import sys
root = pathlib.Path(os.environ["RUNNER_TEMP"])
clock = root / "clock"
with (root / "sleep.log").open("a") as log:
    log.write(sys.argv[1] + "\n")
clock.write_text(str(int(clock.read_text()) + int(sys.argv[1]) * 1000
                     + int(os.environ.get("FAKE_SLEEP_OVERRUN", "0"))))
"""
        )
        for command in (clock_command, sleep):
            command.chmod(command.stat().st_mode | stat.S_IXUSR)
        gh = bin_dir / "gh"
        gh.write_text(
            """#!/bin/sh
set -eu
case \"$*\" in
  *actions/workflows/build.yml*) printf '%s' \"$FAKE_WORKFLOW_JSON\" ;;
  *actions/runs/*) printf '%s' \"$FAKE_RUN_JSON\" ;;
  *) exit 97 ;;
esac
"""
        )
        curl.chmod(curl.stat().st_mode | stat.S_IXUSR)
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        lock = {
            "success": True,
            "errors": [],
            "result": {
                "rules": [
                    {
                        "id": "yuelink-release-versioned-indefinite",
                        "enabled": valid_lock,
                        "prefix": "v",
                        "condition": {"type": "Indefinite"},
                    }
                ]
            },
        }
        workflow = {
            "id": 123,
            "name": "Build YueLink",
            "path": ".github/workflows/build.yml",
            "state": "active",
        }
        run = {
            "id": int(RUN_ID),
            "workflow_id": 123,
            "name": "Build YueLink",
            "path": ".github/workflows/build.yml",
            "event": "push",
            "status": "completed",
            "conclusion": "success" if run_success else "failure",
            "head_branch": "v1.3.14",
            "head_sha": BUILDER,
            "repository": {"full_name": "onesyue/yuelink-ci"},
        }
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bin_dir}:{env['PATH']}",
                "GH_TOKEN": "fake-gh-token",
                "CLOUDFLARE_R2_CONFIG_TOKEN": "fake-scoped-token",
                "GITHUB_REPOSITORY": "onesyue/yuelink-ci",
                "GITHUB_SHA": CONTROL,
                "GITHUB_REF": "refs/heads/master",
                "GITHUB_RUN_ID": "987654",
                "GITHUB_RUN_ATTEMPT": "1",
                "RUNNER_TEMP": str(root),
                "FAKE_CURL_LOG": str(call_log),
                "FAKE_CANDIDATE_JSON": candidate_bytes.decode(),
                "FAKE_LOCK_JSON": json.dumps(lock),
                "FAKE_WORKFLOW_JSON": json.dumps(workflow),
                "FAKE_RUN_JSON": json.dumps(run),
            }
        )
        return stack, root, digest, env, call_log

    def _run(self, root: Path, candidate_digest: str, env: dict[str, str], **kwargs):
        output = root / "proof.json"
        args = [
            "bash",
            str(GENERATOR),
            "1.3.14",
            SOURCE,
            kwargs.get("digest", candidate_digest),
            BUILDER,
            RUN_ID,
            kwargs.get("challenge", CHALLENGE),
            str(output),
        ]
        return subprocess.run(args, env=env, text=True, capture_output=True), output

    def test_generator_binds_release_and_exact_indefinite_lock(self) -> None:
        stack, root, digest, env, _ = self._fixture()
        self.addCleanup(stack.cleanup)
        result, output = self._run(root, digest, env)
        self.assertEqual(result.returncode, 0, result.stderr)
        proof = json.loads(output.read_text())
        self.assertEqual(proof["candidateSha256"], digest)
        self.assertEqual(proof["candidateBuilderCommit"], BUILDER)
        self.assertEqual(proof["candidateBuildRunId"], RUN_ID)
        self.assertEqual(proof["challenge"], CHALLENGE)
        self.assertEqual(proof["workflowCommit"], CONTROL)
        self.assertEqual(proof["lockRule"]["conditionType"], "Indefinite")
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_candidate_failure_happens_before_cloudflare_request(self) -> None:
        stack, root, digest, env, call_log = self._fixture()
        self.addCleanup(stack.cleanup)
        result, output = self._run(root, digest, env, digest="0" * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        self.assertNotIn("api.cloudflare.com", call_log.read_text())
        self.assertEqual(int((root / "candidate.count").read_text()), 1)
        self.assertFalse((root / "sleep.log").exists())

    def test_failed_build_or_lock_is_rejected(self) -> None:
        for valid_lock, run_success in ((False, True), (True, False)):
            with self.subTest(valid_lock=valid_lock, run_success=run_success):
                stack, root, digest, env, _ = self._fixture(
                    valid_lock=valid_lock, run_success=run_success
                )
                try:
                    result, output = self._run(root, digest, env)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(output.exists())
                finally:
                    stack.cleanup()

    def test_retry_budget_and_error_classification(self) -> None:
        for kind, budget, cap, attempts in (
            ("candidate", 60000, 15000, 4), ("lock", 30000, 10000, 3)
        ):
            cases = [
                ("slow_recovers", [{"elapsed": cap + 1}], {}, True, 2, 1),
                ("429_recovers", [{"status": "429", "rc": 22}], {}, True, 2, 1),
                ("503_recovers", [{"status": "503", "rc": 22}], {}, True, 2, 1),
                ("network_recovers", [{"status": "000", "rc": 7}], {}, True, 2, 1),
                ("partial_recovers", [{"status": "200", "rc": 18}], {}, True, 2, 1),
                ("timeouts_exhaust", [{"elapsed": cap + 1}] * attempts,
                 {}, False, attempts, attempts - 1),
                ("late_success", [{"elapsed": cap + 1}] * (attempts - 1)
                 + [{"elapsed": budget - (attempts - 1) * cap
                     - (2 ** (attempts - 1) - 1) * 1000}],
                 {}, False, attempts, attempts - 1),
                ("attempts_exhaust", [{"status": "503", "rc": 22}] * attempts,
                 {}, False, attempts, attempts - 1),
                ("deadline_before_retry", [{"status": "000", "rc": 7}],
                 {"FAKE_SLEEP_OVERRUN": str(budget)}, False, 1, 1),
                ("tls_rejected", [{"status": "000", "rc": 60}], {}, False, 1, 0),
                ("oversize_rejected", [{"status": "200", "rc": 63}], {}, False, 1, 0),
            ]
            for status in ("400", "401", "403", "404", "408", "422"):
                cases.append((status, [{"status": status, "rc": 22}], {}, False, 1, 0))
            for name, steps, extra_env, success, calls, sleeps in cases:
                with self.subTest(kind=kind, case=name):
                    stack, root, digest, env, _ = self._fixture()
                    try:
                        env["FAKE_PLAN"] = json.dumps({kind: steps})
                        env.update(extra_env)
                        result, output = self._run(root, digest, env)
                        self.assertEqual(result.returncode == 0, success, result.stderr)
                        self.assertEqual(output.exists(), success)
                        self.assertEqual(int((root / (kind + ".count")).read_text()), calls)
                        sleep_log = root / "sleep.log"
                        self.assertEqual(
                            len(sleep_log.read_text().splitlines()) if sleep_log.exists() else 0,
                            sleeps,
                        )
                        if name in ("timeouts_exhaust", "late_success"):
                            self.assertIn("budget exhausted", result.stderr)
                            self.assertEqual(int((root / "clock").read_text()), budget)
                        if kind == "candidate" and not success:
                            self.assertFalse((root / "lock.count").exists())
                    finally:
                        stack.cleanup()

    def test_malformed_challenge_is_rejected_before_network(self) -> None:
        stack, root, digest, env, call_log = self._fixture()
        self.addCleanup(stack.cleanup)
        result, output = self._run(root, digest, env, challenge="abcd")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(output.exists())
        self.assertFalse(call_log.exists())

    def test_workflow_commit_must_equal_candidate_builder_commit(self) -> None:
        stack, root, digest, env, call_log = self._fixture()
        self.addCleanup(stack.cleanup)
        env["GITHUB_SHA"] = "c" * 40
        result, output = self._run(root, digest, env)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(output.exists())
        self.assertFalse(call_log.exists())


if __name__ == "__main__":
    unittest.main()
