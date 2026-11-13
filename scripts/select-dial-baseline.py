#!/usr/bin/env python3
"""Freeze the latest GitHub stable release with exact public promotion evidence.

No production endpoint is read. A receipt proves a public pipeline promotion,
not the live version after a possible manual rollback. Missing proof never
causes a fallback to an older release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPOSITORY = "onesyue/yuelink-ci"
SCRIPTS = Path(__file__).resolve().parent
STABLE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
PROMOTION_STEP = "Sign root manifest and promote (in-pipeline)"


class EvidenceError(ValueError):
    """A closed diagnostic without remote payload content."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def timestamp(value: Any) -> datetime:
    require(isinstance(value, str) and value.endswith("Z"), "invalid GitHub timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise EvidenceError("invalid GitHub timestamp") from exc


def positive_id(value: Any) -> int:
    require(type(value) is int and value > 0, "invalid GitHub identifier")
    return value


class GitHub:
    def get(self, endpoint: str) -> Any:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "gh-read-retry.py"),
                "gh",
                "api",
                f"repos/{REPOSITORY}/{endpoint}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    def tag(self, tag: str) -> dict:
        result = subprocess.run(
            ["bash", str(SCRIPTS / "resolve-release-tag.sh"), tag],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    def attest(self, binding: dict, release_id: int) -> None:
        # Reuse every identity, artifact-closure and Sigstore provenance check.
        subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "preserve-source-proof.py"),
                binding["tag"],
                binding["source"],
                binding["builder"],
                binding["attestation_run"],
                "--release-id",
                str(release_id),
            ],
            stdout=subprocess.DEVNULL,
            check=True,
        )


def pages(api: GitHub, endpoint: str, key: str | None = None) -> list[dict]:
    values: list[dict] = []
    seen: set[int] = set()
    separator = "&" if "?" in endpoint else "?"
    for page in range(1, 101):
        data = api.get(f"{endpoint}{separator}per_page=100&page={page}")
        entries = data if key is None else data.get(key)
        require(isinstance(entries, list), "missing GitHub result page")
        for entry in entries:
            require(isinstance(entry, dict), "invalid GitHub result entry")
            identity = positive_id(entry.get("id"))
            require(
                identity not in seen, "duplicate entry while paginating GitHub evidence"
            )
            seen.add(identity)
            values.append(entry)
        if len(entries) < 100:
            if key is not None:
                require(
                    data.get("total_count") == len(values),
                    "incomplete GitHub pagination",
                )
            return values
    raise EvidenceError("GitHub evidence pagination exhausted")


def run_identity(run: dict) -> tuple:
    # Ignore counters/URLs that can change without changing executed evidence.
    return tuple(
        run.get(key)
        for key in (
            "id",
            "run_attempt",
            "workflow_id",
            "name",
            "path",
            "event",
            "head_branch",
            "head_sha",
            "status",
            "conclusion",
            "created_at",
            "updated_at",
        )
    )


def exact_run(run: dict, workflow_id: int, binding: dict, cutoff: datetime) -> None:
    require(
        run.get("workflow_id") == workflow_id
        and run.get("name") == "Build YueLink"
        and run.get("path") == ".github/workflows/build.yml"
        and run.get("event") == "push"
        and run.get("head_branch") == binding["tag"]
        and run.get("head_sha") == binding["builder"]
        and run.get("repository", {}).get("full_name") == REPOSITORY,
        "Build run does not match the release tag and builder",
    )
    require(
        run.get("status") == "completed" and run.get("conclusion") == "success",
        "latest release Build run is not successful",
    )
    require(
        timestamp(run.get("created_at")) <= cutoff
        and timestamp(run.get("updated_at")) <= cutoff,
        "Build evidence is newer than the selection cutoff",
    )
    positive_id(run.get("id"))
    positive_id(run.get("run_attempt"))


def select(api: GitHub, cutoff: datetime) -> dict:
    releases = pages(api, "releases")
    eligible = []
    for release in releases:
        require(
            type(release.get("draft")) is bool
            and type(release.get("prerelease")) is bool,
            "invalid release eligibility metadata",
        )
        if release["draft"] or release["prerelease"]:
            continue
        if not isinstance(release.get("tag_name"), str) or not STABLE.fullmatch(
            release["tag_name"]
        ):
            continue
        if timestamp(release.get("published_at")) <= cutoff:
            eligible.append(release)
    require(bool(eligible), "no published stable release at the selection cutoff")
    release = max(
        eligible, key=lambda item: (timestamp(item["published_at"]), item["id"])
    )
    tag = release["tag_name"]
    binding = api.tag(tag)
    require(binding.get("tag") == tag, "tag resolver returned a different release")
    for field in ("source", "builder", "tag_object"):
        require(
            isinstance(binding.get(field), str) and bool(SHA.fullmatch(binding[field])),
            "invalid resolved tag binding",
        )
    require(
        isinstance(binding.get("attestation_run"), str)
        and bool(re.fullmatch(r"[1-9][0-9]*", binding["attestation_run"])),
        "invalid attestation run binding",
    )
    attestation = api.get(f"actions/runs/{binding['attestation_run']}")
    attestation_attempt = positive_id(attestation.get("run_attempt"))
    require(
        timestamp(attestation.get("updated_at")) <= cutoff,
        "attestation is newer than the selection cutoff",
    )
    api.attest(binding, release["id"])
    # The existing verifier authenticates the current attempt. Reject concurrent reruns.
    require(
        run_identity(api.get(f"actions/runs/{binding['attestation_run']}"))
        == run_identity(attestation),
        "attestation changed during verification",
    )
    workflow = api.get("actions/workflows/build.yml")
    require(
        workflow.get("name") == "Build YueLink"
        and workflow.get("path") == ".github/workflows/build.yml"
        and workflow.get("state") == "active",
        "invalid Build workflow identity",
    )
    workflow_id = positive_id(workflow.get("id"))
    runs = pages(
        api,
        f"actions/workflows/build.yml/runs?event=push&branch={tag}",
        "workflow_runs",
    )
    # Never select an older green attempt/run after a newer failed or partial run.
    runs = [run for run in runs if timestamp(run.get("created_at")) <= cutoff]
    require(bool(runs), "latest stable release has no Build run")
    run = max(runs, key=lambda item: (timestamp(item["created_at"]), item["id"]))
    exact_run(run, workflow_id, binding, cutoff)
    run_id, attempt = run["id"], run["run_attempt"]
    attempt_run = api.get(f"actions/runs/{run_id}/attempts/{attempt}")
    exact_run(attempt_run, workflow_id, binding, cutoff)
    require(
        attempt_run["id"] == run_id and attempt_run["run_attempt"] == attempt,
        "Build attempt identity mismatch",
    )
    jobs = pages(api, f"actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs")
    matches = []
    for job in jobs:
        require(
            job.get("run_id") == run_id
            and job.get("run_attempt") == attempt
            and job.get("head_sha") == binding["builder"],
            "job identity differs from exact Build attempt",
        )
        require(isinstance(job.get("steps"), list), "missing Build job steps")
        for step in job["steps"]:
            if step.get("name") == PROMOTION_STEP:
                matches.append((job, step))
    require(len(matches) == 1, "promotion step is missing or ambiguous")
    job, step = matches[0]
    require(
        job.get("status") == "completed"
        and job.get("conclusion") == "success"
        and step.get("status") == "completed"
        and step.get("conclusion") == "success",
        "promotion did not explicitly succeed",
    )
    require(
        timestamp(job.get("completed_at")) <= cutoff
        and timestamp(step.get("completed_at")) <= cutoff,
        "promotion is newer than selection cutoff",
    )
    step_number = positive_id(step.get("number"))
    require(
        run_identity(api.get(f"actions/runs/{run_id}")) == run_identity(run),
        "Build run changed during verification",
    )
    reread = api.get(f"releases/{release['id']}")
    require(
        all(
            reread.get(key) == release.get(key)
            for key in ("id", "tag_name", "published_at", "draft", "prerelease")
        ),
        "release changed during verification",
    )
    require(api.tag(tag) == binding, "release tag moved during verification")
    return {
        "schema_version": 1,
        "scope": "latest-public-pipeline-promoted-stable-release",
        "repository": REPOSITORY,
        "selection_cutoff": cutoff.isoformat().replace("+00:00", "Z"),
        "release_id": release["id"],
        "published_at": release["published_at"],
        **binding,
        "attestation_attempt": attestation_attempt,
        "build_run": run_id,
        "build_attempt": attempt,
        "promotion_job": job["id"],
        "promotion_step": step_number,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    # Freeze time before any GitHub reads; callers cannot supply a historical cutoff.
    cutoff = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        receipt = select(GitHub(), cutoff)
        encoded = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        with args.receipt.open("xb") as output:
            output.write(encoded)
        digest = hashlib.sha256(encoded).hexdigest()
        if output_path := os.environ.get("GITHUB_OUTPUT"):
            with open(output_path, "a", encoding="utf-8") as output:
                output.write(
                    f"baseline_source={receipt['source']}\nbaseline_receipt_sha256={digest}\n"
                )
        print(
            f"Verified dial baseline: {receipt['tag']} source={receipt['source']} receipt_sha256={digest}"
        )
        return 0
    except EvidenceError as exc:
        print(f"::error::{exc}; no older baseline selected", file=sys.stderr)
        return 1
    except (ValueError, TypeError, KeyError, OSError, subprocess.CalledProcessError):
        print(
            "::error::latest stable release promotion evidence is incomplete or invalid; no older baseline selected",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
