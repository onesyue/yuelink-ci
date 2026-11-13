#!/usr/bin/env python3
"""Self-check for the canonical public build workflow.

The private repository no longer carries a product workflow, so this file is
not a projection of one. Write mode is refused by sync-build.sh. build.yml
calls plan, validate, build-matrix, publish and the dial gate. A missing
called workflow, a missing marker, or a private checkout without the deploy
key fails closed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MIN_PRIVATE_CHECKOUTS = 7
CALLED = ("plan.yml", "validate.yml", "build-matrix.yml", "publish.yml")
INSTALLER = re.compile(
    r"onesyue/yuelink-ci/\.github/actions/setup-flutter@[0-9a-f]{40}"
)
_JOB_HEAD = re.compile(r"(?m)^(?=  [A-Za-z0-9_-]+:\s*$)")
REQUIRED = (
    "PUBLIC CI MIRROR",
    "resolve-release-tag.sh",
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
    "needs.plan.result == 'success'",
    "needs.validate.result == 'success'",
    "needs.dial_gate.result == 'success'",
    "needs.dial_gate_summary.result == 'success'",
    "value: ${{ jobs.source_binding.outputs.source_commit }}",
    "value: ${{ jobs.preflight.outputs.tag_kind }}",
    "secrets: inherit",
)
DIAL_PLATFORMS = ("windows", "macos", "linux", "android", "ios")


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


def job_body(text: str, name: str) -> str:
    prefix = f"  {name}:\n"
    for job in _JOB_HEAD.split(text):
        if job.startswith(prefix):
            return job
    return ""


def composed_text(path: Path) -> str:
    """Concatenate the orchestrator and the four workflows it always calls.

    A missing sibling raises. The dial workflow is reached by
    composed_workflow(), because the orchestrator calls it by path.
    """
    parts = [path.read_text(encoding="utf-8")]
    for name in CALLED:
        sibling = path.parent / name
        if not sibling.is_file():
            raise FileNotFoundError(sibling)
        parts.append(sibling.read_text(encoding="utf-8"))
    return "\n".join(parts)


_LOCAL_USES = re.compile(
    r"uses:\s+\./\.github/workflows/([A-Za-z0-9_.-]+\.ya?ml)\b"
)


def composed_workflow(path: Path) -> str:
    """Caller text plus every local reusable workflow it reaches.

    A checkout or a dial step that moved into a called workflow still counts.
    A missing called file fails closed instead of looking like a smaller
    workflow. Remote actions are not workflows and are left unread.
    """
    repo = path.resolve()
    while not (repo / ".github" / "workflows").is_dir():
        if repo.parent == repo:
            raise FileNotFoundError(f"no workflow directory above {path}")
        repo = repo.parent
    seen: set[Path] = set()
    parts: list[str] = []

    def add(item: Path) -> None:
        resolved = item.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        if not item.is_file():
            raise FileNotFoundError(f"reusable workflow is missing: {item}")
        text = item.read_text(encoding="utf-8")
        parts.append(text)
        for name in _LOCAL_USES.findall(text):
            add(repo / ".github" / "workflows" / name)

    add(path)
    return "\n".join(parts)

def issues(text: str) -> list[str]:
    found: list[str] = []
    if not text.strip():
        return ["public build workflow is empty"]
    for marker in REQUIRED:
        if marker not in text:
            found.append(f"public build workflow lost {marker!r}")
    for name in CALLED:
        uses = f"uses: ./.github/workflows/{name}"
        if uses not in text:
            found.append(f"build.yml does not call {name}")
            continue
        window = text[text.find(uses) : text.find(uses) + 500]
        if "secrets: inherit" not in window:
            found.append(f"{name} call does not inherit secrets")
    if "ubuntu-latest" in text:
        found.append("public build workflow uses the floating ubuntu-latest label")
    lines = text.splitlines()
    private = [
        index
        for index, line in enumerate(lines)
        if line.strip() == "repository: onesyue/yuelink"
    ]
    if len(private) < MIN_PRIVATE_CHECKOUTS:
        found.append(
            "private source checkouts fell below the floor "
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
        values = set(re.findall(rf"(?m)^\s*{name}:\s*'([0-9.]+)'\s*$", text))
        if len(values) != 1:
            found.append(f"{name} must be one pinned value, found {sorted(values)}")
    if "validate" not in build_job_needs(text):
        found.append("build job must need validate")
    if not re.search(r"(?m)^  preflight:\s*$", text):
        found.append("reusable workflows lost the preflight job")
    publish = job_body(text, "publish")
    if "needs.build.result" in publish:
        found.append("publish must not require the build job to succeed")
    for platform in DIAL_PLATFORMS:
        if not re.search(rf"(?m)^[ ]+- name: {platform}\s*$", text):
            found.append(f"dial gate lost {platform}")
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
    missing = [name for name in CALLED if not (path.parent / name).is_file()]
    if missing:
        for name in missing:
            print(f"::error::missing reusable workflow {name}", file=sys.stderr)
        return 1
    try:
        text = composed_workflow(path)
    except FileNotFoundError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    found = issues(text)
    if found:
        for item in found:
            print(f"::error::{item}", file=sys.stderr)
        return 1
    print("public build workflow self-check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
