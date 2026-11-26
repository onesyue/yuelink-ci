#!/usr/bin/env python3
"""Emit a reviewed native ledger command; never write yueto or access the network.

Version, source commit and the raw update.json SHA-256 are derived from the
YueLink checkout, its annotated release tag and pubspec.yaml, plus the signed
stable update.json bytes. A caller-supplied copy of any of those facts must
match the derived value. This planner does not read or bump yueto release.yaml.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import runpy
import shlex
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "pin_manifest_verifier", Path(__file__).with_name("verify-update-manifest.py")
)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)
_LIMITS = runpy.run_path(str(Path(__file__).resolve().with_name("constants.py")))
MAX_BYTES = _LIMITS["MAX_UPDATE_MANIFEST_BYTES"]
_PUBLISH_CLOCK_SKEW = timedelta(seconds=_LIMITS["MAX_PUBLISH_CLOCK_SKEW_SECONDS"])

_PUBSPEC_VERSION = re.compile(
    r"^version:[ \t]+([0-9]+\.[0-9]+\.[0-9]+)(?:\+[0-9]+)?[ \t]*\r?$",
    re.MULTILINE,
)
_ATTESTED_SOURCE = re.compile(r"(?m)^ATTESTED_SOURCE_COMMIT=([0-9a-f]{40})$")
_COMMIT = re.compile(r"[0-9a-f]{40}")


def _git(repo: Path, *args: str, error: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise ValueError(error) from exc
    if result.returncode != 0:
        raise ValueError(error)
    return result.stdout.strip()


def _one_pubspec_version(text: str, error: str) -> str:
    found = _PUBSPEC_VERSION.findall(text)
    if len(found) != 1:
        raise ValueError(error)
    return found[0]


def derive_release_facts(checkout: Path) -> tuple[str, str]:
    """Return (version, source_commit) from the checkout, tag and pubspec."""
    if not checkout.is_dir():
        raise ValueError("yuelink checkout is missing")
    head_pubspec = _git(
        checkout,
        "show",
        "HEAD:pubspec.yaml",
        error="checkout HEAD has no readable pubspec.yaml",
    )
    try:
        worktree_pubspec = (checkout / "pubspec.yaml").read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError("checkout pubspec.yaml is not readable") from exc
    version = _one_pubspec_version(
        head_pubspec, "HEAD pubspec.yaml must contain one stable X.Y.Z version"
    )
    if (
        _one_pubspec_version(
            worktree_pubspec,
            "checkout pubspec.yaml must contain one stable X.Y.Z version",
        )
        != version
    ):
        raise ValueError("checkout pubspec version drifts from HEAD")
    tag = f"v{version}"
    kind = _git(
        checkout,
        "cat-file",
        "-t",
        f"refs/tags/{tag}",
        error="annotated release tag is missing",
    )
    if kind != "tag":
        raise ValueError("release ref must be an annotated tag")
    source = _git(
        checkout,
        "rev-parse",
        f"refs/tags/{tag}^{{commit}}",
        error="annotated tag does not peel to a commit",
    )
    if _COMMIT.fullmatch(source) is None:
        raise ValueError("annotated tag does not peel to a full commit SHA")
    head = _git(checkout, "rev-parse", "HEAD", error="checkout HEAD is not a commit")
    if head != source:
        raise ValueError("checkout HEAD drifts from the annotated tag")
    tag_pubspec = _git(
        checkout,
        "show",
        f"refs/tags/{tag}:pubspec.yaml",
        error="annotated tag has no readable pubspec.yaml",
    )
    if (
        _one_pubspec_version(
            tag_pubspec,
            "annotated tag pubspec.yaml must contain one stable version",
        )
        != version
    ):
        raise ValueError("annotated tag pubspec drifts from the checkout version")
    message = _git(
        checkout,
        "for-each-ref",
        "--format=%(contents)",
        f"refs/tags/{tag}",
        error="annotated tag message is not readable",
    )
    attested = _ATTESTED_SOURCE.findall(message)
    if attested and attested != [source]:
        raise ValueError("annotated tag source binding drifts from the tagged commit")
    return version, source


def _refuse_copy(name: str, supplied: str | None, derived: str) -> None:
    if supplied is not None and supplied != derived:
        raise ValueError(f"caller-supplied {name} drifts from the derived release fact")


def plan(
    raw: bytes,
    checkout: Path,
    *,
    version: str | None = None,
    source_commit: str | None = None,
    update_manifest_sha256: str | None = None,
) -> dict:
    if len(raw) > MAX_BYTES:
        raise ValueError("manifest exceeds 1 MiB")
    manifest = verifier.verify(raw)
    now = datetime.now(timezone.utc)
    expires = datetime.fromisoformat(manifest["expiresAt"].replace("Z", "+00:00"))
    published = datetime.fromisoformat(manifest["publishedAt"].replace("Z", "+00:00"))
    if expires <= now or published > now + _PUBLISH_CLOCK_SKEW:
        raise ValueError("manifest expired or published in the future")
    derived_version, derived_source = derive_release_facts(checkout)
    if manifest["version"] != derived_version:
        raise ValueError("manifest differs from expected release version")
    if manifest.get("sourceCommit") != derived_source:
        raise ValueError("manifest differs from expected release source")
    digest = hashlib.sha256(raw).hexdigest()
    _refuse_copy("version", version, derived_version)
    _refuse_copy("source_commit", source_commit, derived_source)
    _refuse_copy("update_manifest_sha256", update_manifest_sha256, digest)
    return {
        "status": "PLAN_ONLY",
        "yuelink": {
            "version": derived_version,
            "source_commit": derived_source,
            "update_manifest_sha256": digest,
        },
        "command_from_yueto_root": shlex.join(
            [
                "python3",
                "scripts/release-yaml.py",
                "write-yuelink",
                "--version",
                derived_version,
                "--source-commit",
                derived_source,
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
    parser.add_argument(
        "--checkout",
        required=True,
        type=Path,
        help="YueLink git checkout used to derive version and source commit",
    )
    parser.add_argument(
        "--version",
        help="optional copy of the derived X.Y.Z; refused if it drifts",
    )
    parser.add_argument(
        "--source-commit",
        help="optional copy of the derived source SHA; refused if it drifts",
    )
    parser.add_argument(
        "--update-manifest-sha256",
        help="optional copy of the derived raw-byte SHA-256; refused if it drifts",
    )
    args = parser.parse_args(argv)
    try:
        with args.manifest.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        result = plan(
            raw,
            args.checkout,
            version=args.version,
            source_commit=args.source_commit,
            update_manifest_sha256=args.update_manifest_sha256,
        )
    except (OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
