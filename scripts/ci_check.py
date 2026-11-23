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
RUN_URL_RE = re.compile(r"https://[^/]+/([^/]+/[^/]+)/actions/runs/([1-9][0-9]*)")
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


def dispatch(repo: str, ref: str, sha: str) -> tuple[int, dict[str, str | None]]:
    checks = (
        ("policy-ci", "policy-ci.yml", ["-f", f"yuelink_sha={sha}"]),
        ("dial", "dial-gate.yml", ["-f", f"source_sha={sha}", "-f", "run_pr_checks=true"]),
    )
    run_ids: dict[str, str | None] = {}
    expected_repo = "/".join(repo.split("/")[-2:])
    for label, workflow, fields in checks:
        if workflow == "build.yml":
            return _fail("refusing to dispatch the release build"), {}
        proc = _gh(["workflow", "run", workflow, "--repo", repo, "--ref", ref, *fields])
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            print(detail[0] if detail else "workflow dispatch failed", file=sys.stderr)
            return proc.returncode, {}
        # Modern gh prints the new run URL on captured stdout when GitHub
        # returns run details. A missing identity must fail closed.
        match = RUN_URL_RE.fullmatch(proc.stdout.strip())
        run_ids[label] = match[2] if match and match[1].lower() == expected_repo.lower() else None
    return 0, run_ids


def _run(repo: str, run_id: str) -> dict:
    proc = _gh(["run", "view", run_id, "--repo", repo, "--json", "status,jobs"])
    if proc.returncode != 0:
        raise RuntimeError("run view failed")
    payload = json.loads(proc.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("run view failed")
    return payload


def wait_for(repo: str, label: str, run_id: str | None, attempts: int, interval: float) -> list[str]:
    print(f"waiting {label}", file=sys.stderr)
    if run_id is None:
        return [f"{label}/missing"]
    run: dict | None = None
    for attempt in range(attempts):
        try:
            run = _run(repo, run_id)
        except (RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired):
            run = None
        if run and run.get("status") == "completed":
            break
        if interval and attempt + 1 < attempts:
            time.sleep(interval)
    if run is None:
        return [f"{label}/unreadable"]
    if run.get("status") != "completed":
        return [f"{label}/incomplete"]
    jobs = run.get("jobs")
    if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
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
    dispatched, run_ids = dispatch(repo, ref, sha)
    if dispatched:
        return dispatched
    failing: list[str] = []
    for label, run_id in run_ids.items():
        failing.extend(wait_for(repo, label, run_id, attempts, interval))
    for item in failing:
        print(item)
    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main())
