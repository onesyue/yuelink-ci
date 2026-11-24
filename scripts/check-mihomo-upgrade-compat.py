#!/usr/bin/env python3
"""Read-only mihomo ledger and patch-preserving upgrade checks.

Default mode never writes, never fetches, and never treats a newer assessed
stable tag as an adopted runtime pin. Ancestry is judged only against the
ledger's declared base. When --source-root is used, a reviewed patch-id floor
shipped in this repository is applied even if --previous-ledger is omitted.
Source-ledger retired_backports alone cannot drop a floor patch-id.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

LEDGER_RELATIVE = Path("docs/governance/mihomo-fork-ledger.json")
MIHOMO_RELATIVE = Path("core/mihomo")
DEFAULT_FLOOR = (
    Path(__file__).resolve().parents[1]
    / "tests/fixtures/mihomo-upgrade-compat/reviewed-patch-floor.json"
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TAG_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
GITLINK_RE = re.compile(r"^160000 ([0-9a-f]{40}) 0\tcore/mihomo$")
REQUIRED_TOP = (
    "schema",
    "upstream",
    "fork",
    "commit_order",
    "runtime_commits",
    "assurance_commits",
    "backport_commits",
    "retired_backports",
)
REQUIRED_UPSTREAM = (
    "channel",
    "stable_tag",
    "base",
    "latest_stable_tag",
    "assessed_meta_head",
)
REQUIRED_FORK = ("pinned_head", "ahead_by", "behind_by")


class CompatError(ValueError):
    """The ledger is inconsistent, or a rebase dropped unretired floor patches."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CompatError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_ledger(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except OSError as exc:
        raise CompatError(f"ledger is unreadable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CompatError("ledger is not valid JSON") from exc
    if not isinstance(value, dict):
        raise CompatError("ledger must be a JSON object")
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CompatError(message)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    _require(isinstance(value, dict), f"{label} must be an object")
    return value


def _string(value: Any, label: str) -> str:
    _require(isinstance(value, str) and value.strip() != "", f"{label} must be a non-empty string")
    return value


def _sha(value: Any, label: str) -> str:
    text = _string(value, label)
    _require(bool(SHA_RE.fullmatch(text)), f"{label} must be a full lowercase commit SHA")
    return text


def _tag(value: Any, label: str) -> str:
    text = _string(value, label)
    _require(bool(TAG_RE.fullmatch(text)), f"{label} must be a stable vX.Y.Z tag")
    return text


def _entries(value: Any, label: str) -> list[dict[str, Any]]:
    _require(isinstance(value, list), f"{label} must be an array")
    entries: list[dict[str, Any]] = []
    for item in value:
        _require(isinstance(item, dict), f"{label} items must be objects")
        entries.append(item)
    return entries


def classified_intents(ledger: dict[str, Any]) -> dict[str, str]:
    commits: dict[str, str] = {}
    for label in ("runtime_commits", "assurance_commits", "backport_commits"):
        for item in _entries(ledger.get(label), label):
            digest = _sha(item.get("sha"), f"{label} sha")
            intent = _string(item.get("intent"), f"{label} intent")
            _require(digest not in commits, f"duplicate fork commit {digest}")
            commits[digest] = intent
    return commits


def inspect(ledger: dict[str, Any]) -> None:
    for key in REQUIRED_TOP:
        _require(key in ledger, f"missing {key}")
    _require(ledger.get("schema") == 1, "unsupported ledger schema")
    upstream = _mapping(ledger.get("upstream"), "upstream")
    fork = _mapping(ledger.get("fork"), "fork")
    for key in REQUIRED_UPSTREAM:
        _require(key in upstream, f"missing upstream.{key}")
    for key in REQUIRED_FORK:
        _require(key in fork, f"missing fork.{key}")
    _require(upstream.get("channel") == "Meta", "upstream channel must remain Meta")
    stable = _tag(upstream.get("stable_tag"), "stable_tag")
    latest = _tag(upstream.get("latest_stable_tag"), "latest_stable_tag")
    _sha(upstream.get("base"), "base")
    assessed = _sha(upstream.get("assessed_meta_head"), "assessed_meta_head")
    compare = upstream.get("assessment_compare_url")
    if isinstance(compare, str) and compare:
        _require(
            stable in compare and latest in compare,
            "assessment_compare_url must mention stable_tag and latest_stable_tag",
        )
    pin = _sha(fork.get("pinned_head"), "pinned_head")
    ahead = fork.get("ahead_by")
    behind = fork.get("behind_by")
    _require(type(ahead) is int and ahead >= 0, "ahead_by must be a non-negative integer")
    _require(type(behind) is int and behind >= 0, "behind_by must be a non-negative integer")
    _require(behind == 0, "fork is behind its declared base")
    order = ledger.get("commit_order")
    _require(isinstance(order, list) and all(isinstance(item, str) for item in order), "commit_order must be SHA strings")
    commits = classified_intents(ledger)
    _require(ahead == len(order), "ahead_by must match commit_order")
    _require(len(order) == len(set(order)), "commit_order contains duplicate SHAs")
    _require(set(order) == set(commits), "commit_order must equal classified fork commits")
    if ahead == 0 or (pin == assessed and not commits):
        raise CompatError("upstream tag adopted without a patch-preserving rebase")
    _require(order[-1] == pin, "commit_order must end at pinned_head")
    # Ledger behind_by is a claim only. Ancestry versus the declared base is
    # verified separately when history is available; missing history fails closed.


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError as exc:
        raise CompatError(f"git unavailable: {exc}") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
        raise CompatError(f"git {' '.join(args)} failed: {detail}")
    return result


def read_gitlink(source_root: Path) -> str:
    result = git(source_root, "ls-files", "--stage", "--", "core/mihomo")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise CompatError("core/mihomo gitlink is missing")
    match = GITLINK_RE.fullmatch(lines[0])
    _require(match is not None, "core/mihomo must remain a gitlink")
    assert match is not None
    return match.group(1)


def mihomo_repository(source_root: Path) -> Path:
    path = source_root / MIHOMO_RELATIVE
    if not path.exists():
        raise CompatError(
            "mihomo history unavailable: core/mihomo is not checked out; "
            "cannot verify declared base ancestry or the patch-id floor"
        )
    probe = git(path, "rev-parse", "--is-inside-work-tree", check=False)
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        raise CompatError(
            "mihomo history unavailable: core/mihomo is not a git repository; "
            "cannot verify declared base ancestry or the patch-id floor"
        )
    return path


def resolve_commit(repo: Path, rev: str, label: str) -> str:
    result = git(repo, "rev-parse", "-q", "--verify", f"{rev}^{{commit}}", check=False)
    if result.returncode != 0 or not SHA_RE.fullmatch(result.stdout.strip()):
        raise CompatError(f"{label} {rev} is not present in the mihomo repository")
    return result.stdout.strip()


def verify_declared_base_ancestry(repo: Path, ledger: dict[str, Any]) -> None:
    """Judge ahead/behind only against the declared base, never latest_stable_tag."""
    upstream = _mapping(ledger.get("upstream"), "upstream")
    fork = _mapping(ledger.get("fork"), "fork")
    stable = _tag(upstream.get("stable_tag"), "stable_tag")
    base = _sha(upstream.get("base"), "base")
    pin = _sha(fork.get("pinned_head"), "pinned_head")
    tagged = resolve_commit(repo, f"refs/tags/{stable}", f"declared stable_tag {stable}")
    _require(tagged == base, f"declared stable_tag {stable} does not point at the declared base")
    resolve_commit(repo, base, "declared base")
    resolve_commit(repo, pin, "pinned_head")
    ancestor = git(repo, "merge-base", "--is-ancestor", base, pin, check=False)
    behind = int(git(repo, "rev-list", "--count", f"{pin}..{base}").stdout.strip() or "0")
    if ancestor.returncode != 0 or behind != 0:
        raise CompatError(
            f"fork is behind its declared base {base} by {behind} commit(s)"
        )
    order = list(ledger["commit_order"])
    actual = git(repo, "rev-list", "--reverse", f"{base}..{pin}").stdout.split()
    _require(
        actual == order,
        "commit_order must equal git rev-list of the declared base..pin range",
    )
    ahead = len(actual)
    _require(ahead == fork["ahead_by"], "ahead_by must equal declared base..pin history")
    _require(fork.get("behind_by") == 0, "ledger behind_by does not match declared-base ancestry")
    # Presence of a newer assessed tag is evidence only. Do not require it,
    # and do not treat pin..latest_stable as behind.


def patch_id_for(repo: Path, sha: str) -> str | None:
    """Return the stable patch-id for a non-empty commit, else None (e.g. merges)."""
    diff = git(repo, "show", "--format=", "-p", sha)
    ident = subprocess.run(
        ["git", "patch-id", "--stable"],
        input=diff.stdout,
        capture_output=True,
        check=False,
        text=True,
        cwd=str(repo),
    )
    if ident.returncode != 0:
        detail = (ident.stderr or ident.stdout).strip() or f"exit {ident.returncode}"
        raise CompatError(f"git patch-id failed for {sha}: {detail}")
    line = ident.stdout.strip()
    if not line:
        return None
    digest = line.split()[0]
    _require(bool(SHA_RE.fullmatch(digest)), f"malformed patch-id for {sha}")
    return digest


def non_merge_patch_ids(repo: Path, base: str, pin: str) -> dict[str, str]:
    shas = git(repo, "rev-list", "--reverse", "--no-merges", f"{base}..{pin}").stdout.split()
    result: dict[str, str] = {}
    for sha in shas:
        digest = patch_id_for(repo, sha)
        if digest is None:
            continue
        result[sha] = digest
    return result


def load_floor(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except OSError as exc:
        raise CompatError(f"reviewed patch floor is unreadable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CompatError("reviewed patch floor is not valid JSON") from exc
    _require(isinstance(value, dict), "reviewed patch floor must be an object")
    _require(value.get("schema") == 1, "unsupported patch floor schema")
    ids = value.get("patch_ids")
    _require(isinstance(ids, list) and ids, "reviewed patch floor has no patch_ids")
    cleaned: list[str] = []
    for item in ids:
        digest = _sha(item, "floor patch_id")
        _require(digest not in cleaned, f"duplicate floor patch-id {digest}")
        cleaned.append(digest)
    value = dict(value)
    value["patch_ids"] = cleaned
    return value


def enforce_floor(computed: dict[str, str], floor: dict[str, Any]) -> None:
    have = set(computed.values())
    missing = [digest for digest in floor["patch_ids"] if digest not in have]
    if missing:
        raise CompatError(
            f"{len(missing)} reviewed floor patch-id(s) missing from declared base..pin"
        )


def validate_transition(current: dict[str, Any], previous: dict[str, Any]) -> None:
    """Optional ledger-to-ledger check. Intent text is not a retention proof."""
    inspect(current)
    inspect(previous)
    current_upstream = _mapping(current.get("upstream"), "upstream")
    previous_upstream = _mapping(previous.get("upstream"), "upstream")
    if current_upstream.get("stable_tag") != previous_upstream.get("stable_tag"):
        _require(
            current_upstream.get("base") != previous_upstream.get("base"),
            "stable_tag advanced without changing the rebase base",
        )
    # Intent strings are not sufficient to prove patch retention after a rebase.
    # They can still catch an explicit previous-ledger comparison when SHAs
    # were rewritten but the source claims the same work. The reviewed patch-id
    # floor is the authority that cannot be waived from the source ledger.


def check_source_root(
    source_root: Path,
    previous: dict[str, Any] | None = None,
    floor_path: Path | None = DEFAULT_FLOOR,
) -> None:
    ledger = load_ledger(source_root / LEDGER_RELATIVE)
    inspect(ledger)
    gitlink = read_gitlink(source_root)
    _require(
        gitlink == ledger["fork"]["pinned_head"],
        "client gitlink differs from governed pinned_head",
    )
    repo = mihomo_repository(source_root)
    verify_declared_base_ancestry(repo, ledger)
    computed = non_merge_patch_ids(
        repo,
        ledger["upstream"]["base"],
        ledger["fork"]["pinned_head"],
    )
    if floor_path is not None:
        enforce_floor(computed, load_floor(floor_path))
    if previous is not None:
        validate_transition(ledger, previous)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, help="YueLink checkout containing the ledger and gitlink")
    parser.add_argument("--ledger", type=Path, help="ledger JSON to inspect without a checkout")
    parser.add_argument("--gitlink", help="optional 40-character gitlink SHA when using --ledger")
    parser.add_argument(
        "--previous-ledger",
        type=Path,
        help="optional previous ledger; not a substitute for the reviewed patch-id floor",
    )
    parser.add_argument(
        "--floor",
        type=Path,
        default=None,
        help="reviewed patch-id floor (default: the shipped fixture when using --source-root)",
    )
    args = parser.parse_args(argv)
    if args.source_root is None and args.ledger is None:
        parser.print_usage(sys.stderr)
        return 2
    try:
        previous = load_ledger(args.previous_ledger) if args.previous_ledger else None
        if args.source_root is not None:
            floor = args.floor if args.floor is not None else DEFAULT_FLOOR
            check_source_root(args.source_root, previous, floor_path=floor)
        else:
            assert args.ledger is not None
            ledger = load_ledger(args.ledger)
            inspect(ledger)
            if args.gitlink:
                _require(
                    args.gitlink == ledger["fork"]["pinned_head"],
                    "client gitlink differs from governed pinned_head",
                )
            if previous is not None:
                validate_transition(ledger, previous)
            # --ledger without a mihomo history cannot enforce the patch-id floor
            # or declared-base ancestry; that is an incomplete admission, not a pass
            # over those gates. The CLI still reports structural inspection only.
            if args.floor is not None:
                raise CompatError(
                    "patch-id floor and declared-base ancestry require --source-root "
                    "with mihomo history; --ledger alone cannot admit a floor"
                )
    except CompatError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: mihomo ledger is internally consistent and patch-preserving")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
