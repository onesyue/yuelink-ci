"""Test inventory, reviewed plans, and directory grouping."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from flutter_coverage.settings import (
    GROUP_FILE_CAP,
    GROUP_WEIGHT_BUDGET,
    ISOLATED,
    MACOS_TEST,
    MINIMUM_FILES,
    REQUIRED_TESTS,
)


def partition(root: Path) -> list[list[str]]:
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "test/"], cwd=root
    ).decode().split("\0")
    tests = sorted(p for p in tracked if p.endswith("_test.dart"))
    actual = sorted(str(p.relative_to(root)) for p in (root / "test").rglob("*_test.dart"))
    if tests != actual or len(tests) < MINIMUM_FILES:
        raise ValueError("test inventory differs from the complete tracked corpus or scan floor")
    if not REQUIRED_TESTS.issubset(tests) or any((root / p).is_symlink() for p in tests):
        raise ValueError("required crash cases missing or test paths are symlinks")
    for path in tests:
        annotations = re.findall(r"@TestOn\s*\(([^)]*)\)", (root / path).read_text())
        if annotations and (path != MACOS_TEST or annotations != ["'mac-os'"]):
            raise ValueError("unassigned platform-specific test")
        if path == MACOS_TEST and annotations != ["'mac-os'"]:
            raise ValueError("macOS assignment no longer matches its annotation")
    early = REQUIRED_TESTS | {"test/governance/mihomo_fork_ledger_test.dart"}
    return [[p] for p in sorted(tests, key=lambda path: (path not in early, path))]


def declaration_weight(root: Path | None, path: str) -> int:
    """Estimate how long one file takes from its test declarations.

    No per-file duration exists before the group runs. A declaration is the
    unit: eight small files still share a group, and a file with many tests
    is not packed beside them. Missing declarations still cost one unit.
    Callers without a tree keep weight 1.
    """
    if root is None:
        return 1
    text = (root / path).read_text(encoding="utf-8", errors="replace")
    declared = len(re.findall(r"(?m)^[ \t]*(?:test|testWidgets)\(", text))
    return declared if declared else 1


def directory_groups(inventory: list[list[str]], root: Path | None = None) -> list[list[str]]:
    """Group a live tree by parent directory. Do not write a plan file.

    Within one directory, pack by estimated test weight and never exceed
    eight files. Isolated crash cases stay alone. Omitting root keeps every
    ordinary file at weight 1, which is the previous eight-file chunk.
    """
    buckets: dict[str, list[str]] = {}
    order: list[str] = []
    for (path,) in inventory:
        if path == MACOS_TEST:
            continue
        parent = str(Path(path).parent)
        if parent not in buckets:
            order.append(parent)
            buckets[parent] = []
        buckets[parent].append(path)
    groups: list[list[str]] = []
    for parent in order:
        ordinary: list[str] = []
        used = 0

        def flush() -> None:
            nonlocal ordinary, used
            if ordinary:
                groups.append(ordinary)
                ordinary = []
                used = 0

        for path in buckets[parent]:
            if path in ISOLATED:
                flush()
                groups.append([path])
                continue
            weight = declaration_weight(root, path)
            if ordinary and (len(ordinary) >= GROUP_FILE_CAP or used + weight > GROUP_WEIGHT_BUDGET):
                flush()
            ordinary.append(path)
            used += weight
            if len(ordinary) >= GROUP_FILE_CAP or used >= GROUP_WEIGHT_BUDGET:
                flush()
        flush()
    return groups


def parse_job_shard(value: str) -> tuple[int, int] | None:
    """Parse COVERAGE_JOB_SHARD as index/count. Empty means the whole corpus."""
    if value == "":
        return None
    parts = value.split("/")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ValueError("COVERAGE_JOB_SHARD must be index/count")
    index, count = int(parts[0]), int(parts[1])
    if count < 1 or index < 0 or index >= count:
        raise ValueError("COVERAGE_JOB_SHARD index is outside its count")
    return index, count


def select_shard(groups: list[list[str]], shard: tuple[int, int] | None) -> list[tuple[int, list[str]]]:
    """Keep original group indexes so a join can prove the corpus was covered once."""
    indexed = list(enumerate(groups))
    if shard is None:
        return indexed
    index, count = shard
    return [item for item in indexed if item[0] % count == index]


def assert_directory_groups(groups: list[list[str]], inventory: list[list[str]]) -> None:
    expected = [path for (path,) in inventory if path != MACOS_TEST]
    covered = [path for group in groups for path in group]
    if any(path in ISOLATED and group != [path] for group in groups for path in group):
        raise ValueError("isolated path inside a multi-file group")
    if (sorted(covered) != sorted(expected) or len(covered) != len(set(covered))
            or any(not group or len(group) > GROUP_FILE_CAP
                   or len({str(Path(path).parent) for path in group}) != 1 for group in groups)):
        raise ValueError("PLAN_INCOMPLETE")


def frozen_sdk(grouped: dict) -> dict:
    sdk = grouped.get("sdk")
    required = {"frameworkVersion", "frameworkRevision", "engineRevision", "dartSdkVersion"}
    if (not isinstance(sdk, dict) or set(sdk) != required
            or any(not isinstance(sdk[key], str) or not sdk[key] for key in required)):
        raise ValueError("frozen grouped plan is missing the reviewed Flutter SDK")
    return sdk


def load_grouped_plan(root: Path, inventory: list[list[str]], plan_path: Path,
                      isolated: set[str], macos_test: str) -> tuple[dict, list[list[str]]]:
    data = json.loads(plan_path.read_text())
    paths = [item[0] for item in inventory]
    expected = {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in paths}
    groups = data["groups"]
    linux = [path for path in paths if path != macos_test]
    if (data["schema"] != 1 or data["files"] != expected
            or sorted(data["isolated"]) != sorted(isolated)
            or data["macos_test"] != macos_test
            or not all(isinstance(group, list) and 1 <= len(group) <= GROUP_FILE_CAP for group in groups)
            or [path for group in groups for path in group] != linux
            or any(path in isolated and group != [path] for group in groups for path in group)
            or not isolated.issubset(expected)):
        raise ValueError("reviewed grouping inventory/blob/isolation plan mismatch")
    if set(data["runtime_files"]) != {"pubspec.lock"} or set(data["sdk"]) != {
            "frameworkVersion", "frameworkRevision", "engineRevision", "dartSdkVersion"}:
        raise ValueError("missing reviewed runtime/SDK binding")
    for path, expected_hash in data["runtime_files"].items():
        if path != "pubspec.lock":
            raise ValueError("unknown runtime binding")
        if hashlib.sha256((root / path).read_bytes()).hexdigest() != expected_hash:
            raise ValueError("reviewed grouping runtime lock mismatch")
    tree = subprocess.check_output(["git", "rev-parse", "HEAD:lib"], cwd=root, text=True).strip()
    if tree != data["lib_tree"]:
        raise ValueError("reviewed grouping runtime tree mismatch")
    return data, groups


def select_execution_plan(root: Path, inventory: list[list[str]], *, plan_path: Path,
                          grouped_plan_sha256: str, grouped_plan_fn, directory_groups_fn,
                          frozen_sdk_fn, assert_directory_groups_fn, macos_test: str):
    """Select only an explicitly admitted corpus, never retry a failed mode."""
    if hashlib.sha256(plan_path.read_bytes()).hexdigest() != grouped_plan_sha256:
        raise ValueError("reviewed grouping control digest mismatch")
    grouped = json.loads(plan_path.read_text())
    expected = {path: hashlib.sha256((root / path).read_bytes()).hexdigest()
                for (path,) in inventory}
    tree = subprocess.check_output(["git", "rev-parse", "HEAD:lib"], cwd=root, text=True).strip()
    lock = {"pubspec.lock": hashlib.sha256((root / "pubspec.lock").read_bytes()).hexdigest()}
    if (grouped["files"] == expected and grouped["lib_tree"] == tree
            and grouped["runtime_files"] == lock):
        data, groups = grouped_plan_fn(root, inventory)
        return data, groups, "reviewed_groups", plan_path

    sdk = frozen_sdk_fn({"sdk": grouped.get("live_sdk")})
    groups = directory_groups_fn(inventory, root)
    assert_directory_groups_fn(groups, inventory)
    data = {
        "schema": 1,
        "execution_mode": "directory_groups",
        "sdk": sdk,
        "macos_test": macos_test,
        "groups": groups,
    }
    return data, groups, "directory_groups", plan_path
