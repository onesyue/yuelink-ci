from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release.sh"
TAG = "v9.8.7"
TAG_OBJECT = "7" * 40
BUILDER = "b" * 40
SOURCE = "a" * 40
RUN = "12345"
MESSAGE = (
    f"YueLink public builder {TAG}\n\n"
    f"ATTESTED_SOURCE_COMMIT={SOURCE}\n"
    f"SOURCE_ATTESTATION_RUN_ID={RUN}"
)
SIGNATURE = (
    "-----BEGIN PGP SIGNATURE-----\n\n"
    "iQEzBAABCAAdFiEEfixturefixturefixturefixturefixturefixture\n"
    "=ABCD\n"
    "-----END PGP SIGNATURE-----\n"
)


def shell_function(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    end = source.index("\n}\n", start) + len("\n}\n")
    return source[start:end]


class ReleaseRemoteTagBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("jq") is None:
            self.fail("jq is required by release.sh and its behavior tests")
        release = RELEASE.read_text(encoding="utf-8")
        self.inspector = shell_function(release, "inspect_remote_public_tag")

    def run_inspector(
        self,
        *,
        message: str = MESSAGE,
        signature: str | None = SIGNATURE,
        verified: bool = True,
        reason: str = "valid",
        tag: str = TAG,
        builder: str = BUILDER,
        object_type: str = "commit",
        ref_type: str = "tag",
    ) -> subprocess.CompletedProcess[str]:
        ref_json = json.dumps(
            {"object": {"sha": TAG_OBJECT, "type": ref_type}},
            separators=(",", ":"),
        )
        verification: dict[str, object] = {
            "verified": verified,
            "reason": reason,
        }
        if signature is not None:
            verification["signature"] = signature
        tag_json = json.dumps(
            {
                "tag": tag,
                "object": {"sha": builder, "type": object_type},
                "message": message,
                "verification": verification,
            },
            separators=(",", ":"),
        )
        script = (
            "set -u\n"
            + self.inspector
            + "\n"
            + "gh() {\n"
            + "  case \"$*\" in\n"
            + "    *git/ref/tags/*) printf '%s' \"$FAKE_REF_JSON\" ;;\n"
            + "    *git/tags/*) printf '%s' \"$FAKE_TAG_JSON\" ;;\n"
            + "    *) return 99 ;;\n"
            + "  esac\n"
            + "}\n"
            + "inspect_remote_public_tag\n"
        )
        return subprocess.run(
            ["/bin/bash", "-c", script],
            env={
                **os.environ,
                "TAG": TAG,
                "BUILDER_COMMIT": BUILDER,
                "PUBLIC_TAG_MESSAGE": MESSAGE,
                "FAKE_REF_JSON": ref_json,
                "FAKE_TAG_JSON": tag_json,
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_exact_body_without_api_signature_suffix_is_accepted(self) -> None:
        result = self.run_inspector(message=MESSAGE, signature=None)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_exact_body_plus_verified_api_signature_is_accepted(self) -> None:
        result = self.run_inspector(message=MESSAGE + "\n" + SIGNATURE)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_stale_or_extended_binding_is_rejected_even_with_signature(self) -> None:
        messages = (
            MESSAGE.replace(f"SOURCE_ATTESTATION_RUN_ID={RUN}", "SOURCE_ATTESTATION_RUN_ID=9"),
            MESSAGE + "\nUNEXPECTED=1",
            MESSAGE + "\n\n" + SIGNATURE,
            MESSAGE + "\n" + SIGNATURE + "UNEXPECTED",
        )
        for message in messages:
            with self.subTest(message=message):
                result = self.run_inspector(message=message)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn("exact source/attestation message", result.stdout)

    def test_suffix_must_equal_one_well_formed_api_signature(self) -> None:
        malformed = (
            "not an armored signature",
            SIGNATURE + SIGNATURE,
            SIGNATURE.replace("-----END PGP SIGNATURE-----", "-----END OTHER-----"),
            SIGNATURE.replace("\n", "\r\n"),
        )
        for signature in malformed:
            with self.subTest(signature=signature):
                result = self.run_inspector(
                    message=MESSAGE + "\n" + signature,
                    signature=signature,
                )
                self.assertEqual(result.returncode, 2, result.stdout)

        different = SIGNATURE.replace("=ABCD", "=WXYZ")
        result = self.run_inspector(
            message=MESSAGE + "\n" + different,
            signature=SIGNATURE,
        )
        self.assertEqual(result.returncode, 2, result.stdout)

    def test_github_verified_valid_remains_mandatory(self) -> None:
        for verified, reason in ((False, "unsigned"), (True, "unknown_key")):
            with self.subTest(verified=verified, reason=reason):
                result = self.run_inspector(
                    message=MESSAGE + "\n" + SIGNATURE,
                    verified=verified,
                    reason=reason,
                )
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn("签名被拒绝", result.stdout)

    def test_exact_tag_object_and_builder_remain_mandatory(self) -> None:
        mutations = (
            {"tag": "v9.8.6"},
            {"builder": "c" * 40},
            {"object_type": "tag"},
            {"ref_type": "commit"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                result = self.run_inspector(
                    message=MESSAGE + "\n" + SIGNATURE,
                    **mutation,
                )
                self.assertEqual(result.returncode, 2, result.stdout)


class ReleaseSourceSnapshotFetchTests(unittest.TestCase):
    def test_snapshot_function_does_not_verify_inside_the_fetch(self) -> None:
        body = shell_function(RELEASE.read_text(encoding="utf-8"), "fetch_private_source_snapshot")
        self.assertNotIn("verify-source-version", body)
        self.assertNotIn("verify-tag", body)

    def test_runbook_documents_shallow_snapshot_fetch(self) -> None:
        text = (ROOT / "docs/RUNBOOK.md").read_text(encoding="utf-8")
        start = text.index("## Release an exact source revision")
        section = text[start:text.index("\n## ", start + 1)]
        for phrase in ("signing subkey", "Mac", "--depth=1", "three"):
            self.assertIn(phrase, section)

    def test_first_attempt_hides_git_stderr(self) -> None:
        result, events, sleeps, fields = self.run_fetch(
            [0],
            "planted ghp_SUCCESSSECRET\nAuthorization: Bearer planted\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(fields["RESULT"], "0")
        self.assertEqual(fields["FETCH_ERR"], "")
        self.assertEqual(sleeps, [])
        self.assert_fetch_shape(events, attempts=1)
        self.assertEqual(fields["SNAPSHOT"], events[0]["dir"])
        self.assertTrue(Path(fields["SNAPSHOT"]).is_dir())
        for stream in (result.stdout, result.stderr):
            self.assertNotIn("ghp_SUCCESSSECRET", stream)
            self.assertNotIn("Authorization", stream)
            self.assertNotIn("cannot fetch private source tag", stream)

    def test_second_attempt_uses_a_fresh_directory(self) -> None:
        result, events, sleeps, fields = self.run_fetch(
            [1, 0],
            stderr_by_attempt={
                0: "attempt-one ghp_FIRSTSECRET\n",
                1: "attempt-two ghp_SECONDSECRET\n",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sleeps, ["1"])
        self.assertEqual(fields["FETCH_ERR"], "")
        inits = [event for event in events if event["cmd"] == "init"]
        self.assert_fetch_shape(events, attempts=2)
        self.assertNotEqual(inits[0]["dir"], inits[1]["dir"])
        self.assertFalse(Path(inits[0]["dir"]).exists())
        self.assertEqual(fields["SNAPSHOT"], inits[1]["dir"])
        self.assertTrue(Path(inits[1]["dir"]).is_dir())
        self.assertNotIn("cannot fetch private source tag", result.stderr)
        for secret in ("ghp_FIRSTSECRET", "ghp_SECONDSECRET"):
            self.assertNotIn(secret, result.stdout)
            self.assertNotIn(secret, result.stderr)

    def test_three_failures_print_sanitized_tail(self) -> None:
        raw = [f"noise-{number:02d}" for number in range(1, 25)]
        raw[1] = (
            "early-fetch-marker https://x-access-token:"
            "ghp_EARLYSECRET@github.com/onesyue/yuelink.git"
        )
        raw.extend([
            "Authorization: Bearer ghp_HEADERSECRET",
            "fatal: unable to access 'https://x-access-token:ghp_LATESECRET@"
            "github.com/onesyue/yuelink.git/': LibreSSL SSL_connect: SSL_ERROR_SYSCALL",
            "noise-27",
            "Authorization: Basic ghp_BASICSECRET",
            "noise-29",
            "remote: token ghp_BARESECRET rejected",
        ])
        self.assertEqual(len(raw), 30)
        result, events, sleeps, fields = self.run_fetch([1, 1, 1], "\n".join(raw) + "\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(fields["RESULT"], "1")
        self.assertEqual(sleeps, ["1", "2"])
        inits = self.assert_fetch_shape(events, attempts=3)
        self.assertEqual(len({event["dir"] for event in inits}), 3)
        self.assertFalse(Path(inits[0]["dir"]).exists())
        self.assertFalse(Path(inits[1]["dir"]).exists())
        self.assertEqual(fields["SNAPSHOT"], inits[2]["dir"])
        self.assertTrue(Path(inits[2]["dir"]).is_dir())
        self.assertIn("::notice::private source tag fetch failed; retry 1/3.", result.stderr)
        self.assertIn("::notice::private source tag fetch failed; retry 2/3.", result.stderr)
        self.assertNotIn("retry 3/3", result.stderr)
        error = "::error::cannot fetch private source tag; release admission rejected"
        stderr_lines = result.stderr.splitlines()
        self.assertEqual(stderr_lines.count(error), 1)
        diagnosis = stderr_lines[stderr_lines.index(error) + 1:]
        expected = [f"noise-{number:02d}" for number in range(9, 25)]
        expected.append(
            "fatal: unable to access 'https://github.com/onesyue/yuelink.git/': "
            "LibreSSL SSL_connect: SSL_ERROR_SYSCALL"
        )
        expected.extend(["noise-27", "noise-29", "remote: token [redacted] rejected"])
        self.assertEqual(len(expected), 20)
        self.assertEqual(diagnosis, expected)
        for secret in (
            "ghp_",
            "Authorization",
            "HEADERSECRET",
            "LATESECRET",
            "EARLYSECRET",
            "BASICSECRET",
            "BARESECRET",
            "early-fetch-marker",
            "noise-01",
            "x-access-token",
        ):
            self.assertNotIn(secret, result.stderr)
            self.assertNotIn(secret, result.stdout)

    def assert_fetch_shape(self, events: list[dict[str, object]], attempts: int) -> list[dict[str, object]]:
        inits = [event for event in events if event["cmd"] == "init"]
        fetches = [event for event in events if event["cmd"] == "fetch"]
        self.assertEqual(len(inits), attempts)
        self.assertEqual(len(fetches), attempts)
        for init, fetch in zip(inits, fetches):
            self.assertTrue(init["empty"])
            self.assertEqual(fetch["leaked"], [])
            self.assertEqual(
                fetch["argv"],
                [
                    "-C", init["dir"],
                    "-c", "credential.helper=",
                    "-c", "credential.helper=!gh auth git-credential",
                    "-c", "http.lowSpeedLimit=1000",
                    "-c", "http.lowSpeedTime=60",
                    "fetch", "--depth=1", "--quiet", "--no-tags",
                    "https://github.com/onesyue/yuelink.git",
                    "refs/tags/v9.8.7:refs/tags/v9.8.7",
                ],
            )
        return inits

    def run_fetch(
        self,
        statuses: list[int],
        stderr_text: str = "",
        stderr_by_attempt: dict[int, str] | None = None,
    ):
        function = shell_function(
            RELEASE.read_text(encoding="utf-8"), "fetch_private_source_snapshot"
        )
        directory = tempfile.mkdtemp(prefix="yuelink-ci-snapshot-test-")
        self.addCleanup(shutil.rmtree, directory, True)
        root = Path(directory)
        (root / "statuses.json").write_text(json.dumps(statuses), encoding="utf-8")
        if stderr_by_attempt is None:
            (root / "stderr.txt").write_text(stderr_text, encoding="utf-8")
        else:
            for attempt, text in stderr_by_attempt.items():
                (root / f"stderr-{attempt}.txt").write_text(text, encoding="utf-8")
        git = root / "git"
        git.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "root = Path(os.environ['SNAPSHOT_TEST_DIR'])\n"
            "argv = sys.argv[1:]\n"
            "log = root / 'events.json'\n"
            "events = json.loads(log.read_text()) if log.exists() else []\n"
            "def save(event):\n"
            "    events.append(event)\n"
            "    log.write_text(json.dumps(events))\n"
            "if argv[:2] == ['init', '-q'] and len(argv) == 3:\n"
            "    directory = argv[2]\n"
            "    save({'cmd': 'init', 'dir': directory, 'empty': os.listdir(directory) == []})\n"
            "    sys.exit(0)\n"
            "if 'fetch' in argv:\n"
            "    names = ('GIT_TRACE', 'GIT_CURL_VERBOSE', 'GIT_TRACE_CURL',\n"
            "             'GIT_TRACE_PACKET', 'GIT_TRACE_PERFORMANCE')\n"
            "    leaked = [name for name in names if name in os.environ]\n"
            "    count = sum(1 for event in events if event.get('cmd') == 'fetch')\n"
            "    stderr_path = root / f'stderr-{count}.txt'\n"
            "    if not stderr_path.exists():\n"
            "        stderr_path = root / 'stderr.txt'\n"
            "    sys.stderr.write(stderr_path.read_text())\n"
            "    save({'cmd': 'fetch', 'argv': argv, 'leaked': leaked})\n"
            "    if leaked:\n"
            "        (root / 'TRACE_STILL_SET').write_text(' '.join(leaked))\n"
            "        sys.exit(97)\n"
            "    statuses = json.loads((root / 'statuses.json').read_text())\n"
            "    sys.exit(int(statuses[min(count, len(statuses) - 1)]))\n"
            "(root / 'UNEXPECTED').write_text(' '.join(argv))\n"
            "sys.exit(98)\n",
            encoding="utf-8",
        )
        git.chmod(0o755)
        sleep = root / "sleep"
        sleep.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$1" >> "$SNAPSHOT_TEST_DIR/sleeps"\n',
            encoding="utf-8",
        )
        sleep.chmod(0o755)
        script = (
            "set -euo pipefail\n"
            "SOURCE_SNAPSHOT=\"\"\n"
            "FETCH_ERR=\"\"\n"
            "TAG=v9.8.7\n"
            + function
            + "\nstatus=0\n"
            "fetch_private_source_snapshot || status=$?\n"
            "printf 'RESULT=%s\\nSNAPSHOT=%s\\nFETCH_ERR=%s\\n' "
            "\"$status\" \"$SOURCE_SNAPSHOT\" \"$FETCH_ERR\"\n"
            "exit \"$status\"\n"
        )
        result = subprocess.run(
            ["/bin/bash", "-c", script],
            env={
                **os.environ,
                "SNAPSHOT_TEST_DIR": directory,
                "PATH": directory + os.pathsep + os.environ["PATH"],
                "GIT_TRACE": "1",
                "GIT_TRACE_PACKET": "1",
                "GIT_TRACE_CURL": "1",
                "GIT_CURL_VERBOSE": "1",
                "GIT_TRACE_PERFORMANCE": "1",
            },
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        self.cleanup_fetch_artifacts(root, result.stdout)
        self.assertFalse((root / "UNEXPECTED").exists(), result.stderr)
        self.assertFalse((root / "TRACE_STILL_SET").exists(), result.stderr)
        events_path = root / "events.json"
        events = json.loads(events_path.read_text(encoding="utf-8")) if events_path.exists() else []
        pauses = root / "sleeps"
        sleeps = pauses.read_text(encoding="utf-8").splitlines() if pauses.exists() else []
        fields = {}
        for line in result.stdout.splitlines():
            key, value = line.split("=", 1)
            fields[key] = value
        return result, events, sleeps, fields

    def cleanup_fetch_artifacts(self, root: Path, stdout: str) -> None:
        events_path = root / "events.json"
        if events_path.is_file():
            for event in json.loads(events_path.read_text(encoding="utf-8")):
                if event.get("cmd") == "init":
                    self.addCleanup(shutil.rmtree, event["dir"], True)
        for line in stdout.splitlines():
            if line.startswith("FETCH_ERR="):
                path = line.split("=", 1)[1]
                if path:
                    self.addCleanup(lambda fetch_err=path: Path(fetch_err).unlink(missing_ok=True))

if __name__ == "__main__":
    unittest.main()
