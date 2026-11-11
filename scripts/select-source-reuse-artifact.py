#!/usr/bin/env python3
"""Select source-reuse receipts from this attestation run.

A failed-job rerun increments ``github.run_attempt`` and does not upload
new receipts for jobs that stayed green. Those earlier artifacts are still
evidence for the same run. Keep the highest attempt that is not newer than
the signing attempt. Ignore other runs, expired artifacts, non-canonical
attempt numbers, and any attempt above the signing attempt.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPOSITORY = "onesyue/yuelink-ci"
KINDS = ("flutter", "service")
RUN_ID_RE = re.compile(r"[1-9][0-9]{0,19}")
ATTEMPT_RE = re.compile(r"[1-9][0-9]{0,5}")
PAGE_LIMIT = 20


class SelectionError(ValueError):
    """The artifact list cannot authorize a receipt."""


def _canonical(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise SelectionError(f"invalid {label}")
    return value


def flatten_pages(pages: object) -> list[dict[str, object]]:
    """Return every artifact from a complete GitHub list response."""
    if not isinstance(pages, list) or not pages:
        raise SelectionError("artifact list is empty or not a page array")
    artifacts: list[dict[str, object]] = []
    total: int | None = None
    for page in pages:
        if not isinstance(page, dict):
            raise SelectionError("artifact page is not an object")
        page_total = page.get("total_count")
        page_artifacts = page.get("artifacts")
        if not isinstance(page_total, int) or page_total < 0:
            raise SelectionError("artifact page is missing total_count")
        if not isinstance(page_artifacts, list):
            raise SelectionError("artifact page is missing artifacts")
        if total is None:
            total = page_total
        elif page_total != total:
            raise SelectionError("artifact page total changed while listing")
        for item in page_artifacts:
            if not isinstance(item, dict):
                raise SelectionError("artifact entry is not an object")
            artifacts.append(item)
        if len(page_artifacts) == 0 and len(artifacts) != total:
            raise SelectionError("artifact page ended before total_count")
    if total != len(artifacts):
        raise SelectionError(
            f"incomplete artifact list: got {len(artifacts)} of {total}"
        )
    return artifacts


def select_artifact(
    artifacts: list[dict[str, object]],
    *,
    kind: str,
    run_id: str,
    current_attempt: str,
) -> tuple[str, str]:
    """Return ``(name, attempt)`` for one receipt kind."""
    if kind not in KINDS:
        raise SelectionError("unknown receipt kind")
    run_id = _canonical(run_id, RUN_ID_RE, "run id")
    current_attempt = _canonical(current_attempt, ATTEMPT_RE, "signing attempt")
    current = int(current_attempt)
    pattern = re.compile(
        rf"^source-reuse-{kind}-{re.escape(run_id)}-([1-9][0-9]{{0,5}})$"
    )
    chosen: tuple[int, str] | None = None
    seen: set[str] = set()
    for item in artifacts:
        name = item.get("name")
        match = pattern.fullmatch(name) if isinstance(name, str) else None
        if match is None or item.get("expired") is not False:
            continue
        attempt = int(match.group(1))
        if attempt > current:
            continue
        if name in seen:
            raise SelectionError(f"ambiguous {kind} receipt name")
        seen.add(name)
        if chosen is None or attempt > chosen[0]:
            chosen = (attempt, name)
    if chosen is None:
        raise SelectionError(
            f"no {kind} receipt at or before attempt {current_attempt} "
            f"for run {run_id}"
        )
    return chosen[1], str(chosen[0])


def fetch_pages(run_id: str) -> list[dict[str, object]]:
    """Read this repository's artifact list. The run id is already canonical."""
    pages: list[dict[str, object]] = []
    for page_number in range(1, PAGE_LIMIT + 1):
        endpoint = (
            f"repos/{REPOSITORY}/actions/runs/{run_id}/artifacts"
            f"?per_page=100&page={page_number}"
        )
        completed = subprocess.run(
            ["gh", "api", endpoint],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if completed.returncode != 0:
            raise SelectionError("artifact list request failed")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise SelectionError("artifact list response is not JSON") from exc
        if not isinstance(payload, dict):
            raise SelectionError("artifact list response is not an object")
        pages.append(payload)
        artifacts = payload.get("artifacts")
        total = payload.get("total_count")
        if not isinstance(artifacts, list) or not isinstance(total, int):
            raise SelectionError("artifact list response is incomplete")
        if len(artifacts) == 0 or page_number * 100 >= total:
            return pages
    raise SelectionError("artifact list has too many pages")


def write_output(path: Path, selected: dict[str, tuple[str, str]]) -> None:
    lines = []
    for kind, (name, attempt) in selected.items():
        if "\n" in name or "\n" in attempt:
            raise SelectionError("receipt name contains a newline")
        lines.append(f"{kind}_name={name}")
        lines.append(f"{kind}_attempt={attempt}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--current-attempt", required=True)
    parser.add_argument("--pages", type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        run_id = _canonical(args.run_id, RUN_ID_RE, "run id")
        current = _canonical(args.current_attempt, ATTEMPT_RE, "signing attempt")
        if args.pages is None:
            pages = fetch_pages(run_id)
        else:
            pages = json.loads(args.pages.read_text(encoding="utf-8"))
        artifacts = flatten_pages(pages)
        selected = {
            kind: select_artifact(
                artifacts, kind=kind, run_id=run_id, current_attempt=current
            )
            for kind in KINDS
        }
    except (SelectionError, OSError, json.JSONDecodeError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    for kind, (name, attempt) in selected.items():
        print(f"selected {kind} attempt {attempt} name {name}")
    if args.github_output is not None:
        try:
            write_output(args.github_output, selected)
        except (SelectionError, OSError) as exc:
            print(f"::error::{exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
