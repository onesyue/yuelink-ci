#!/usr/bin/env python3
"""Select a signed, reviewed update floor from the exact builder checkout."""
import argparse
import importlib.util
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "update_manifest", ROOT / "scripts/verify-update-manifest.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("update manifest verifier is unavailable")
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


def select_baseline(manifest_path: Path) -> str:
    # Historical policy verifies the pinned signing key and complete schema.
    # Do not discover a baseline from the candidate, a tag, or a live CDN.
    manifest = VERIFIER.verify_historical_archive(manifest_path.read_bytes())
    baseline = manifest.get("sourceCommit")
    if not isinstance(baseline, str) or not re.fullmatch(r"[0-9a-f]{40}", baseline):
        raise ValueError("signed contract baseline must be an exact source SHA")
    return baseline


def validate_baseline(source: Path, baseline: str) -> None:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=source, text=True).strip()

    candidate = git("rev-parse", "HEAD")
    if baseline == candidate:
        raise ValueError("contract baseline is the candidate itself")
    if git("rev-parse", baseline + "^{commit}") != baseline:
        raise ValueError("contract baseline must identify a commit")
    subprocess.run(["git", "merge-base", "--is-ancestor", baseline, candidate],
                   cwd=source, check=True)
    if git("rev-parse", baseline + "^{tree}") == git("rev-parse", "HEAD^{tree}"):
        raise ValueError("contract baseline has the same tree as the candidate")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    baseline = select_baseline(ROOT / "tests/fixtures/update-manifest-v1.json")
    if args.source is not None:
        validate_baseline(args.source, baseline)
    print(baseline)


if __name__ == "__main__":
    main()
