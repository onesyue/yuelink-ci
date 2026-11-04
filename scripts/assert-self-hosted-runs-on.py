#!/usr/bin/env python3
"""Prove every job uses literal self-hosted labels; unknown syntax is rejected."""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("::error::self-hosted admission requires PyYAML (requirements-ci.txt)")


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate and merge keys instead of silently overriding a job."""

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, (str, bool)) or key in result:
                raise ValueError("duplicate or unsupported YAML mapping key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def check(path: Path) -> None:
    workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
    if not isinstance(workflow, dict):
        raise ValueError("workflow must be a mapping")
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        raise ValueError("workflow must contain jobs")
    for name, job in jobs.items():
        if not isinstance(job, dict):
            raise ValueError(f"job {name}: invalid mapping")
        value = job.get("runs-on")
        labels = [value] if isinstance(value, str) else value
        if not isinstance(labels, list) or not labels:
            raise ValueError(f"job {name}: missing or unprovable runs-on")
        for label in labels:
            # Expressions, aliases to non-label objects, maps and YAML tags
            # cannot smuggle a dynamic runner choice through this allowlist.
            if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", label):
                raise ValueError(f"job {name}: nonliteral or unsupported runner label")
            if re.search(r"^(ubuntu-|macos-|windows-)|-latest$", label, re.I):
                raise ValueError(f"job {name}: hosted runner label is forbidden")
        if "self-hosted" not in labels:
            raise ValueError(f"job {name}: self-hosted label is required")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: assert-self-hosted-runs-on.sh <workflow files...>", file=sys.stderr)
        return 2
    failed = False
    for arg in sys.argv[1:]:
        try:
            check(Path(arg))
        except (OSError, ValueError, yaml.YAMLError, RecursionError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            print(f"::error::{arg}: self-hosted admission rejected: {reason}", file=sys.stderr)
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
