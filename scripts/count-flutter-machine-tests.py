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
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError as error:
            raise ProtocolError(
                f"malformed Flutter machine JSON at line {line_number}: {error.msg}"
            ) from error
        # Flutter also emits valid JSON arrays for VM service-extension events.
        # They are protocol records, but not testStart records.
        if isinstance(event, dict) and event.get("type") == "testStart":
            count += 1
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
