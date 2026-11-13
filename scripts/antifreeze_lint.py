#!/usr/bin/env python3
"""Reject new tests that freeze a repository inventory.

A test may not newly pin the sha256 of a tracked file, a (line count, sha)
pair, an exact directory listing, a len(inventory) == N equality, or a
file:line reference into another source file. Existing hits live in
tests/antifreeze_baseline.txt and may only shrink. External digests and
fixture goldens are not repository files, so they are not hits.

The scan has to see real test files and a real hash corpus. An empty tree
is a failure, not a pass.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HEX64 = re.compile(r"\b[0-9a-f]{64}\b")
TUPLE_PIN = re.compile(r"\(\s*\d+\s*,\s*['\"][0-9a-f]{7,64}['\"]\s*\)")
LEN_EQ = re.compile(
    r"(?:len\(\s*(inventory|WORKFLOWS|workflows)\s*\)\s*==\s*\d+"
    r"|assertEqual\(\s*len\(\s*(inventory|WORKFLOWS|workflows)\s*\)\s*,\s*\d+"
    r"|expect\(\s*(inventory|workflows)\.length\s*,\s*\d+)"
)
# Workflow file:line pins break when a job moves. Stack frames in crash
# fixtures name a source line as sample text, so they are not this pin.
FILE_LINE = re.compile(r"[\w./-]+\.(?:yml|yaml):\d{2,}")
STRINGS = re.compile(r"""['"]([^'"]+)['"]""")
COLLECTION = re.compile(r"[\{\[\(]([^{}\[\]]{0,4000})[\}\]\)]", re.S)
SKIP_PREFIXES = ("core/mihomo/", ".git/", "build/", ".dart_tool/")
MIN_TEST_FILES = 8
MIN_HASHED_FILES = 20
BASELINE_NAME = "tests/antifreeze_baseline.txt"


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _skipped(rel: str) -> bool:
    if rel == BASELINE_NAME or "/fixtures/" in f"/{rel}" or rel.startswith("fixtures/"):
        return True
    return rel.startswith(SKIP_PREFIXES)


def test_sources(root: Path) -> list[Path]:
    found: list[Path] = []

    def walk_error(error: OSError) -> None:
        raise RuntimeError(f"cannot scan test directory: {error.filename}") from error

    for directory, dirs, files in os.walk(root, onerror=walk_error):
        parent = Path(directory)
        # Prune dependency/cache trees and nested checkouts before traversing.
        # Git worktrees use a .git file; ordinary clones use a .git directory.
        dirs[:] = [
            name
            for name in dirs
            if name not in {".git", ".lane", "node_modules", "vendor"}
            and not _skipped(_rel(parent / name, root) + "/")
            and not (parent / name / ".git").exists()
        ]
        for name in files:
            path = parent / name
            if _skipped(_rel(path, root)) or not path.is_file():
                continue
            if (
                name.startswith(("test_", "test-")) and name.endswith(".py")
            ) or name.endswith(
                (
                    "_test.dart",
                    "_test.go",
                    ".test.ts",
                    ".spec.ts",
                    ".test.tsx",
                    ".spec.tsx",
                )
            ):
                found.append(path)
    return sorted(found)


def repo_index(root: Path) -> tuple[dict[str, str], dict[str, set[str]]]:
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=False,
        capture_output=True,
    )
    if listed.returncode != 0:
        raise RuntimeError("git ls-files failed")
    hashes: dict[str, str] = {}
    listings: dict[str, set[str]] = {}
    for raw in listed.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode()
        if _skipped(rel):
            continue
        path = root / rel
        if not path.is_file() or path.stat().st_size > 1_000_000:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes.setdefault(digest, rel)
        parent = str(Path(rel).parent)
        listings.setdefault(parent, set()).add(Path(rel).name)
    if len(hashes) < MIN_HASHED_FILES:
        raise RuntimeError(f"repo hash corpus is too small ({len(hashes)})")
    return hashes, listings


def text_hits(
    rel: str,
    text: str,
    hashes: dict[str, str],
    listings: dict[str, set[str]],
) -> set[str]:
    hits: set[str] = set()
    for token in set(HEX64.findall(text)):
        pinned = hashes.get(token)
        if pinned is not None and pinned != rel:
            hits.add(f"repo-hash {rel} {pinned}")
    lines = text.splitlines()
    for index, line in enumerate(lines):
        window = "\n".join(lines[index : index + 5])
        if "line_count" in line and re.search(r"[0-9a-f]{7,64}", window):
            hits.add(f"line-sha {rel}")
        if TUPLE_PIN.search(line):
            hits.add(f"line-sha {rel}")
        if LEN_EQ.search(line):
            hits.add(f"inventory-len {rel}")
        for match in FILE_LINE.findall(line):
            # A runtime f-string such as "{name}:{number}" is not a source pin.
            if "{" in match or "}" in match:
                continue
            hits.add(f"file-line {rel} {match}")
    for group in COLLECTION.findall(text):
        names = STRINGS.findall(group)
        if len(names) < 4:
            continue
        basenames = {Path(name).name for name in names}
        for directory, listing in listings.items():
            if len(listing) >= 4 and basenames == listing:
                hits.add(f"path-list {rel} {directory}")
    return hits


def scan(root: Path) -> set[str]:
    sources = test_sources(root)
    if len(sources) < MIN_TEST_FILES:
        raise RuntimeError(f"anti-freeze scan saw {len(sources)} test files")
    hashes, listings = repo_index(root)
    hits: set[str] = set()
    for path in sources:
        hits.update(
            text_hits(
                _rel(path, root), path.read_text(encoding="utf-8"), hashes, listings
            )
        )
    return hits


def baseline_path(root: Path) -> Path:
    candidates = (
        root / "tests/antifreeze_baseline.txt",
        root / "test/antifreeze_baseline.txt",
    )
    for path in candidates:
        if path.is_file():
            return path
    if (root / "test").is_dir() and not (root / "tests").is_dir():
        return candidates[1]
    return candidates[0]


def read_baseline(root: Path) -> set[str]:
    path = baseline_path(root)
    if not path.is_file():
        raise FileNotFoundError(path)
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def ancestor_baseline(root: Path) -> set[str] | None:
    """Return the baseline at merge-base, or None when this commit introduces it.

    Any other git failure is an error. A missing ancestor must not look like
    an empty allow-list after the file already exists on the ancestor.
    """
    base = subprocess.run(
        ["git", "-C", str(root), "merge-base", "HEAD", "origin/master"],
        check=False,
        capture_output=True,
        text=True,
    )
    if base.returncode != 0 or not base.stdout.strip():
        raise RuntimeError("cannot read merge-base with origin/master")
    rel = baseline_path(root).relative_to(root).as_posix()
    spec = f"{base.stdout.strip()}:{rel}"
    exists = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", spec],
        check=False,
        capture_output=True,
        text=True,
    )
    if exists.returncode != 0:
        detail = exists.stderr
        introduced = (
            "does not exist" in detail or "exists on disk, but not in" in detail
        )
        if introduced:
            return None
        raise RuntimeError(f"cannot read {spec}: {detail.strip()}")
    shown = subprocess.run(
        ["git", "-C", str(root), "show", spec],
        check=False,
        capture_output=True,
        text=True,
    )
    if shown.returncode != 0:
        raise RuntimeError(f"cannot show {spec}")
    return {
        line.strip()
        for line in shown.stdout.splitlines()
        if line.strip() and not line.startswith("#")
    }


def compare(
    current: set[str],
    baseline: set[str],
    ancestor: set[str] | None,
) -> list[str]:
    found: list[str] = []
    added = sorted(current - baseline)
    stale = sorted(baseline - current)
    if added:
        found.append("new anti-freeze offender: " + "; ".join(added))
    if stale:
        found.append(
            "anti-freeze baseline still lists a removed offender: " + "; ".join(stale)
        )
    if ancestor is not None and not baseline <= ancestor:
        grown = sorted(baseline - ancestor)
        found.append("anti-freeze baseline grew: " + "; ".join(grown))
    return found


def issues(root: Path) -> list[str]:
    try:
        current = scan(root)
        baseline = read_baseline(root)
    except (FileNotFoundError, RuntimeError) as exc:
        return [str(exc)]
    try:
        ancestor = ancestor_baseline(root)
    except RuntimeError as exc:
        return [str(exc)]
    return compare(current, baseline, ancestor)


def source_snapshot(root: Path, destination: Path, env: dict[str, str]) -> Path:
    """Scan current root-owned bytes without ignored siblings or evidence trees."""
    master = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "origin/master^{commit}"],
        env=env, text=True, timeout=30,
    ).strip()
    subprocess.run(
        ["git", "clone", "--quiet", "--shared", "--no-checkout", str(root), str(destination)],
        env=env, check=True, capture_output=True, timeout=30,
    )
    subprocess.run(
        ["git", "-C", str(destination), "checkout", "--quiet", "--detach", "HEAD"],
        env=env, check=True, capture_output=True, timeout=30,
    )
    subprocess.run(
        ["git", "-C", str(destination), "update-ref", "refs/remotes/origin/master", master],
        env=env, check=True, capture_output=True, timeout=30,
    )
    listed = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        env=env, timeout=30,
    )
    paths = sorted(os.fsdecode(raw) for raw in set(listed.split(b"\0")) - {b""})
    original = subprocess.check_output(
        ["git", "-C", str(destination), "ls-files", "-z"], env=env, timeout=30,
    )
    original_paths = {os.fsdecode(raw) for raw in original.split(b"\0") if raw}
    links: list[tuple[Path, str]] = []
    # Remove checkout files first: a former link must not redirect later copies.
    for rel in original_paths:
        target = destination / rel
        if target.is_symlink() or target.is_file():
            target.unlink()
    for rel in paths:
        source, target = root / rel, destination / rel
        if target.is_symlink() or target.is_file():
            target.unlink()
        if source.is_symlink():
            link = os.readlink(source)
            if Path(link).is_absolute():
                raise OSError("Absolute symbolic link cannot be projected")
            links.append((target, link))
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    # Install links only after copying regular files, then reject links whose
    # inputs are absent or outside the projection instead of silently skipping.
    for target, link in links:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(link)
    for target, _ in links:
        try:
            resolved = target.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise OSError("Symbolic link cannot be projected") from error
        if not resolved.is_relative_to(destination.resolve()):
            raise OSError("Symbolic link escapes projection")
    # The shared hash corpus uses Git membership, including new local files.
    subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(destination), "add", "-f",
         "--pathspec-from-file=-", "--pathspec-file-nul"],
        input=b"".join(os.fsencode(path) + b"\0" for path in sorted(set(paths) | original_paths)),
        env=env, check=True, capture_output=True, timeout=30,
    )
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1],
        help="repository to scan (default: this policy repository)",
    )
    parser.add_argument(
        "--git-owned", action="store_true",
        help="scan tracked and non-ignored current files in a private Git snapshot",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if args.git_owned:
        # Git hook selectors must not redirect either repository. This only
        # changes the isolated CLI process, never the caller's environment.
        for name in list(os.environ):
            if name.startswith("GIT_"):
                del os.environ[name]
        try:
            with tempfile.TemporaryDirectory(prefix="antifreeze-source-") as directory:
                snapshot = source_snapshot(root, Path(directory) / "source", dict(os.environ))
                found = issues(snapshot)
        except (OSError, subprocess.SubprocessError) as error:
            print(f"anti-freeze source snapshot unavailable: {type(error).__name__}", file=sys.stderr)
            return 2
    else:
        found = issues(root)
    if found:
        for item in found:
            print(item, file=sys.stderr)
        return 1
    print("anti-freeze lint passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
