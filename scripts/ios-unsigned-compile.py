#!/usr/bin/env python3
"""Compile Runner and PacketTunnel for one booted-capable iOS simulator.

The destination comes from `xcrun simctl list -j` on the runner. A device
SDK destination is not selected. The receipt records only the outcome.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


TARGETS = ("Runner", "PacketTunnel")
_SWIFT_LINE = re.compile(
    r"SwiftCompile\b.*(?:/|\\)(?P<target>Runner|PacketTunnel)(?:/|\\)"
)

# The candidate Release configuration sets SUPPORTED_PLATFORMS to iphoneos,
# and Flutter only supports Debug on the simulator. PacketTunnel links
# ios/Frameworks/libclash.a, so that archive has to be a simulator build.
CONFIGURATION = "Debug"
PRODUCT_DIRECTORY = "Debug-iphonesimulator"
_SIMULATOR_PLATFORM = re.compile(r"platform (?:7|IOSSIMULATOR)\b")


def simulator_destination(payload: dict) -> str:
    """Return `platform=iOS Simulator,id=<udid>` for an available iPhone."""
    devices = payload.get("devices") if isinstance(payload, dict) else None
    if not isinstance(devices, dict) or not devices:
        raise ValueError("simctl")
    for entries in devices.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("isAvailable") is not True:
                continue
            name = entry.get("name")
            udid = entry.get("udid")
            if (
                isinstance(name, str)
                and name.startswith("iPhone")
                and isinstance(udid, str)
                and udid
            ):
                return f"platform=iOS Simulator,id={udid}"
    raise ValueError("simctl")


def compile_argv(destination: str, derived: Path) -> list[str]:
    if not destination.startswith("platform=iOS Simulator,id="):
        raise ValueError("destination")
    return [
        "xcodebuild",
        "-workspace",
        "ios/Runner.xcworkspace",
        "-scheme",
        "Runner",
        "-configuration",
        CONFIGURATION,
        "-sdk",
        "iphonesimulator",
        "-destination",
        destination,
        "-derivedDataPath",
        str(derived),
        "CODE_SIGNING_ALLOWED=NO",
        "CODE_SIGNING_REQUIRED=NO",
        "CODE_SIGN_IDENTITY=",
        "build",
    ]


def swift_compile_issues(log: str) -> list[str]:
    seen = {match.group("target") for match in _SWIFT_LINE.finditer(log)}
    issues = [
        f"missing SwiftCompile evidence for {target}"
        for target in TARGETS
        if target not in seen
    ]
    if "** BUILD SUCCEEDED **" not in log:
        issues.append("build did not succeed")
    return issues


def product_issues(derived: Path) -> list[str]:
    product = derived / "Build" / "Products" / PRODUCT_DIRECTORY / "Runner.app"
    if not product.is_dir():
        return ["Runner.app was not produced"]
    return []


def simulator_archive_issues(text: str) -> list[str]:
    """Reject an archive that is not marked as an iOS simulator binary."""
    if _SIMULATOR_PLATFORM.search(text):
        return []
    return ["libclash.a is not an iOS simulator archive"]


def _emit_failure(text: str) -> None:
    lines = [line[:400] for line in text.splitlines() if line.strip()]
    matched = [line for line in lines if "error:" in line.lower()]
    tail = matched[-20:] or lines[-20:]
    if tail:
        print("\n".join(tail), file=sys.stderr)


def _capture(argv: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def build_simulator_kernel(source: Path, log) -> None:
    """Write a simulator c-archive over the device library PacketTunnel links."""
    sdk = _capture(["xcrun", "--sdk", "iphonesimulator", "--show-sdk-path"], source, 60)
    cc = _capture(["xcrun", "--sdk", "iphonesimulator", "--find", "clang"], source, 60)
    log.write(sdk.stdout + sdk.stderr + cc.stdout + cc.stderr)
    if sdk.returncode != 0 or cc.returncode != 0 or not sdk.stdout.strip() or not cc.stdout.strip():
        raise ValueError("kernel")
    flags = (
        "-target arm64-apple-ios15.5-simulator -isysroot "
        f"{sdk.stdout.strip()} -mios-simulator-version-min=15.5"
    )
    env = os.environ.copy()
    env["GOOS"] = "ios"
    env["GOARCH"] = "arm64"
    env["CGO_ENABLED"] = "1"
    env["CC"] = cc.stdout.strip()
    env["CGO_CFLAGS"] = flags
    env["CGO_LDFLAGS"] = flags
    env["GOTOOLCHAIN"] = env.get("GOTOOLCHAIN") or "local"
    if "-mod=" not in env.get("GOFLAGS", ""):
        env["GOFLAGS"] = (env.get("GOFLAGS", "") + " -mod=readonly").strip()
    archive = source / "ios" / "Frameworks" / "libclash.a"
    archive.parent.mkdir(parents=True, exist_ok=True)
    code = subprocess.run(
        [
            "go",
            "build",
            "-buildmode=c-archive",
            "-trimpath",
            "-ldflags=-s -w -X github.com/metacubex/mihomo/constant.Version=yuelink",
            "-tags",
            "with_gvisor",
            "-o",
            str(archive),
            ".",
        ],
        cwd=source / "core",
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
        timeout=2400,
        check=False,
    ).returncode
    if code != 0:
        raise ValueError("kernel")
    probe = _capture(["xcrun", "otool", "-l", str(archive)], source, 120)
    log.write(probe.stdout + probe.stderr)
    if probe.returncode != 0 or simulator_archive_issues(probe.stdout):
        raise ValueError("kernel")


def _exact_sha(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value or ""):
        raise ValueError("sha")
    return value


def _head(checkout: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    head = proc.stdout.strip()
    if proc.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("identity")
    return head


def verify(args: argparse.Namespace) -> int:
    source_sha = _exact_sha(args.expected_source)
    builder_sha = _exact_sha(args.expected_builder)
    source = args.checkout.resolve()
    builder = Path(__file__).resolve().parents[1]
    temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    output = args.receipt.resolve()
    if not output.is_relative_to(temp) or output.exists():
        raise ValueError("receipt location")
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema": 1,
        "status": "failed",
        "source": source_sha,
        "builder": builder_sha,
        "sdk": "iphonesimulator",
        "stage": "identity",
    }
    result = 1
    log_path: Path | None = None
    try:
        if _head(source) != source_sha or _head(builder) != builder_sha:
            raise ValueError("identity")
        receipt["stage"] = "simctl"
        listing = subprocess.run(
            ["xcrun", "simctl", "list", "-j"],
            cwd=source,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if listing.returncode != 0:
            raise ValueError("simctl")
        destination = simulator_destination(json.loads(listing.stdout))
        derived = source / "derived"
        log_path = temp / "ios-unsigned-compile.log"
        with log_path.open("w", encoding="utf-8") as log:
            receipt["stage"] = "kernel"
            build_simulator_kernel(source, log)
            receipt["stage"] = "compile"
            code = subprocess.run(
                compile_argv(destination, derived),
                cwd=source,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=2400,
                check=False,
            ).returncode
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        issues = swift_compile_issues(log_text) + product_issues(derived)
        if code != 0 or issues:
            raise ValueError("compile")
        receipt["status"] = "passed"
        receipt["stage"] = "passed"
        result = 0
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired, json.JSONDecodeError):
        receipt["outcome"] = "verification_failed"
        if log_path is not None and log_path.exists():
            _emit_failure(log_path.read_text(encoding="utf-8", errors="replace"))
    finally:
        output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--expected-builder", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    try:
        return verify(parser.parse_args())
    except (OSError, ValueError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
