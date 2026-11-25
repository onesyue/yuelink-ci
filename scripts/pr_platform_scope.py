#!/usr/bin/env python3
"""Decide which dial platforms a pull request still has to run.

Release and dispatch runs always run every platform. A pull request reruns a
platform only when that platform's dial inputs changed relative to the base
commit: its matrix row, the steps that apply to it, the shared dial header,
the baseline job, and the script bytes those steps name. Unchanged platforms
are omitted so the pull request does not repeat a dial whose inputs match the
base. A comparison that cannot be proved reruns every platform.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

DIAL_PLATFORMS = ("windows", "macos", "linux", "android")
CALLER = ".github/workflows/build.yml"
SCOPE_SCRIPT = "scripts/pr_platform_scope.py"
Reader = Callable[[str], bytes | None]


class ScopeError(RuntimeError):
    pass


def _job(workflow: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [A-Za-z0-9_]+:\n|\Z)",
        workflow,
    )
    if match is None:
        raise ScopeError(f"missing job {name}")
    return match.group(1)


def _matrix_rows(job: str) -> dict[str, str]:
    match = re.search(r"(?ms)^        include:\n(.*?)(?=^    steps:\n)", job)
    if match is None:
        raise ScopeError("dial matrix include is missing")
    rows: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in match.group(1).splitlines(keepends=True):
        row = re.match(r"          - name: (\w+)\n", line)
        if row:
            if current is not None:
                rows[current] = "".join(buf)
            current = row.group(1)
            buf = [line]
        elif current is not None:
            buf.append(line)
    if current is not None:
        rows[current] = "".join(buf)
    if set(rows) != set(DIAL_PLATFORMS):
        raise ScopeError(f"dial platforms are {sorted(rows)}")
    return rows


def _steps(job: str) -> list[str]:
    marker = "\n    steps:\n"
    index = job.find(marker)
    if index < 0:
        raise ScopeError("job has no steps")
    parts = re.split(r"(?m)^      - ", job[index + len(marker):])[1:]
    if not parts:
        raise ScopeError("job steps are empty")
    return ["      - " + part for part in parts]


def _condition(step: str) -> str | None:
    match = re.search(r"(?m)^        if:\s*(.+)$", step)
    if match is None:
        return None
    return match.group(1).strip()


def _step_applies(step: str, platform: str) -> bool:
    condition = _condition(step)
    if condition is None:
        return True
    mentioned = {name for name in DIAL_PLATFORMS if re.search(rf"\b{name}\b", condition)}
    if not mentioned:
        return True
    return platform in mentioned


def _paths(blob: str) -> tuple[str, ...]:
    found = re.findall(
        r"(?:scripts|\.github/workflows)/[A-Za-z0-9_./-]+\.(?:py|sh|yml)",
        blob,
    )
    return tuple(sorted(set(found) | {CALLER, SCOPE_SCRIPT}))


def _digest(text: str, reader: Reader) -> str:
    chunk = [text.encode()]
    for path in _paths(text):
        data = reader(path)
        if data is None:
            raise ScopeError(f"missing {path}")
        chunk.append(path.encode() + b"\0" + data)
    return hashlib.sha256(b"\n".join(chunk)).hexdigest()


def unit_digests(workflow: str, reader: Reader) -> dict[str, str]:
    dial = _job(workflow, "dial_gate")
    rows = _matrix_rows(dial)
    include_at = dial.find("\n        include:\n")
    if include_at < 0:
        raise ScopeError("dial matrix include is missing")
    shared = dial[:include_at] + "\n" + _job(workflow, "baseline_binding")
    shared += "\n" + _job(workflow, "platform_scope")
    steps = _steps(dial)
    digests: dict[str, str] = {}
    for platform in DIAL_PLATFORMS:
        applied = "".join(step for step in steps if _step_applies(step, platform))
        digests[platform] = _digest(shared + "\n" + rows[platform] + applied, reader)
    digests["ios"] = _digest(_job(workflow, "ios_unsigned_compile") + shared, reader)
    digests["auth"] = _digest(_job(workflow, "linux_auth_storage") + shared, reader)
    return digests


def _all(digests: Mapping[str, str]) -> dict[str, object]:
    return {
        "names": list(DIAL_PLATFORMS),
        "skipped": [],
        "ios": True,
        "auth": True,
        "digests": dict(digests),
    }


def decide(
    event: str,
    head_workflow: str,
    head_reader: Reader,
    base_workflow: str | None = None,
    base_reader: Reader | None = None,
) -> dict[str, object]:
    """Return the platforms to run. Unproved comparisons run every platform."""
    head = unit_digests(head_workflow, head_reader)
    if event != "pull_request" or base_workflow is None or base_reader is None:
        return _all(head)
    try:
        base = unit_digests(base_workflow, base_reader)
    except ScopeError:
        return _all(head)
    names = [platform for platform in DIAL_PLATFORMS if head[platform] != base[platform]]
    return {
        "names": names,
        "skipped": [platform for platform in DIAL_PLATFORMS if platform not in names],
        "ios": head["ios"] != base["ios"],
        "auth": head["auth"] != base["auth"],
        "digests": head,
    }


def _git_show(rev: str, path: str) -> bytes | None:
    if rev in ("", "HEAD"):
        file = Path(path)
        return file.read_bytes() if file.is_file() else None
    proc = subprocess.run(
        ["git", "show", f"{rev}:{path}"],
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def emit(scope: Mapping[str, object], destination: Path | None) -> None:

    names = scope["names"]
    payload = {
        "names": names,
        "skipped": scope["skipped"],
        "ios": bool(scope["ios"]),
        "auth": bool(scope["auth"]),
    }
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(payload, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    lines = [
        "names=" + json.dumps(names, separators=(",", ":")),
        "ios=" + ("true" if payload["ios"] else "false"),
        "auth=" + ("true" if payload["auth"] else "false"),
    ]
    text = "\n".join(lines) + "\n"
    github = os.environ.get("GITHUB_OUTPUT")
    if github:
        with open(github, "a", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args(argv)
    workflow = args.workflow.read_text(encoding="utf-8")
    head_reader: Reader = lambda path: _git_show("HEAD", path)
    base_workflow = None
    base_reader = None
    if args.event == "pull_request":
        if not re.fullmatch(r"[0-9a-f]{40}", args.base):
            print("::error::pull request base is not 40 lowercase hex", file=sys.stderr)
            return 1
        shown = _git_show(args.base, args.workflow.as_posix())
        base_workflow = None if shown is None else shown.decode()
        base_reader = lambda path, rev=args.base: _git_show(rev, path)
    try:
        scope = decide(args.event, workflow, head_reader, base_workflow, base_reader)
    except ScopeError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    emit(scope, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
