#!/usr/bin/env python3
"""Exact-source, unsigned Runner/PacketTunnel verification. Never distribute products.

The isolated Flutter SDK deliberately omits four signing calls; it still runs
actual Flutter assembly, Swift compilation and linking. Signing attempts fail.
Raw subprocess output stays run-local; only allowlisted evidence is published.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

FLUTTER_COMMIT = "737ba8f885dc774b53b442759999a312c9461e4e"
POSITIVE_SOURCE = "0b5096da3ceeca50b2f0c5185147a62df078e7d0"
IOS_TARGET = "packages/flutter_tools/lib/src/build_system/targets/ios.dart"
NATIVE_TARGET = (
    "packages/flutter_tools/lib/src/isolated/native_assets/ios/native_assets.dart"
)
SDK_HASHES = {
    IOS_TARGET: "42c082aaa5b62e1b169a9d1511ba6e2a2f5f51e8703739835d65a13d0951238c",
    NATIVE_TARGET: "780e74aaed51823a08726ed3ec7f0ca89c64de22a670d2c34a2b1fc82d4a2ca9",
    "packages/flutter_tools/bin/xcode_backend.dart": "472073909ce8bde7f5844379c8949bc536a9889a7565a962608fd0da6bcda494",
    "packages/flutter_tools/bin/xcode_backend.sh": "9cff5480ae09878b112017e62c56cbbebb9c85bd56d1a3fd03160ebb07a1fc88",
}
PROJECT_HASHES = {
    "ios/Runner.xcodeproj/project.pbxproj": "ec1f83241c3fa5b2a632d6a849015e1d2733c4e96a1dfdb4f746b622c052c2be",
    "ios/Runner.xcodeproj/xcshareddata/xcschemes/Runner.xcscheme": "9fbce429f56243561dd437a527136c9f9ed77165e3b271b923faf236cfcd0484",
}
DELEGATE = "ios/Runner/AppDelegate.swift"
DELEGATE_HASH = "ffcfbc05e3f0e2f0e8d3f449a6ce7bc4d252fd4793be004c996e0323ccdeeaed"
ARGUMENT = "appProfile: AppGroupResolver.liveInputs().profile,\n            extensionProfile: embeddedPacketTunnelProfile()"
REPLACEMENT = "appProfile: AppGroupResolver.liveInputs().profile"
SIGNING_SETTINGS = [
    "CODE_SIGNING_ALLOWED=NO",
    "CODE_SIGNING_REQUIRED=NO",
    "CODE_SIGN_IDENTITY=",
    "EXPANDED_CODE_SIGN_IDENTITY=",
]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str) -> str:
    return (
        subprocess.check_output(
            ["git", "-C", str(root), *args], stderr=subprocess.DEVNULL
        )
        .decode()
        .strip()
    )


def exact_sha(value: str) -> str:
    if re.fullmatch("[0-9a-f]{40}", value) is None:
        raise ValueError("invalid exact commit")
    return value


def source_identity(root: Path, expected: str) -> dict:
    if git(root, "rev-parse", "HEAD") != exact_sha(expected):
        raise ValueError("source HEAD mismatch")
    repositories = [root]
    modules = git(root, "submodule", "status", "--recursive").splitlines()
    # Keep the original status prefix: strip() would discard the clean marker.
    raw = subprocess.check_output(
        ["git", "-C", str(root), "submodule", "status", "--recursive"]
    ).decode()
    for line in raw.splitlines():
        if not line.startswith(" ") or re.match(r" [0-9a-f]{40} ", line) is None:
            raise ValueError("uninitialized, changed or conflicted submodule")
        relative = line[42:].split(" (", 1)[0]
        repo = (root / relative).resolve()
        if not repo.is_relative_to(root.resolve()):
            raise ValueError("submodule escapes source checkout")
        repositories.append(repo)
    for repo in repositories:
        if git(repo, "status", "--porcelain", "--untracked-files=normal"):
            raise ValueError("source or submodule is dirty")
        credentials = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "config",
                "--local",
                "--name-only",
                "--get-regexp",
                r"^(core\.sshcommand|http\..*\.extraheader)$",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if credentials.returncode != 1:
            raise ValueError("checkout credentials remain configured")
    locks = {}
    for name in git(root, "ls-files").splitlines():
        if name.endswith((".lock", "Package.resolved")):
            locks[name] = sha((root / name).read_bytes())
    return {
        "source": expected,
        "tree": git(root, "rev-parse", "HEAD^{tree}"),
        "submodules": modules,
        "locks": locks,
    }


def validate_project(root: Path) -> None:
    for name, digest in PROJECT_HASHES.items():
        if sha((root / name).read_bytes()) != digest:
            raise ValueError("reviewed Xcode build phases changed")


def mutate_fixture(data: bytes) -> bytes:
    if sha(data) != DELEGATE_HASH or data.decode().count(ARGUMENT) != 1:
        raise ValueError("negative fixture source/match drift")
    return data.decode().replace(ARGUMENT, REPLACEMENT, 1).encode()


def validate_mode(root: Path, mode: str) -> None:
    data = (root / DELEGATE).read_bytes()
    if mode == "positive":
        if sha(data) != DELEGATE_HASH:
            raise ValueError("positive Swift source changed")
    else:
        if git(root, "show", "-s", "--format=%P", "HEAD") != POSITIVE_SOURCE:
            raise ValueError(
                "negative fixture must be a single child of reviewed source"
            )
        if git(root, "diff", "--name-only", POSITIVE_SOURCE, "HEAD") != DELEGATE:
            raise ValueError("negative fixture changes unrelated source")
        original = subprocess.check_output(
            ["git", "-C", str(root), "show", POSITIVE_SOURCE + ":" + DELEGATE]
        )
        if data != mutate_fixture(original):
            raise ValueError("negative fixture is not the reviewed missing argument")


def patch_sdk_sources(sdk: Path) -> dict:
    # Validate all originals before changing any file. No global SDK is passed here.
    for name, digest in SDK_HASHES.items():
        if sha((sdk / name).read_bytes()) != digest:
            raise ValueError("pinned Flutter source hash mismatch")
    replacements = {
        IOS_TARGET: [
            ("await _signFramework(environment, frameworkBinary, buildMode);", 2),
            ("await _signFramework(environment, outputFile, BuildMode.debug);", 1),
        ],
        NATIVE_TARGET: [
            ("await codesignDylib(codesignIdentity, buildMode, frameworkDir);", 1)
        ],
    }
    changed = {}
    for name, patterns in replacements.items():
        path = sdk / name
        text = path.read_text()
        for old, count in patterns:
            if text.count(old) != count:
                raise ValueError("Flutter signing call count changed")
            text = text.replace(
                old, "// Verification-only SDK: framework signing deliberately omitted."
            )
        path.write_text(text)
        changed[name] = {"before": SDK_HASHES[name], "after": sha(path.read_bytes())}
    return changed


def prepare_sdk(original: Path, isolated: Path) -> dict:
    if isolated.exists() or original.resolve() == isolated.resolve():
        raise ValueError("SDK destination must be fresh and isolated")
    if git(original, "rev-parse", "HEAD") != FLUTTER_COMMIT or git(
        original, "diff", "HEAD", "--"
    ):
        raise ValueError("Flutter SDK revision or tracked source drift")
    # Dereference links while copying; no writable source/snapshot link points back
    # into the shared SDK. Do not use hardlinks or mutate the setup action cache.
    if not (original / ".git").is_dir() or (original / ".git").is_symlink():
        raise ValueError("SDK git metadata must be self-contained")
    shutil.copytree(original, isolated, symlinks=False)
    if (
        Path(git(isolated, "rev-parse", "--show-toplevel")).resolve()
        != isolated.resolve()
    ):
        raise ValueError("isolated SDK git metadata escapes its copy")
    changed = patch_sdk_sources(isolated)
    for name in ("flutter_tools.snapshot", "flutter_tools.stamp"):
        target = isolated / "bin/cache" / name
        if not target.resolve().is_relative_to(isolated.resolve()):
            raise ValueError("SDK cache escapes isolated copy")
        target.unlink(missing_ok=True)
    return {
        "revision": FLUTTER_COMMIT,
        "adaptation": changed,
        "purpose": "unsigned compile verification; not production build equivalence",
    }


def install_signing_guard(directory: Path, violation: Path) -> None:
    directory.mkdir()
    guard = directory / "codesign"
    guard.write_text(
        "#!/bin/sh\n"
        'if [ "$#" = 3 ] && [ "$1" = --verify ] && [ "$2" = --strict ]; then\n'
        '  exec /usr/bin/codesign "$@"\nfi\n'
        'printf "signing attempt rejected\\n" >> "$IOS_SIGNING_VIOLATION"\nexit 97\n'
    )
    guard.chmod(0o755)


def run(command: list[str], cwd: Path, env: dict, log: Path, timeout: int = 900) -> int:
    # Do not stream raw tool output: Swift snippets, settings and environment can
    # contain identifiers. The public receipt below is an allowlist, not a dump.
    with log.open("wb") as stream:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            return process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise


def require_run(command: list[str], cwd: Path, env: dict, log: Path) -> None:
    if run(command, cwd, env, log) != 0:
        raise ValueError("preparation command failed: " + log.name)


def protected_command(command: list[str], profile: Path) -> list[str]:
    return ["/usr/bin/sandbox-exec", "-f", str(profile), *command]


def compilation_outcome(
    code: int, evidence: dict, mode: str, products: bool
) -> tuple[str, int]:
    if evidence["signing_operation"]:
        return "signing_operation_rejected", 1
    if mode == "negative":
        status = (
            "expected_swift_failure"
            if code != 0 and evidence["expected_negative_diagnostic"]
            else "negative_control_not_proven"
        )
        return status, code if code != 0 else 1
    if (
        code == 0
        and evidence["compiled_targets"] == ["Runner", "PacketTunnel"]
        and products
    ):
        return "passed_unsigned_compilation", 0
    return "compilation_failed_or_targets_missing", code if code != 0 else 1


def compile_command(derived: Path, result: Path) -> list[str]:
    return [
        "xcodebuild",
        "-workspace",
        "ios/Runner.xcworkspace",
        "-scheme",
        "Runner",
        "-configuration",
        "Release",
        "-sdk",
        "iphoneos",
        "-destination",
        "generic/platform=iOS",
        "-derivedDataPath",
        str(derived),
        "-resultBundlePath",
        str(result),
        *SIGNING_SETTINGS,
        "build",
    ]


def signing_disabled(settings: str, target: str) -> dict:
    rows = json.loads(settings)
    selected = [r["buildSettings"] for r in rows if r.get("target") == target]
    if len(selected) != 1:
        raise ValueError("missing resolved target settings")
    values = {
        k: selected[0].get(k, "")
        for k in (
            "CODE_SIGNING_ALLOWED",
            "CODE_SIGNING_REQUIRED",
            "CODE_SIGN_IDENTITY",
            "EXPANDED_CODE_SIGN_IDENTITY",
        )
    }
    if values != {
        "CODE_SIGNING_ALLOWED": "NO",
        "CODE_SIGNING_REQUIRED": "NO",
        "CODE_SIGN_IDENTITY": "",
        "EXPANDED_CODE_SIGN_IDENTITY": "",
    }:
        raise ValueError("target signing is not disabled")
    return values


def compile_evidence(text: str) -> dict:
    targets = [
        target
        for target in ("Runner", "PacketTunnel")
        if any(
            re.match(r"\s*(SwiftCompile|CompileSwift|SwiftEmitModule)\b", line)
            and f"in target '{target}' from project 'Runner'" in line
            for line in text.splitlines()
        )
    ]
    signing = bool(
        re.search(
            r"^\s*(?:CodeSign\s|(?:/usr/bin/)?codesign\s+.*(?:--sign|--force))",
            text,
            re.MULTILINE,
        )
    )
    missing = re.search(
        r"AppDelegate\.swift:\d+:\d+: error: missing argument for parameter 'extensionProfile' in call",
        text,
    )
    return {
        "compiled_targets": targets,
        "signing_operation": signing,
        "expected_negative_diagnostic": missing is not None,
        "diagnostic": missing.group(0) if missing else None,
    }


def verify(args: argparse.Namespace) -> int:
    root = args.checkout.resolve()
    builder = Path(__file__).resolve().parents[1]
    expected_builder = exact_sha(args.expected_builder)
    if git(builder, "rev-parse", "HEAD") != expected_builder or git(
        builder, "status", "--porcelain"
    ):
        raise ValueError("public verifier source is not the exact clean builder")
    temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    output = args.receipt_dir.resolve()
    if not output.is_relative_to(temp) or output.exists():
        raise ValueError("receipt directory must be fresh beneath RUNNER_TEMP")
    output.mkdir()
    private = temp / ("ios-raw-" + args.expected_source + "-" + args.mode)
    private.mkdir()
    receipt = {
        "schema": 1,
        "status": "failed",
        "source": args.expected_source,
        "builder": expected_builder,
        "mode": args.mode,
        "sdk": {},
        "evidence": {},
    }
    result = 1
    try:
        before = source_identity(root, args.expected_source)
        validate_project(root)
        validate_mode(root, args.mode)
        receipt["identity"] = before
        env = dict(
            os.environ,
            DEVELOPER_DIR="/Applications/Xcode_26.6.app/Contents/Developer",
            GOTOOLCHAIN="local",
            GOMAXPROCS="2",
            GOMEMLIMIT="1800MiB",
            GOFLAGS="-mod=readonly",
            **dict(x.split("=", 1) for x in SIGNING_SETTINGS),
        )
        require_run(["xcodebuild", "-version"], root, env, private / "xcode-version")
        if (private / "xcode-version").read_text().splitlines()[0] != "Xcode 26.6":
            raise ValueError("pinned Xcode unavailable")
        profile = private / "no-sign.sb"
        profile.write_text(
            '(version 1)\n(allow default)\n(deny process-exec (regex #".*/(codesign|ldid)$"))\n'
        )
        require_run(
            protected_command(["/usr/bin/true"], profile),
            root,
            env,
            private / "sandbox-check",
        )
        receipt["signing_sandbox"] = (
            "deny codesign/ldid execution; read-only media signature verification runs separately"
        )
        original = args.flutter_sdk.resolve()
        isolated = private / "flutter-3.47.8-no-sign"
        receipt["sdk"] = prepare_sdk(original, isolated)
        violation = private / "signing-violation"
        install_signing_guard(private / "guard-bin", violation)
        env.update(
            PATH=str(private / "guard-bin")
            + os.pathsep
            + str(isolated / "bin")
            + os.pathsep
            + env["PATH"],
            FLUTTER_ROOT=str(isolated),
            IOS_SIGNING_VIOLATION=str(violation),
        )
        require_run(
            protected_command(["flutter", "--version"], profile),
            root,
            env,
            private / "flutter-bootstrap",
        )
        require_run(
            protected_command(["flutter", "--version", "--machine"], profile),
            root,
            env,
            private / "flutter-version",
        )
        info = json.loads((private / "flutter-version").read_text())
        if (
            info.get("frameworkVersion") != "3.47.8"
            or info.get("frameworkRevision") != FLUTTER_COMMIT
        ):
            raise ValueError("Flutter runtime identity mismatch")
        snapshot = isolated / "bin/cache/flutter_tools.snapshot"
        receipt["sdk"]["snapshot_sha256"] = sha(snapshot.read_bytes())
        receipt["sdk"]["snapshot_rebuilt_after_patch"] = True
        for name, values in receipt["sdk"]["adaptation"].items():
            if sha((isolated / name).read_bytes()) != values["after"]:
                raise ValueError("SDK adaptation changed during bootstrap")
        commands = [
            (["go", "version"], "go-version"),
            (["flutter", "config", "--enable-swift-package-manager"], "spm"),
            (["flutter", "pub", "get", "--enforce-lockfile"], "pub"),
            (["python3", "scripts/apple_media/verify.py", "--mach-o"], "media"),
            (["dart", "setup.dart", "build", "-p", "ios"], "ffi-build"),
            (["dart", "setup.dart", "install", "-p", "ios"], "ffi-copy"),
            (
                [
                    "flutter",
                    "build",
                    "ios",
                    "--release",
                    "--no-codesign",
                    "--config-only",
                    "--no-pub",
                ],
                "config",
            ),
        ]
        for command, label in commands:
            receipt["stage"] = label
            guarded = (
                command if label == "media" else protected_command(command, profile)
            )
            require_run(guarded, root, env, private / label)
        if "go version go1.27.4 " not in (private / "go-version").read_text():
            raise ValueError("Go runtime version mismatch")
        receipt["ffi"] = {
            name: sha((root / name).read_bytes())
            for name in ("ios/Frameworks/libclash.a", "ios/Frameworks/libclash.h")
        }
        receipt["settings"] = {}
        for target in ("Runner", "PacketTunnel"):
            command = [
                "xcodebuild",
                "-project",
                "ios/Runner.xcodeproj",
                "-target",
                target,
                "-configuration",
                "Release",
                "-sdk",
                "iphoneos",
                *SIGNING_SETTINGS,
                "-showBuildSettings",
                "-json",
            ]
            require_run(command, root, env, private / ("settings-" + target))
            receipt["settings"][target] = signing_disabled(
                (private / ("settings-" + target)).read_text(), target
            )
        if source_identity(root, args.expected_source) != before:
            raise ValueError("source inputs changed during preparation")
        derived = private / "DerivedData"
        receipt["stage"] = "actual-xcodebuild"
        code = run(
            protected_command(
                compile_command(derived, private / "compile.xcresult"), profile
            ),
            root,
            env,
            private / "compile",
            timeout=1800,
        )
        evidence = compile_evidence((private / "compile").read_text(errors="replace"))
        receipt["evidence"] = evidence
        receipt["compiler_exit"] = code
        if violation.exists() or evidence["signing_operation"]:
            raise ValueError("signing operation rejected")
        if source_identity(root, args.expected_source) != before:
            raise ValueError("source inputs changed during compilation")
        app = derived / "Build/Products/Release-iphoneos/Runner.app"
        products = (app / "Runner").is_file() and (
            app / "PlugIns/PacketTunnel.appex/PacketTunnel"
        ).is_file()
        receipt["status"], result = compilation_outcome(
            code, evidence, args.mode, products
        )
    except (ValueError, OSError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        receipt["failure_class"] = type(exc).__name__
        # Exception strings and raw subprocess output are deliberately not public.
    finally:
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print("iOS verification: " + receipt["status"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--expected-source", required=True, type=exact_sha)
    parser.add_argument("--expected-builder", required=True, type=exact_sha)
    parser.add_argument("--flutter-sdk", type=Path, required=True)
    parser.add_argument("--mode", choices=("positive", "negative"), default="positive")
    parser.add_argument("--receipt-dir", type=Path, required=True)

    def cancelled(_signal, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, cancelled)
    return verify(parser.parse_args())


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError):
        sys.exit("iOS verification preflight rejected")
