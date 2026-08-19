#!/usr/bin/env python3
"""Count Flutter machine-protocol testStart events and enforce a floor."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path


class ProtocolError(ValueError):
    pass


def count_test_starts(lines: Iterable[str]) -> int:
    count = 0
    started = False
    done = False
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError as error:
            # Flutter tooling may print dependency/build-hook status before the
            # machine protocol starts. Once the authenticated protocol stream
            # begins, any non-JSON line is corruption and must fail closed.
            if not started:
                continue
            raise ProtocolError(
                f"malformed Flutter machine JSON at line {line_number}: {error.msg}"
            ) from error
        if not started:
            if not isinstance(event, dict) or event.get("type") != "start":
                raise ProtocolError(
                    f"JSON record before Flutter protocol start at line {line_number}"
                )
            started = True
            continue
        if done:
            raise ProtocolError(
                f"record after Flutter protocol done at line {line_number}"
            )
        # Flutter also emits valid JSON arrays for VM service-extension events.
        # They are protocol records, but not testStart records.
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "start":
            raise ProtocolError(f"duplicate Flutter protocol start at line {line_number}")
        if event_type == "testStart":
            count += 1
        elif event_type == "done":
            if event.get("success") is not True:
                raise ProtocolError("Flutter protocol completed without success=true")
            done = True
    if not started:
        raise ProtocolError("Flutter protocol start record is missing")
    if not done:
        raise ProtocolError("Flutter protocol done record is missing")
    return count


def enforce_floor(count: int, minimum: int) -> None:
    if count < minimum:
        raise ProtocolError(
            f"reviewed Flutter test floor is {minimum}; observed {count}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--minimum", type=int, required=True)
    args = parser.parse_args()

    with args.path.open(encoding="utf-8") as stream:
        count = count_test_starts(stream)
    enforce_floor(count, args.minimum)
    print(f"Flutter tests observed: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
