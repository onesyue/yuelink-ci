#!/usr/bin/env python3
"""Derive the release build's cache identities from build-matrix.yml itself.

Release builds run on a fresh tag ref, and GitHub scopes caches by ref, so a
cache a tag run saves is unreachable from the next tag. Release builds
therefore only restore; the master-scoped warm-up workflow saves. Nothing here
hand-copies a key: the warm-up reads the platform matrix from plan.yml, and the
toolchain pins, the Go core identity script and every cache path/key/
restore-key from build-matrix.yml at the same commit, then evaluates those
expressions for the current runner. An expression it does not understand is
an error, never a guess, so a drifted build workflow fails the warm-up loudly
instead of silently warming a key the build never reads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_MATRIX = ROOT / ".github/workflows/build-matrix.yml"
PLAN = ROOT / ".github/workflows/plan.yml"

# Logical cache -> exact step-name prefix in build-matrix.yml. Each runner
# selects the one variant whose `if:` holds for it.
CACHES = {
    "gocore": "Restore built Go core",
    "gomod": "Restore Go module cache",
    "gocache": "Restore Go compiler cache",
    "pub": "Restore pub cache (lockfile-verified",
}
IDENTITY_STEP = "Record Go core cache identity"
SOURCE_TITLE = re.compile(r"Source attestation ([0-9a-f]{40})")
EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")


class PlanError(RuntimeError):
    pass


def steps(text: str) -> list[tuple[str, str]]:
    """(name, block) for every `      - name:` step, in order."""
    marker = re.compile(r"(?m)^      - name: (.+)$")
    found = list(marker.finditer(text))
    out = []
    for i, match in enumerate(found):
        end = found[i + 1].start() if i + 1 < len(found) else len(text)
        block = text[match.start():end]
        nxt = re.search(r"(?m)^      - (?!name:)", block[1:])
        if nxt:
            block = block[: nxt.start() + 1]
        out.append((match.group(1).strip(), block))
    return out


def field(block: str, name: str) -> str | None:
    """A scalar or `|` block field of a step's `with:` (or the step itself)."""
    lines = block.splitlines()
    for i, line in enumerate(lines):
        match = re.match(rf"^(\s+){re.escape(name)}:\s?(.*)$", line)
        if not match:
            continue
        indent, value = len(match.group(1)), match.group(2).strip()
        if value != "|":
            return value
        body = []
        for nxt in lines[i + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            if nxt.strip():
                body.append(nxt.strip())
        return "\n".join(body)
    return None


def hash_files(path: Path) -> str:
    """GitHub hashFiles() for one file: sha256 over the file's sha256 digest."""
    if not path.is_file():
        return ""
    outer = hashlib.sha256()
    outer.update(hashlib.sha256(path.read_bytes()).digest())
    return outer.hexdigest()


def evaluate(text: str, ctx: dict[str, str], workspace: Path) -> str:
    def one(match: re.Match[str]) -> str:
        expr = match.group(1)
        if expr in ctx:
            return ctx[expr]
        hashed = re.fullmatch(r"hashFiles\('([^'*?]+)'\)", expr)
        if hashed:
            return hash_files(workspace / hashed.group(1))
        ternary = re.fullmatch(r"runner\.os == '(\w+)' && '([^']*)' \|\| '([^']*)'", expr)
        if ternary:
            return ternary.group(2) if ctx["runner.os"] == ternary.group(1) else ternary.group(3)
        raise PlanError(f"unsupported expression in build-matrix.yml: ${{{{ {expr} }}}}")
    return EXPR.sub(one, text)


def condition(expr: str | None, ctx: dict[str, str]) -> bool:
    if expr is None:
        return True
    match = re.fullmatch(r"runner\.os (==|!=) '(\w+)'", expr.strip())
    if not match:
        raise PlanError(f"unsupported cache step condition: {expr}")
    equal = ctx["runner.os"] == match.group(2)
    return equal if match.group(1) == "==" else not equal


def env_pin(text: str, name: str) -> str:
    values = set(re.findall(rf"(?m)^\s*{name}:\s*'([^']+)'\s*$", text))
    if len(values) != 1:
        raise PlanError(f"{name} must have exactly one pinned value, found {sorted(values)}")
    return values.pop()


def one_value(pattern: str, text: str, what: str) -> str:
    values = set(re.findall(pattern, text))
    if len(values) != 1:
        raise PlanError(f"{what} must have exactly one value, found {sorted(values)}")
    return values.pop()


def toolchain(text: str) -> dict[str, str]:
    return {
        "go_version": env_pin(text, "GO_VERSION"),
        "flutter_version": env_pin(text, "FLUTTER_VERSION"),
        "ndk": one_value(r'sdkmanager "ndk;([0-9.]+)"', text, "android ndk"),
        "xcode": one_value(r"test \"\$version\" = 'Xcode ([0-9.]+)'", text, "xcode"),
    }


def platform_matrix(plan_text: str) -> list[dict[str, str]]:
    literal = one_value(r"(?m)^\s*ALL='(\[.*\])'\s*$", plan_text, "plan ALL matrix")
    rows = json.loads(literal)
    if not rows or any(set(row) != {"platform", "os"} for row in rows):
        raise PlanError("plan matrix rows must be exactly {platform, os}")
    return rows


def source_sha(title: str, explicit: str) -> str:
    if explicit:
        if not re.fullmatch(r"[0-9a-f]{40}", explicit):
            raise PlanError("source_sha must be 40 lowercase hex")
        return explicit
    match = SOURCE_TITLE.fullmatch(title.strip())
    if not match:
        raise PlanError(f"not a source attestation run title: {title!r}")
    return match.group(1)


def identity_script(text: str) -> str:
    named = [block for name, block in steps(text) if name == IDENTITY_STEP]
    if len(named) != 1:
        raise PlanError(f"{IDENTITY_STEP} appears {len(named)} times")
    body = named[0].split("run: |\n", 1)
    if len(body) != 2:
        raise PlanError(f"{IDENTITY_STEP} has no run block")
    lines = []
    for line in body[1].splitlines():
        if line.startswith("          "):
            lines.append(line[10:])
        elif not line.strip():
            lines.append("")
        else:
            break
    return "\n".join(lines).strip() + "\n"


def recorded_identity(environ: dict[str, str]) -> dict[str, str]:
    """CORE_* exactly as the build's identity script recorded them."""
    values = {name: environ.get(name, "") for name in ("CORE_SHA", "CORE_ARCH_SCOPE", "CORE_INPUTS_SHA")}
    if not re.fullmatch(r"[0-9a-f]{40}", values["CORE_SHA"]) \
            or not re.fullmatch(r"[0-9a-f]{40}", values["CORE_INPUTS_SHA"]) \
            or values["CORE_ARCH_SCOPE"] not in ("amd64", "all"):
        raise PlanError("run the build-matrix identity script first (CORE_* missing or malformed)")
    return values


def cache_plan(text: str, ctx: dict[str, str], workspace: Path) -> dict[str, dict[str, str]]:
    plan = {}
    all_steps = steps(text)
    for logical, prefix in CACHES.items():
        chosen = [block for name, block in all_steps
                  if name.startswith(prefix) and condition(field(block, "if"), ctx)]
        if len(chosen) != 1:
            raise PlanError(f"{prefix!r}: expected one step for {ctx['runner.os']}, found {len(chosen)}")
        block = chosen[0]
        if not re.search(r"uses: actions/cache(/restore)?@[0-9a-f]{40}", block):
            raise PlanError(f"{prefix!r} is not an actions/cache step")
        entry = {}
        for name in ("path", "key", "restore-keys"):
            value = field(block, name)
            if value is not None:
                entry[name] = evaluate(value, ctx, workspace)
        if not entry.get("path") or not entry.get("key"):
            raise PlanError(f"{prefix!r} lost its path or key")
        if "${{" in "".join(entry.values()):
            raise PlanError(f"{prefix!r} still has an unevaluated expression")
        plan[logical] = entry
    return plan


def write_outputs(values: dict[str, str]) -> None:
    lines = []
    for name, value in values.items():
        delimiter = "EOF_" + hashlib.sha256(value.encode()).hexdigest()[:16]
        lines.append(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as out:
            out.writelines(lines)
    else:
        sys.stdout.writelines(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan", help="source sha, platform matrix and toolchain pins")
    p.add_argument("--title", default="")
    p.add_argument("--source-sha", default="")
    sub.add_parser("identity-script", help="print build-matrix.yml's Go core identity script")
    k = sub.add_parser("keys", help="evaluate build-matrix cache paths/keys for this runner")
    k.add_argument("--platform", required=True)
    k.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    text = BUILD_MATRIX.read_text(encoding="utf-8")
    try:
        if args.command == "plan":
            values = {"source_sha": source_sha(args.title, args.source_sha),
                      "matrix": json.dumps(platform_matrix(PLAN.read_text(encoding="utf-8")),
                                           separators=(",", ":"))}
            values.update(toolchain(text))
            write_outputs(values)
            return 0
        if args.command == "identity-script":
            sys.stdout.write(identity_script(text))
            return 0
        workspace = args.workspace.resolve()
        identity = recorded_identity(dict(os.environ))
        ctx = {
            "matrix.platform": args.platform,
            "runner.os": os.environ["RUNNER_OS"],
            "runner.arch": os.environ["RUNNER_ARCH"],
            "github.sha": os.environ["GITHUB_SHA"],
            "env.GO_VERSION": env_pin(text, "GO_VERSION"),
            **{f"env.{name}": value for name, value in identity.items()},
        }
        plan = cache_plan(text, ctx, workspace)
        values = dict(identity)
        for logical, entry in plan.items():
            for name, value in entry.items():
                values[f"{logical}_{name.replace('-', '_')}"] = value
        write_outputs(values)
        return 0
    except PlanError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
