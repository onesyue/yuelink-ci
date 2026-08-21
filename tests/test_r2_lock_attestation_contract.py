from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
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
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertNotIn("self-hosted", text)
        self.assertIn(
            "actions/attest-build-provenance@a2bbfa25375fe432b6a289bc6b6cd05ecd0c4c32",
            text,
        )
        self.assertIn(
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
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
            """#!/bin/sh
set -eu
printf '%s\\n' \"$*\" >> \"$FAKE_CURL_LOG\"
case \"$*\" in
  *api.cloudflare.com*)
    printf '%s' \"$FAKE_LOCK_JSON\"
    ;;
  *)
    output=''
    previous=''
    for arg in \"$@\"; do
      if [ \"$previous\" = --output ]; then output=$arg; fi
      previous=$arg
    done
    [ -n \"$output\" ]
    printf '%s' \"$FAKE_CANDIDATE_JSON\" > \"$output\"
    printf 'https://yuetong.app/v1.3.14/update.candidate.json'
    ;;
esac
"""
        )
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
