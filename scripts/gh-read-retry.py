#!/usr/bin/env python3
"""Bound a few release reads; never replay a release write or a rejected proof."""
from __future__ import annotations

import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time

ATTEMPTS = 3
ATTEMPT_SECONDS = 60
TOTAL_SECONDS = 150
OUTPUT_LIMIT = 16 * 1024 * 1024
REPOSITORY = "onesyue/yuelink-ci"


def validate(argv: list[str]) -> Path | None:
    """Allow only the exact read families used by the two native consumers."""
    if argv[:2] == ["gh", "api"]:
        if (len(argv) not in (3, 5) or not re.fullmatch(
                r"repos/onesyue/(?:yuelink|yuelink-ci)/[A-Za-z0-9._/?&=%:-]+", argv[2])
                or len(argv) == 5 and argv[3] != "--jq"):
            raise ValueError("only a GET endpoint and optional --jq are allowed")
        return None
    if argv == ["gh", "secret", "list", "-R", REPOSITORY,
                "--json", "name", "--jq", ".[].name"]:
        return None
    if (len(argv) == 10 and argv[:3] == ["gh", "run", "download"]
            and re.fullmatch(r"[1-9][0-9]*", argv[3])
            and argv[4:7] == ["-R", REPOSITORY, "--name"]
            and re.fullmatch(r"yuelink-source-attestation-[0-9a-f]{40}", argv[7])
            and argv[8] == "--dir"):
        target = Path(argv[9])
        info = target.lstat()
        if (not target.is_absolute() or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid() or info.st_mode & 0o077
                or any(target.iterdir())):
            raise ValueError("proof destination must be an empty private owned directory")
        return target
    if (len(argv) == 17 and argv[:3] == ["gh", "attestation", "verify"]
            and not argv[3].startswith("-")
            and argv[4:10] == ["--repo", REPOSITORY, "--signer-workflow",
                REPOSITORY + "/.github/workflows/source-attestation.yml",
                "--source-ref", "refs/heads/master"]
            and argv[10] == "--source-digest" and argv[12] == "--signer-digest"
            and argv[11] == argv[13] and re.fullmatch(r"[0-9a-f]{40}", argv[11])
            and argv[14:] == ["--deny-self-hosted-runners", "--format", "json"]):
        return None
    # The native verifier does not request JSON, but all its identity flags
    # are otherwise identical. Normalize only for validation, never execution.
    if len(argv) == 15 and argv[-1] == "--deny-self-hosted-runners":
        return validate(argv + ["--format", "json"])
    raise ValueError("not an approved release read")


def retryable(stderr: bytes) -> bool:
    text = stderr.decode("utf-8", errors="replace")
    # A network-looking suffix cannot override a real authentication, content
    # or signature rejection, including a verifier initialization failure.
    if re.search(r"(?i)HTTP\s*[45][0-9]{2}|x509|certificate|invalid|expired|"
                 r"signature|verification failed|mismatch|not available|unauthorized|forbidden", text):
        return False
    # Match the complete native diagnostic, not a suffix that could conceal
    # an unrecognized content/verifier failure on an earlier line.
    return bool(re.fullmatch(
        r'\s*(?:Error: )?(?:Get|Head) "https://[^"\r\n]+": '
        r'(?:EOF|unexpected EOF|(?:net/http: )?TLS handshake timeout|'
        r'(?:read|write|dial) tcp [^\r\n]+: (?:read: |write: |connect: )?'
        r'(?:connection reset by peer|connection refused|i/o timeout|network is unreachable))\s*',
        text))


def stop(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    # The group can still contain descendants after its leader exits.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def execute(argv: list[str], timeout: float) -> tuple[int, bytes, bytes]:
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out,
                                   stderr=err, start_new_session=True)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    stop(process)
                    return 124, b"", b"release read deadline exceeded; not retrying\n"
                if max(os.fstat(out.fileno()).st_size, os.fstat(err.fileno()).st_size) > OUTPUT_LIMIT:
                    stop(process)
                    return 125, b"", b"release read output limit exceeded; not retrying\n"
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        finally:
            stop(process)
        out.seek(0)
        err.seek(0)
        stdout, stderr = out.read(OUTPUT_LIMIT + 1), err.read(OUTPUT_LIMIT + 1)
        if max(len(stdout), len(stderr)) > OUTPUT_LIMIT:
            return 125, b"", b"release read output limit exceeded; not retrying\n"
        return process.returncode, stdout, stderr


def run(argv: list[str]) -> int:
    target = validate(argv)
    original = target.lstat() if target is not None else None
    deadline = time.monotonic() + TOTAL_SECONDS
    for attempt in range(1, ATTEMPTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 124
        # Failed downloads never leave a partial proof in the next attempt.
        with tempfile.TemporaryDirectory(dir=target.parent if target else None,
                                         prefix="gh-release-read-") as temporary:
            command = list(argv)
            if target is not None:
                command[-1] = temporary
            code, stdout, stderr = execute(command, min(ATTEMPT_SECONDS, remaining))
            sys.stderr.buffer.write(stderr)
            sys.stderr.buffer.flush()
            if code == 0:
                if target is not None:
                    current = target.lstat()
                    if ((current.st_dev, current.st_ino) != (original.st_dev, original.st_ino)
                            or not stat.S_ISDIR(current.st_mode) or any(target.iterdir())):
                        raise ValueError("proof destination changed during download")
                    os.replace(temporary, target)
                sys.stdout.buffer.write(stdout)
                return 0
        # Deadline termination and arbitrary nonzero status are not evidence
        # of a retryable transport error. Keep them immediately red.
        if code != 1 or not retryable(stderr) or attempt == ATTEMPTS:
            return code if code > 0 else 1
        pause = attempt
        if time.monotonic() + pause >= deadline:
            return 124
        print(f"release read transport failure; retry {attempt}/{ATTEMPTS}", file=sys.stderr)
        time.sleep(pause)
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(run(sys.argv[1:]))
    except (OSError, ValueError) as exc:
        print(f"release read refused: {exc}", file=sys.stderr)
        raise SystemExit(2)
