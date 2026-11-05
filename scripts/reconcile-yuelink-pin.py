#!/usr/bin/env python3
"""Emit a reviewed native ledger command; never write yueto or access the network."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shlex
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "pin_manifest_verifier", Path(__file__).with_name("verify-update-manifest.py")
)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)
MAX_BYTES = 1024 * 1024


def plan(raw: bytes, version: str, source: str) -> dict:
    if len(raw) > MAX_BYTES:
        raise ValueError("manifest exceeds 1 MiB")
    manifest = verifier.verify(raw)
    now = datetime.now(timezone.utc)
    expires = datetime.fromisoformat(manifest["expiresAt"].replace("Z", "+00:00"))
    published = datetime.fromisoformat(manifest["publishedAt"].replace("Z", "+00:00"))
    if expires <= now or published > now + timedelta(hours=6):
        raise ValueError("manifest expired or published in the future")
    if manifest["version"] != version:
        raise ValueError("manifest differs from expected release version")
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("expected source must be a full lowercase commit SHA")
    if manifest.get("sourceCommit") != source:
        raise ValueError("manifest differs from expected release source")
    digest = hashlib.sha256(raw).hexdigest()
    return {
        "status": "PLAN_ONLY",
        "yuelink": {
            "version": version,
            "source_commit": source,
            "update_manifest_sha256": digest,
        },
        "command_from_yueto_root": shlex.join(
            [
                "python3",
                "scripts/release-yaml.py",
                "write-yuelink",
                "--version",
                version,
                "--source-commit",
                source,
                "--update-manifest-sha256",
                digest,
            ]
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifest", type=Path, help="fresh exact stable update.json bytes"
    )
    parser.add_argument("--version", required=True, help="expected X.Y.Z, without v")
    parser.add_argument(
        "--source-commit", required=True, help="expected released source SHA"
    )
    args = parser.parse_args(argv)
    try:
        with args.manifest.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        result = plan(raw, args.version, args.source_commit)
    except (OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
