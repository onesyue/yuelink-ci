#!/usr/bin/env python3
"""Join coverage shard manifests and enforce the corpus floor once."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifests_from(directory: Path) -> list[dict]:
    found = sorted(path for path in directory.glob("*/shard.json") if path.is_file())
    if not found:
        raise SystemExit("coverage join has no shard manifest")
    loaded: list[dict] = []
    for path in found:
        item = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(item, dict):
            raise SystemExit(f"shard manifest is not an object: {path.name}")
        loaded.append(item)
    return loaded


def planned_observation(manifests: list[dict], groups: list[list[str]]) -> int:
    """Refuse a partial join. A missing shard is not a green corpus."""
    counts = {item.get("count") for item in manifests}
    if len(counts) != 1:
        raise SystemExit("coverage shards disagree on the shard count")
    count = next(iter(counts))
    if not isinstance(count, int) or count < 1:
        raise SystemExit("coverage shard count is not a positive integer")
    shards = [item.get("shard") for item in manifests]
    if sorted(shards) != list(range(count)):
        raise SystemExit("coverage join is missing a shard")
    indexes: list[int] = []
    files: list[str] = []
    observed = 0
    for item in manifests:
        item_indexes = item.get("indexes")
        item_files = item.get("files")
        item_observed = item.get("observed")
        if not isinstance(item_indexes, list) or not isinstance(item_files, list):
            raise SystemExit("coverage shard manifest is missing indexes or files")
        if not isinstance(item_observed, int) or item_observed < 0:
            raise SystemExit("coverage shard observed count is not a whole number")
        indexes.extend(item_indexes)
        files.extend(item_files)
        observed += item_observed
    expected = [path for group in groups for path in group]
    if sorted(indexes) != list(range(len(groups))):
        raise SystemExit("coverage shard indexes are not the planned groups")
    if sorted(files) != sorted(expected) or len(files) != len(set(files)):
        raise SystemExit("coverage shard files are not the planned corpus")
    return observed


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: stitch-coverage-shards.py SHARD_DIR")
    directory = Path(sys.argv[1])
    here = Path(__file__).resolve().parent
    runner = load_module(here / "run-flutter-coverage.py", "coverage_runner")
    root = Path.cwd()
    inventory = runner.partition(root)
    _, groups, _, _ = runner.execution_plan(root, inventory)
    observed = planned_observation(manifests_from(directory), groups)
    counter = load_module(here / "count-flutter-machine-tests.py", "machine_counter")
    counter.enforce_floor(observed, runner.MINIMUM_TESTS)
    traces = [path for path in sorted(directory.glob("*/lcov.info")) if path.stat().st_size]
    if not traces:
        raise SystemExit("coverage join collected no application lines")
    arguments: list[str] = []
    for path in traces:
        arguments.extend(["--add-tracefile", str(path)])
    output = root / "coverage"
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["lcov", *arguments, "--output-file", str(output / "lcov.info")],
        check=True,
    )
    print(f"stitched {len(list(directory.glob('*/shard.json')))} shards; tests observed: {observed}")


if __name__ == "__main__":
    main()
