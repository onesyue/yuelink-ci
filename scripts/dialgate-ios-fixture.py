#!/usr/bin/env python3
"""Prepare one missing-argument fixture in an isolated checkout; never commit/push."""

import argparse
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "ios_compile", Path(__file__).with_name("dialgate-ios-compile.py")
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def prepare(checkout: Path, receipt: Path) -> None:
    module.source_identity(checkout, module.POSITIVE_SOURCE)
    if receipt.exists() or receipt.resolve().is_relative_to(checkout.resolve()):
        raise ValueError("fixture receipt must be fresh and outside source")
    branch = module.git(checkout, "branch", "--show-current")
    if not branch.startswith("grok/ios-negative-"):
        raise ValueError("use a dedicated grok/ios-negative- fixture branch")
    path = checkout / module.DELEGATE
    original = path.read_bytes()
    changed = module.mutate_fixture(original)
    path.write_bytes(changed)
    receipt.write_text(
        json.dumps(
            {
                "kind": "uncommitted_negative_fixture",
                "base": module.POSITIVE_SOURCE,
                "path": module.DELEGATE,
                "before": hashlib.sha256(original).hexdigest(),
                "after": hashlib.sha256(changed).hexdigest(),
                "next": "review and sign this single-file child; bind its own PR/head before verification",
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(args.checkout.resolve(), args.receipt.resolve())
    except (ValueError, OSError, subprocess.SubprocessError):
        raise SystemExit("negative fixture preparation rejected")
