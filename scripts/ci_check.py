#!/usr/bin/env python3
"""Dispatch Policy CI and the dial gate for one YueLink commit.

Prints one failing id per line. Empty output and exit 0 means both runs
finished without a failing job. A missing run is a failure. This does not
dispatch the release build.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CHECKS = (
    ("policy-ci", "policy-ci.yml", ("yuelink_sha",)),
    ("dial", "dial-gate.yml", ("source_sha", "run_pr_checks")),
)
PASSING = {"success", "skipped"}


def _gh(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def dispatch(repo: str, ref: str, sha: str) -> int:
    commands = (
        ["workflow", "run", "policy-ci.yml", "--repo", repo, "--ref", ref, "-f", f"yuelink_sha={sha}"],
        [
            "workflow",
            "run",
            "dial-gate.yml",
            "--repo",
            repo,
            "--ref",
            ref,
            "-f",
            f"source_sha={sha}",
            "-f",
            "run_pr_checks=true",
        ],
    )
    for command in commands:
        if "build.yml" in command:
            return _fail("refusing to dispatch the release build")
        proc = _gh(command)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            print(detail[0] if detail else "workflow dispatch failed", file=sys.stderr)
            return proc.returncode or 1
    return 0


def _runs(repo: str, workflow: str) -> list[dict]:
    proc = _gh(
        [
            "run",
            "list",
            "--repo",
            repo,
            "--workflow",
            workflow,
            "--event",
            "workflow_dispatch",
            "--limit",
            "20",
            "--json",
            "databaseId,displayTitle,status,conclusion,createdAt",
        ]
    )
    if proc.returncode != 0:
        raise RuntimeError("run list failed")
    payload = json.loads(proc.stdout or "[]")
    if not isinstance(payload, list):
        raise RuntimeError("run list failed")
    return [item for item in payload if isinstance(item, dict)]


def _jobs(repo: str, run_id: int) -> list[dict]:
    proc = _gh(
        [
            "run",
            "view",
            str(run_id),
            "--repo",
            repo,
            "--json",
            "jobs",
        ]
    )
    if proc.returncode != 0:
        raise RuntimeError("run view failed")
    payload = json.loads(proc.stdout or "{}")
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        raise RuntimeError("run view failed")
    return [item for item in jobs if isinstance(item, dict)]


def _match(runs: list[dict], sha: str) -> dict | None:
    titled = [run for run in runs if sha in str(run.get("displayTitle") or "")]
    chosen = titled or runs
    if not chosen:
        return None
    return max(chosen, key=lambda run: str(run.get("createdAt") or ""))


def wait_for(repo: str, label: str, workflow: str, sha: str, attempts: int, interval: float) -> list[str]:
    print(f"waiting {label}", file=sys.stderr)
    last: dict | None = None
    for _ in range(attempts):
        try:
            last = _match(_runs(repo, workflow), sha)
        except (RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired):
            last = None
        if last and last.get("status") == "completed":
            break
        if interval:
            time.sleep(interval)
    if not last:
        return [f"{label}/missing"]
    if last.get("status") != "completed":
        return [f"{label}/incomplete"]
    try:
        jobs = _jobs(repo, int(last["databaseId"]))
    except (RuntimeError, KeyError, TypeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return [f"{label}/unreadable"]
    failing: list[str] = []
    for job in jobs:
        conclusion = str(job.get("conclusion") or "")
        if conclusion in PASSING:
            continue
        name = str(job.get("name") or "unknown").replace(" ", "-")
        failing.append(f"{label}/{name}")
    if not jobs:
        failing.append(f"{label}/no-jobs")
    return failing


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or SHA_RE.fullmatch(args[0]) is None:
        print("usage: ci-check.sh <40-hex-sha>", file=sys.stderr)
        return 2
    sha = args[0]
    repo = os.environ.get("CI_CHECK_REPO", "onesyue/yuelink-ci")
    ref = os.environ.get("CI_CHECK_REF", "master")
    try:
        attempts = int(os.environ.get("CI_CHECK_ATTEMPTS", "30"))
        interval = float(os.environ.get("CI_CHECK_INTERVAL", "20"))
    except ValueError:
        print("usage: ci-check.sh <40-hex-sha>", file=sys.stderr)
        return 2
    if attempts < 1:
        print("usage: ci-check.sh <40-hex-sha>", file=sys.stderr)
        return 2
    dispatched = dispatch(repo, ref, sha)
    if dispatched:
        return dispatched
    failing: list[str] = []
    for label, workflow, _fields in CHECKS:
        failing.extend(wait_for(repo, label, workflow, sha, attempts, interval))
    for item in failing:
        print(item)
    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main())
