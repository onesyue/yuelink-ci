#!/usr/bin/env python3
"""Fail if first-party builder paths regress to the retired Telegram bot.

t1218u: the official handle is @yuetoo_bot. This scan rejects that handle's
retired predecessor and the t.me URL that uses it. The group yue_to and the
channel yueto_news are different strings and stay allowed.

The guarded layer is this public builder. policy-ci checks out yuelink-ci,
not the private application tree. Historical fixtures, release proofs,
reviews and reports are outside the scan, so a frozen artifact cannot
satisfy the file floor or hide a hit in a path this gate claims to cover.
A missing root, an empty walk, an unreadable file, or a symlink fails closed.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

# Split so this file does not itself contain the retired handle.
RETIRED_HANDLE = "yue" + "_to_bot"
RETIRED_URL = "t.me/" + RETIRED_HANDLE
NEEDLES = (
    ("retired-url", RETIRED_URL),
    ("retired-handle", RETIRED_HANDLE),
)
MIN_FILES = 80
SUFFIXES = frozenset({
    ".py",
    ".sh",
    ".yml",
    ".yaml",
    ".md",
    ".json",
    ".txt",
    ".patch",
    ".gradle",
    ".toml",
})
EXCLUDE_DIRS = frozenset({"fixtures", "release-proofs", "__pycache__"})
ROOT_ENTRIES = (
    Path(".github"),
    Path("scripts"),
    Path("policy"),
    Path("docs"),
    Path("tests"),
    Path("README.md"),
    Path("FINDINGS.md"),
    Path("release.sh"),
    Path("sync-build.sh"),
)
ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Hit:
    path: str
    label: str


@dataclass(frozen=True)
class ScanResult:
    paths: tuple[str, ...]
    hits: tuple[Hit, ...]
    errors: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.paths)


def candidate_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for entry in ROOT_ENTRIES:
        path = root / entry
        if path.is_symlink():
            found.append(path)
            continue
        if path.is_file():
            found.append(path)
            continue
        if not path.is_dir():
            continue
        for file in path.rglob("*"):
            if not file.is_symlink() and not file.is_file():
                continue
            relative = file.relative_to(root)
            if any(part in EXCLUDE_DIRS for part in relative.parts):
                continue
            if file.suffix not in SUFFIXES:
                continue
            found.append(file)
    return sorted(found)


def needle_labels(text: str) -> tuple[str, ...]:
    folded = text.casefold()
    return tuple(label for label, needle in NEEDLES if needle.casefold() in folded)


def scan(root: Path) -> ScanResult:
    paths: list[str] = []
    hits: list[Hit] = []
    errors: list[str] = []
    for file in candidate_files(root):
        relative = file.relative_to(root).as_posix()
        paths.append(relative)
        if file.is_symlink():
            errors.append(f"{relative}: symlink")
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeError:
            errors.append(f"{relative}: not utf-8")
            continue
        except OSError:
            errors.append(f"{relative}: unreadable")
            continue
        for label in needle_labels(text):
            hits.append(Hit(relative, label))
    return ScanResult(tuple(paths), tuple(hits), tuple(errors))


def evaluate(result: ScanResult, *, min_files: int = MIN_FILES) -> list[str]:
    problems: list[str] = []
    if result.count < min_files:
        counted = "file" if result.count == 1 else "files"
        problems.append(
            f"scanned only {result.count} first-party {counted}; need at least {min_files}"
        )
    problems.extend(result.errors)
    problems.extend(f"{hit.path}: {hit.label}" for hit in result.hits)
    return problems


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    if len(args) > 2:
        print("usage: check_bot_handle.py [root]", file=sys.stderr)
        return 2
    root = Path(args[1]) if len(args) == 2 else ROOT
    result = scan(root)
    problems = evaluate(result)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"bot-handle gate: scanned {result.count} first-party files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
