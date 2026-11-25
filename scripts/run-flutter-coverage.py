#!/usr/bin/env python3
"""Run the complete Flutter corpus with independent compiler/cache lifetimes."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parent / "flutter_coverage" / "runner.py"
    spec = importlib.util.spec_from_file_location("flutter_coverage.runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("flutter coverage runner is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    _module().main()


if __name__ == "__main__":
    main()

