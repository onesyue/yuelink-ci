"""Linux runner pin + build.yml mirror equality (v1.3.83).

ubuntu-latest moves to a newer image on 2026-10-19 and the Linux AppImage
inherits the runner's glibc floor, so no workflow here may use a floating
Ubuntu label. build.yml is not hand-edited: it must be exactly what
sync-build.sh generates from the private repo's build.yml.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))
PRIVATE = Path(os.environ.get("YUELINK_SRC", ROOT.parent / "yuelink"))


def test_scan_floor() -> None:
    # An empty glob would make the label check below vacuously green.
    assert len(WORKFLOWS) >= 8
    assert (ROOT / ".github/workflows/build.yml") in WORKFLOWS


def test_no_floating_ubuntu_label() -> None:
    offenders = [
        f"{wf.name}:{n}"
        for wf in WORKFLOWS
        for n, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1)
        if "ubuntu-latest" in line
    ]
    assert offenders == []


def test_linux_jobs_name_the_pinned_image() -> None:
    labels = set()
    for wf in WORKFLOWS:
        labels.update(re.findall(r"ubuntu-[0-9][0-9.]*", wf.read_text(encoding="utf-8")))
    assert labels == {"ubuntu-24.04"}


@pytest.mark.skipif(
    not (PRIVATE / ".github/workflows/build.yml").is_file(),
    reason="private yuelink checkout not present (set YUELINK_SRC)",
)
def test_build_yml_equals_sync_output() -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "sync-build.sh"), "--check", str(PRIVATE)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
