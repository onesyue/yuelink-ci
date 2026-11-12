#!/usr/bin/env python3
"""Self-check for the canonical public build workflow.

The private repository no longer carries a product workflow, so this file is
not a projection of one. Write mode is refused by sync-build.sh. A missing
marker or a private checkout without the deploy key fails closed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MIN_PRIVATE_CHECKOUTS = 7
INSTALLER = re.compile(
    r"onesyue/yuelink-ci/\.github/actions/setup-flutter@[0-9a-f]{40}"
)
_JOB_HEAD = re.compile(r"(?m)^(?=  [A-Za-z0-9_-]+:\s*$)")
REQUIRED = (
    "PUBLIC CI MIRROR",
    "gpgverify_unavailable",
    "vless-reality-tfo",
    "vless-reality",
    "vless-ws-tls",
    "hysteria2",
    "stable manifest must contain all 9 platform artifacts",
    "dart run tool/windows_durability_probe.dart",
    "name: Sign root manifest and promote (in-pipeline)",
    'bash scripts/ci/promote_in_pipeline.sh "$APP_VERSION"',
    'bash .public-builder/sync-build.sh --check "$GITHUB_WORKSPACE"',
    "repository: onesyue/yuelink-ci",
    "FLUTTER_VERSION:",
    "GO_VERSION:",
)


def build_job_needs(text: str) -> list[str]:
    for job in _JOB_HEAD.split(text):
        if not job.startswith("  build:\n"):
            continue
        inline = re.search(r"(?m)^    needs:\s*\[([^\]]*)\]\s*$", job)
        if not inline:
            return []
        return [
            item.strip().strip("'\"")
            for item in inline.group(1).split(",")
            if item.strip()
        ]
    return []

def issues(text: str) -> list[str]:
    found: list[str] = []
    for marker in REQUIRED:
        if marker not in text:
            found.append(f"public build.yml lost {marker!r}")
    if "ubuntu-latest" in text:
        found.append("public build.yml uses the floating ubuntu-latest label")
    lines = text.splitlines()
    private = [
        index
        for index, line in enumerate(lines)
        if line.strip() == "repository: onesyue/yuelink"
    ]
    if len(private) < MIN_PRIVATE_CHECKOUTS:
        found.append(
            f"private source checkouts fell below the floor "
            f"({len(private)} < {MIN_PRIVATE_CHECKOUTS})"
        )
    for index in private:
        window = "\n".join(lines[index : index + 8])
        if "ssh-key: ${{ secrets.SRC_DEPLOY_KEY }}" not in window:
            found.append(
                f"private checkout near line {index + 1} has no SRC_DEPLOY_KEY"
            )
    installers = set(INSTALLER.findall(text))
    if len(installers) != 1:
        found.append(
            f"expected one setup-flutter installer, found {sorted(installers)}"
        )
    envs = set(
        re.findall(r"(?m)^[ ]*YUELINK_FLUTTER_INSTALLER:\s*(\S+)\s*$", text)
    )
    if envs != installers:
        found.append(
            "YUELINK_FLUTTER_INSTALLER "
            f"{sorted(envs)} must match setup-flutter {sorted(installers)}"
        )
    for name in ("FLUTTER_VERSION", "GO_VERSION"):
        values = set(
            re.findall(rf"(?m)^\s*{name}:\s*'([0-9.]+)'\s*$", text)
        )
        if len(values) != 1:
            found.append(f"{name} must be one pinned value, found {sorted(values)}")
    if "preflight" not in build_job_needs(text):
        found.append("build job must need preflight")
    android = re.search(
        r"uses:\s*android-actions/setup-android@\S+.*?(?=\n      - )",
        text,
        re.S,
    )
    packages = (
        []
        if android is None
        else [
            value.strip().strip("'\"")
            for value in re.findall(
                r"(?m)^\s+packages:\s*([^#\n]+)",
                android.group(0),
            )
        ]
    )
    if packages != ["platform-tools"]:
        found.append(
            "Android SDK packages must explicitly be platform-tools; "
            f"saw {packages}"
        )
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_public_build.py <build.yml>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.is_file():
        print(f"::error::missing public workflow {path}", file=sys.stderr)
        return 1
    found = issues(path.read_text(encoding="utf-8"))
    if found:
        for item in found:
            print(f"::error::{item}", file=sys.stderr)
        return 1
    print("public build.yml self-check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
