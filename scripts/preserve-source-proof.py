#!/usr/bin/env python3
"""Verify durable release provenance; publish only when explicitly requested.

Both durable assets must exist together. Invalid durable evidence never falls
back to an artifact or the one-time, signed migration archive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = "onesyue/yuelink-ci"
SCRIPTS = Path(__file__).resolve().parent
ARCHIVE = SCRIPTS.parent / "docs/release-proofs"
PROOF = "source-attestation.json"
BUNDLE = "source-attestation.sigstore.jsonl"


def read(*args: str) -> bytes:
    return subprocess.check_output(
        [sys.executable, str(SCRIPTS / "gh-read-retry.py"), "gh", *args]
    )


def assets(release_id: int) -> dict[str, dict]:
    found = {}
    seen = set()
    for page in range(1, 101):
        values = json.loads(
            read(
                "api",
                f"repos/{REPO}/releases/{release_id}/assets?per_page=100&page={page}",
            )
        )
        if not isinstance(values, list):
            raise TypeError("invalid release asset list")
        for value in values:
            identity = value.get("id")
            if type(identity) is not int or identity <= 0 or identity in seen:
                raise ValueError("invalid or duplicate release asset")
            seen.add(identity)
            if value.get("name") in (PROOF, BUNDLE):
                if value["name"] in found or value.get("state") != "uploaded":
                    raise ValueError("ambiguous or incomplete durable proof")
                found[value["name"]] = value
        if len(values) < 100:
            return found
    raise ValueError("release asset pagination exhausted")


def verify(
    binding: dict,
    proof: Path | None = None,
    bundle: Path | None = None,
    export: Path | None = None,
) -> None:
    args = [
        "bash",
        str(SCRIPTS / "verify-source-attestation.sh"),
        binding["tag"],
        binding["source"],
        binding["builder"],
        binding["attestation_run"],
    ]
    for flag, path in [
        ("--proof-file", proof),
        ("--bundle-file", bundle),
        ("--export-verified-proof", export),
    ]:
        if path is not None:
            args.extend([flag, str(path)])
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL)


def acquire(binding: dict, release_id: int, directory: Path) -> str:
    present = assets(release_id)
    proof, bundle = directory / PROOF, directory / BUNDLE
    if present:
        if set(present) != {PROOF, BUNDLE}:
            raise ValueError("partial durable proof; fallback forbidden")
        for name, asset in present.items():
            data = read(
                "api",
                f"repos/{REPO}/releases/assets/{asset['id']}",
                "-H",
                "Accept: application/octet-stream",
            )
            if len(data) != asset.get("size"):
                raise ValueError("durable proof size mismatch")
            (directory / name).write_bytes(data)
        origin = "release-assets"
    else:
        archive = ARCHIVE / binding["tag"]
        if archive.exists():
            for name in (PROOF, BUNDLE):
                source = archive / name
                if not source.is_file() or source.is_symlink():
                    raise ValueError("incomplete migration proof")
                (directory / name).write_bytes(source.read_bytes())
            origin = "signed-migration-archive"
        else:
            verify(binding, export=proof)
            # Download the public bundle for these exact bytes, in a private cwd.
            subprocess.run(
                ["gh", "attestation", "download", str(proof), "--repo", REPO],
                cwd=directory,
                timeout=150,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            downloaded = directory / (
                "sha256:" + hashlib.sha256(proof.read_bytes()).hexdigest() + ".jsonl"
            )
            if not downloaded.is_file() or downloaded.is_symlink():
                raise ValueError("missing downloaded attestation bundle")
            downloaded.rename(bundle)
            origin = "actions-artifact"
    verify(binding, proof, bundle)
    return origin


def preserve(binding: dict, release_id: int, directory: Path, publish: bool) -> str:
    origin = acquire(binding, release_id, directory)
    if publish and origin != "release-assets":
        # No clobber: conflicting/racing publication fails instead of overwriting.
        subprocess.run(
            [
                "gh",
                "release",
                "upload",
                binding["tag"],
                str(directory / PROOF),
                str(directory / BUNDLE),
                "--repo",
                REPO,
            ],
            check=True,
        )
        with tempfile.TemporaryDirectory(prefix="yuelink-proof-readback-") as temporary:
            check_dir = Path(temporary)
            if acquire(binding, release_id, check_dir) != "release-assets":
                raise ValueError("durable proof absent after publication")
            for name in (PROOF, BUNDLE):
                if (directory / name).read_bytes() != (check_dir / name).read_bytes():
                    raise ValueError("durable proof changed during publication")
    return origin


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("source")
    parser.add_argument("builder")
    parser.add_argument("attestation_run")
    parser.add_argument("--release-id", type=int)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if (
        not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", args.tag)
        or not all(
            re.fullmatch(r"[0-9a-f]{40}", v) for v in (args.source, args.builder)
        )
        or not re.fullmatch(r"[1-9][0-9]*", args.attestation_run)
    ):
        raise ValueError("invalid proof binding")
    binding = {
        key: getattr(args, key)
        for key in ("tag", "source", "builder", "attestation_run")
    }
    release = json.loads(read("api", f"repos/{REPO}/releases/tags/{args.tag}"))
    identity = release.get("id")
    if (
        type(identity) is not int
        or identity <= 0
        or release.get("tag_name") != args.tag
        or args.release_id is not None
        and args.release_id != identity
    ):
        raise ValueError("release identity mismatch")
    # Independent tag validation protects both caller-supplied identities.
    resolved = json.loads(
        subprocess.check_output(
            ["bash", str(SCRIPTS / "resolve-release-tag.sh"), args.tag, args.builder]
        )
    )
    if any(resolved.get(key) != value for key, value in binding.items()):
        raise ValueError("signed tag proof binding mismatch")
    with tempfile.TemporaryDirectory(prefix="yuelink-durable-proof-") as temporary:
        origin = preserve(binding, identity, Path(temporary), args.publish)
    print(origin)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError):
        print("::error::durable source proof verification failed", file=sys.stderr)
        sys.exit(1)
