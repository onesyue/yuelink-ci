#!/usr/bin/env python3
"""Prefer a reachable official HTTPS archive in the hosted Ubuntu mirror list."""

import re
import subprocess
from pathlib import Path

# The Ubuntu jobs check out this script on its own.
MIRROR_LIST_PATH = Path("/etc/apt/apt-mirrors.txt")
PROBE_TIMEOUT_SECONDS = "8"
HTTP_ARCHIVE = re.compile(
    r"^([ \t]*)http://(?:[a-z0-9.-]+\.)?(?:archive|security)\.ubuntu\.com"
    r"/ubuntu(?=/|[ \t\r\n]|$)", re.MULTILINE,
)
ARCHIVES = ("archive.ubuntu.com", "azure.archive.ubuntu.com")


def configure(path=MIRROR_LIST_PATH):
    if path.is_symlink() or not path.is_file():
        return
    original = path.read_bytes().decode("utf-8")
    if not HTTP_ARCHIVE.search(original):
        return
    for host in ARCHIVES:
        base = f"https://{host}/ubuntu"
        # Both affected jobs run on ubuntu-24.04. apt verifies signed metadata
        # against the runner keyring after the ordinary update.
        probe = subprocess.run(["curl", "--fail", "--silent", "--show-error",
                                "--max-time", PROBE_TIMEOUT_SECONDS, "--output", "/dev/null",
                                f"{base}/dists/noble/InRelease"])
        if probe.returncode:
            continue
        updated = HTTP_ARCHIVE.sub(lambda match: match[1] + base, original)
        subprocess.run(["sudo", "tee", str(path)], input=updated.encode("utf-8"),
                       stdout=subprocess.DEVNULL, check=True)
        print(f"Selected {base} for Ubuntu package downloads")
        return


if __name__ == "__main__":
    configure()
