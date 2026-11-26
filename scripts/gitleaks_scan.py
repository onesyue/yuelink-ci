#!/usr/bin/env python3
"""Scan this repository with the pinned Gitleaks binary.

The current tree is always scanned. A pull request also scans commits
unique to that request. A push scans commits unique to that push when
GitHub names a real parent. This is not a full-history scan.

Set GITLEAKS_BIN to a version-matched binary to skip the download.
Set GITLEAKS_ROOT to scan a checkout other than the working directory.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import runpy

_LIMITS = runpy.run_path(str(Path(__file__).resolve().with_name("constants.py")))
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SCAN_PREFIX = [
    "--config",
    ".gitleaks.toml",
    "--redact",
    "--verbose",
    "--no-banner",
    "--no-color",
    "--exit-code",
    "1",
]


class GateError(Exception):
    """The scan could not be run."""


def _die(message: str) -> None:
    raise GateError(message)


def _run(argv: list[str], cwd: Path | None, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _git(root: Path, *args: str) -> str:
    proc = _run(
        ["git", "-C", str(root), *args],
        cwd=None,
        timeout=_LIMITS["GH_COMMAND_TIMEOUT_SECONDS"],
    )
    if proc.returncode != 0:
        _die("git command failed")
    return proc.stdout.strip()


def _require_commit(root: Path, sha: str) -> None:
    proc = _run(
        ["git", "-C", str(root), "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=None,
        timeout=_LIMITS["GH_COMMAND_TIMEOUT_SECONDS"],
    )
    if proc.returncode != 0:
        _die("git range parent is not in this checkout")


def _event_payload() -> dict:
    raw = os.environ.get("GITHUB_EVENT_PATH", "")
    if not raw:
        return {}
    path = Path(raw)
    if not path.is_file():
        _die("GITHUB_EVENT_PATH is not a file")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        _die("GITHUB_EVENT_PATH is not an object")
    return loaded


def _ranges(root: Path) -> list[str]:
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event not in {"pull_request", "push"}:
        return []
    head = _git(root, "rev-parse", "HEAD")
    if not _SHA.fullmatch(head):
        _die("HEAD is not a commit")
    payload = _event_payload()
    if event == "pull_request":
        pull = payload.get("pull_request")
        base = ""
        if isinstance(pull, dict):
            base_obj = pull.get("base")
            if isinstance(base_obj, dict):
                base = str(base_obj.get("sha", ""))
        if not _SHA.fullmatch(base):
            _die("pull request base sha is missing")
        _require_commit(root, base)
        return [f"{base}..{head}"]
    before = str(payload.get("before", ""))
    if before in {"", _LIMITS["GITLEAKS_ABSENT_PUSH_BEFORE"]}:
        return []
    if not _SHA.fullmatch(before):
        _die("push before sha is not 40 hex")
    _require_commit(root, before)
    return [f"{before}..{head}"]


def _version_ok(prefix: list[str], cwd: Path | None) -> None:
    proc = _run(prefix + ["version"], cwd, _LIMITS["GH_COMMAND_TIMEOUT_SECONDS"])
    if proc.returncode != 0 or proc.stdout.strip() != _LIMITS["GITLEAKS_VERSION"]:
        _die("gitleaks version does not match the pin")


def _download(url: str, dest: Path) -> None:
    connect = str(_LIMITS["CURL_CONNECT_TIMEOUT_SECONDS"])
    maximum = str(_LIMITS["GITLEAKS_DOWNLOAD_MAX_SECONDS"])
    attempts = str(_LIMITS["GITLEAKS_DOWNLOAD_ATTEMPTS"])
    budget = (
        _LIMITS["GITLEAKS_DOWNLOAD_MAX_SECONDS"] * _LIMITS["GITLEAKS_DOWNLOAD_ATTEMPTS"]
        + _LIMITS["CURL_CONNECT_TIMEOUT_SECONDS"]
    )
    proc = _run(
        [
            "curl",
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--tlsv1.2",
            "--connect-timeout",
            connect,
            "--max-time",
            maximum,
            "--retry",
            attempts,
            "--retry-all-errors",
            "--output",
            str(dest),
            url,
        ],
        cwd=None,
        timeout=budget,
    )
    if proc.returncode != 0:
        _die("gitleaks download failed")


def _install(work: Path) -> Path:
    version = _LIMITS["GITLEAKS_VERSION"]
    archive_name = f"gitleaks_{version}_linux_x64.tar.gz"
    url = f"{_LIMITS['GITLEAKS_RELEASE_BASE']}/v{version}/{archive_name}"
    archive = work / archive_name
    _download(url, archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if not hmac.compare_digest(digest, _LIMITS["GITLEAKS_LINUX_X64_SHA256"]):
        _die("gitleaks archive digest mismatch")
    binary = work / "gitleaks"
    with tarfile.open(archive, "r:gz") as bundle:
        chosen = None
        for member in bundle.getmembers():
            if member.name == "gitleaks" and member.isfile():
                chosen = member
                break
        if chosen is None:
            _die("gitleaks archive does not contain the gitleaks binary")
        payload = bundle.extractfile(chosen)
        if payload is None:
            _die("gitleaks archive member is empty")
        binary.write_bytes(payload.read())
    binary.chmod(0o755)
    return binary


def _linux_amd64() -> bool:
    return platform.system() == "Linux" and platform.machine() in {"x86_64", "amd64"}


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    proc = _run(
        ["docker", "image", "inspect", _LIMITS["GITLEAKS_DOCKER_IMAGE"]],
        cwd=None,
        timeout=_LIMITS["GH_COMMAND_TIMEOUT_SECONDS"],
    )
    return proc.returncode == 0


def _docker_prefix(binary: Path, root: Path) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "-v",
        f"{binary}:{_LIMITS['GITLEAKS_CONTAINER_BIN']}:ro",
        "-v",
        f"{root}:/repo",
        "-w",
        "/repo",
        _LIMITS["GITLEAKS_DOCKER_IMAGE"],
        "gitleaks",
    ]


def _command(root: Path, work: Path) -> list[str]:
    override = os.environ.get("GITLEAKS_BIN", "")
    if override:
        prefix = [override]
        _version_ok(prefix, root)
        return prefix
    binary = _install(work)
    if _linux_amd64():
        prefix = [str(binary)]
        _version_ok(prefix, root)
        return prefix
    if _docker_ready():
        prefix = _docker_prefix(binary, root)
        _version_ok(prefix, None)
        return prefix
    host = shutil.which("gitleaks")
    if host:
        prefix = [host]
        _version_ok(prefix, root)
        return prefix
    _die("pinned gitleaks archive verified but this host cannot run it")


def _scan_once(prefix: list[str], root: Path, args: list[str]) -> int:
    proc = subprocess.run(
        [*prefix, *args],
        cwd=root,
        check=False,
        timeout=_LIMITS["GITLEAKS_SCAN_TIMEOUT_SECONDS"],
    )
    return proc.returncode


def scan(prefix: list[str], root: Path) -> int:
    if not (root / ".gitleaks.toml").is_file():
        _die("missing .gitleaks.toml")
    code = _scan_once(prefix, root, ["dir", *_SCAN_PREFIX, "."])
    if code != 0:
        return code
    for spec in _ranges(root):
        if "--all" in spec.split():
            _die("refusing a full-history scan")
        code = _scan_once(prefix, root, ["git", *_SCAN_PREFIX, "--log-opts", spec, "."])
        if code != 0:
            return code
    return 0


def main() -> int:
    root = Path(os.environ.get("GITLEAKS_ROOT", ".")).resolve()
    try:
        with tempfile.TemporaryDirectory(prefix="gitleaks-pin-") as tmp:
            prefix = _command(root, Path(tmp))
            return scan(prefix, root)
    except GateError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
