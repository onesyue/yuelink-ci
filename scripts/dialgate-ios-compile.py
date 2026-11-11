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

# Explicit verification-only adaptation for the reviewed source/artifact set.
# These skip package plugin and signature validation, not just prompts. Pinned
# Flutter's actual build passes skipPackageValidation=false; these are not its
# default build flags. They neither sign nor relax the codesign/ldid sandbox.
PACKAGE_PLUGIN_ARGUMENTS = [
    "-skipPackagePluginValidation",
    "-skipPackageSignatureValidation",
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


PREPARATION_STAGES = frozenset(
    {
        "xcode-version",
        "sandbox-check",
        "flutter-bootstrap",
        "flutter-version",
        "go-version",
        "spm",
        "pub",
        "media",
        "ffi-build",
        "ffi-copy",
        "config",
        "config-retry",
        "settings-Runner",
        "settings-PacketTunnel",
    }
)
DIAGNOSTIC_PATTERNS = {
    "nested_sandbox_denied": r"\bsandbox_apply(?:_container)?: Operation not permitted\b",
    "signer_execution_denied": r"(?:deny|denied|not permitted)[^\n]*(?:codesign|ldid)|(?:codesign|ldid)[^\n]*(?:deny|denied|not permitted)",
    "package_network_failure": r"could not resolve host|connection timed out|failed to connect|network is unreachable|certificate verify failed",
    "signing_attempt_rejected": r"signing attempt rejected|code signing is required|requires a development team|no signing certificate|no profiles for",
    "sandbox_denied": r"sandbox[^\n]*(?:deny|denied)|operation not permitted",
    "unavailable_xcode_sdk": r"SDK[^\n]*(?:cannot be located|not found|not installed)|unable to find a destination|Xcode installation is incomplete",
    "xcode_license_required": r"agree[^\n]*Xcode[^\n]*license|Xcode[^\n]*license[^\n]*(?:accept|agree)",
    "missing_cocoapods": r"CocoaPods not installed|CocoaPods not in valid state|pod: command not found",
    "pod_install_failed": r"Error running pod install|pod install[^\n]*(?:failed|non-zero)|CocoaPods could not find compatible versions",
    "swift_package_resolution_failed": r"could not resolve package dependencies|failed to resolve[^\n]*package|failed to clone repository|package resolution failed",
    "swift_package_manager_configuration": r"Swift Package Manager[^\n]*(?:error|failed|not supported|requires)|SwiftPM[^\n]*(?:error|failed|not supported)",
    "deployment_target_mismatch": r"requires a higher minimum deployment target|deployment target[^\n]*(?:unsupported|outside|less than)|minimum[^\n]*deployment target",
    "dependency_resolution_failed": r"version solving failed|failed to resolve dependencies|unable to resolve dependency|could not find[^\n]*dependency",
    "missing_generated_configuration": r"Generated\.xcconfig[^\n]*(?:not found|must exist|missing)|unable to open base configuration reference file|could not find[^\n]*Generated",
}


def preparation_diagnostics(text: str) -> list[str]:
    # Only fixed identifiers leave this function. Never return captured log text.
    return [
        name
        for name, pattern in DIAGNOSTIC_PATTERNS.items()
        if re.search(pattern, text, re.IGNORECASE)
    ] or ["unclassified_preparation_failure"]


def require_run(
    command: list[str], cwd: Path, env: dict, log: Path, receipt: dict | None = None
) -> None:
    if receipt is not None:
        if log.name not in PREPARATION_STAGES:
            raise ValueError("unknown preparation stage")
        receipt["stage"] = log.name
        receipt.pop("preparation_result", None)
    try:
        code = run(command, cwd, env, log)
    except OSError:
        if receipt is not None:
            receipt["preparation_result"] = {
                "stage": log.name,
                "exit_code": None,
                "outcome": "launch_failed",
            }
        raise
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        if receipt is not None:
            receipt["preparation_result"] = {
                "stage": log.name,
                "exit_code": None,
                "outcome": "timeout"
                if isinstance(exc, subprocess.TimeoutExpired)
                else "cancelled",
            }
        raise
    if receipt is not None:
        receipt["preparation_result"] = {
            "stage": log.name,
            "exit_code": code,
            "outcome": "success" if code == 0 else "failure",
        }
        if code != 0:
            receipt["preparation_result"]["diagnostic_codes"] = preparation_diagnostics(
                log.read_text(errors="replace")
            )
    if code != 0:
        raise ValueError("preparation command failed")


MANIFEST_SANDBOX_ARGUMENT = "-IDEPackageSupportDisableManifestSandbox=YES"


def install_manifest_xcrun(directory: Path) -> None:
    wrapper = directory / "xcrun"
    if wrapper.exists():
        raise ValueError("manifest wrapper already exists")
    wrapper.write_text(
        "#!/bin/sh\n"
        'if [ "${1-}" = xcodebuild ]; then\n'
        "  shift\n"
        '  exec /usr/bin/xcrun xcodebuild -IDEPackageSupportDisableManifestSandbox=YES "$@"\n'
        "fi\n"
        'exec /usr/bin/xcrun "$@"\n'
    )
    wrapper.chmod(0o755)


def config_with_recovery(
    command: list[str],
    root: Path,
    env: dict,
    private: Path,
    profile: Path,
    receipt: dict,
) -> bool:
    # A second call cannot spend another retry, even after a failed recovery.
    if "config_recovery" in receipt:
        raise ValueError("config recovery already attempted")
    guarded = protected_command(command, profile)
    try:
        require_run(guarded, root, env, private / "config", receipt)
        return False
    except ValueError:
        failure = receipt.get("preparation_result", {})
        codes = set(failure.get("diagnostic_codes", []))
        forbidden = {
            "signing_attempt_rejected",
            "signer_execution_denied",
            "package_network_failure",
        }
        if (
            failure.get("stage") != "config"
            or failure.get("outcome") != "failure"
            or "nested_sandbox_denied" not in codes
            or codes & forbidden
            or (private / "signing-violation").exists()
        ):
            raise
        receipt["config_recovery"] = {
            "attempts": 1,
            "reason": "nested_sandbox_denied",
            "first_failure": failure,
            "completed": False,
        }
    receipt["stage"] = "install-manifest-xcrun"
    install_manifest_xcrun(private / "guard-bin")
    # Same real config command, still enclosed by the original signer sandbox.
    require_run(guarded, root, env, private / "config-retry", receipt)
    receipt["config_recovery"]["completed"] = True
    return True


def validate_go_version(text: str, receipt: dict) -> None:
    receipt["stage"] = "validate-go-version"
    if "go version go1.27.4 " not in text:
        raise ValueError("Go runtime version mismatch")


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


def compile_command(
    derived: Path, result: Path, manifest_recovery: bool = False
) -> list[str]:
    return [
        "xcodebuild",
        *([MANIFEST_SANDBOX_ARGUMENT] if manifest_recovery else []),
        *PACKAGE_PLUGIN_ARGUMENTS,
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


COMPILE_DIAGNOSTIC_PATTERNS = {
    "plugin_validation_failed": r"\bplug-?ins?\b[^\n]*(?:failed validation|validation failed)|validation[^\n]*(?:failed|failure)[^\n]*\bplug-?ins?\b",
    "plugin_approval_required": r"\bplug-?ins?\b[^\n]*(?:must be enabled|must be approved|requires approval|not trusted|is disabled)",
    "plugin_execution_failed": r"\b(?:build tool )?plug-?ins?\b[^\n]*(?:failed|error|not permitted)|failed to (?:invoke|run|load)[^\n]*\bplug-?ins?\b",
    "plugin_sandbox_denied": r"\bplug-?ins?\b[^\n]*sandbox[^\n]*(?:deny|denied|not permitted)|sandbox[^\n]*\bplug-?ins?\b[^\n]*(?:deny|denied|not permitted)",
    "macro_execution_failed": r"external macro implementation type|macro expansion[^\n]*(?:failed|error)|macro[^\n]*(?:could not be found|not found)|failed to (?:load|execute)[^\n]*macro",
    "missing_module": r"no such module|missing required module|could not build[^\n]*module|unable to find module|module[^\n]*not found",
    "missing_package_product": r"missing package product|product[^\n]*(?:not found in package|could not be found)|package product[^\n]*(?:not found|missing)",
    "missing_build_input": r"build input file[^\n]*cannot be found|no such file or directory|unable to open[^\n]*file|file[^\n]*(?:not found|does not exist)",
    "missing_framework_or_library": r"framework[^\n]*not found|library[^\n]*not found|cannot find[^\n]*\.(?:a|framework|xcframework)|xcframework[^\n]*(?:missing|does not exist)",
    "swift_syntax_error": r"error: expected (?:expression|declaration|type|pattern|identifier|'}'|'\)')|unterminated string literal|consecutive statements on a line",
    "swift_typecheck_error": r"cannot (?:find[^\n]*in scope|convert value|infer contextual base|assign value)|has no member|does not conform to protocol|ambiguous use of|extra argument[^\n]*in call|missing argument for parameter|incorrect argument label|cannot be used on instance of type",
    "swift_compilation_failed": r"SwiftCompile[^\n]*failed|SwiftEmitModule[^\n]*failed|CompileSwift[^\n]*failed|emit-module command failed|Swift Compiler Error",
    "clang_compilation_failed": r"clang: error:|CompileC[^\n]*failed|fatal error:",
    "linker_failed": r"linker command failed|undefined symbols for architecture|duplicate symbol|^ld: |ld: (?:error|symbol)|Linker command failed",
    "platform_binary_mismatch": r"building for[^\n]*but linking|incompatible architecture|built for[^\n]*which is not the architecture|unsupported file format",
    "script_phase_failed": r"PhaseScriptExecution[^\n]*(?:failed|nonzero exit)|Command[^\n]*script[^\n]*failed",
    "asset_catalog_failed": r"CompileAssetCatalog[^\n]*failed|actool[^\n]*(?:error|failed)",
    "invalid_project_configuration": r"unable to read project|project[^\n]*cannot be opened|scheme[^\n]*(?:not found|does not contain)|Info\.plist[^\n]*(?:invalid|missing)|build setting[^\n]*(?:invalid|required)",
    "signing_configuration_error": r"requires a provisioning profile|requires a development team|code signing[^\n]*(?:required|error|failed)|signing certificate[^\n]*(?:missing|not found)|no profiles for",
    "tool_process_failed": r"Command[^\n]*failed with a nonzero exit code|process[^\n]*(?:crashed|terminated by signal)",
}
KNOWN_BUILD_TARGETS = (
    "Runner",
    "PacketTunnel",
    "Flutter",
    "FlutterGeneratedPluginSwiftPackage",
)
KNOWN_BUILD_STAGES = (
    "ResolvePackageGraph",
    "SwiftCompile",
    "CompileSwift",
    "SwiftEmitModule",
    "CompileC",
    "Ld",
    "PhaseScriptExecution",
    "CompileAssetCatalog",
    "ProcessInfoPlistFile",
    "ValidateEmbeddedBinary",
    "CodeSign",
)
KNOWN_BUILD_MODULES = (
    "Flutter",
    "UIKit",
    "Foundation",
    "NetworkExtension",
    "libclash",
    "Darwin",
    "Swift",
)


# Closed vocabulary: emitted keys are literals from this list, never log captures.
FAILURE_TOKENS = (
    "PhaseScriptExecution",
    "rsync",
    "SwiftCompile",
    "CompileSwift",
    "SwiftEmitModule",
    "CompileC",
    "Ld",
    "CodeSign",
    "CompileAssetCatalog",
    "ProcessInfoPlistFile",
    "module",
    "framework",
    "xcframework",
    "sandbox",
    "sandbox_apply",
    "sandbox_apply_container",
    "deny",
    "denied",
    "read",
    "write",
    "signing",
    "codesign",
    "validation",
    "missing",
    "permission",
    "package",
    "product",
    "dependency",
    "dependencies",
    "deployment",
    "platform",
    "architecture",
    "duplicate",
    "cycle",
    "input",
    "output",
    "command",
    "script",
    "plugin",
    "plugins",
    "macro",
    "disabled",
    "enabled",
    "approval",
    "minimum",
    "version",
    "required",
    "unsupported",
    "binary",
    "resource",
    "asset",
    "header",
    "symbol",
    "symbols",
    "linker",
    "cannot",
    "conflict",
    "directory",
    "file",
    "found",
    "valid",
    "invalid",
    "expected",
    "compatible",
    "incompatible",
    "failed",
    "error",
    "not",
    "permitted",
    "operation",
    "access",
    "restricted",
    "build",
    "target",
    "multiple",
    "produce",
    "outputs",
    "destination",
    "sdk",
    "xcodebuild",
    "resolve",
)


def failure_vocabulary(text: str) -> dict:
    selected = []
    failed_commands = False
    for line in text.splitlines():
        if "The following build commands failed:" in line:
            failed_commands = True
            continue
        command_line = failed_commands and any(
            re.match(r"^\s*" + re.escape(stage) + r"\b", line)
            for stage in KNOWN_BUILD_STAGES
        )
        if (
            re.search(
                r"\berror:|\bfatal:|\bCommand\b[^\n]*\bfailed\b", line, re.IGNORECASE
            )
            or command_line
        ):
            selected.append(line)
    # Even allowlisted words inside URLs, paths, quoted identifiers or credentials
    # must not be counted as diagnostic context.
    selected_text = "\n".join(selected)
    selected_text = re.sub(r"https?://\S+|file://\S+", " ", selected_text)
    selected_text = re.sub(
        r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", " ", selected_text
    )
    selected_text = re.sub(
        # Discard the complete line tail: quoted values and authentication
        # schemes may contain whitespace, so a single-token removal is unsafe.
        r"(?im)\b(?:token|password|secret|authorization|key)\s*[:=][^\n]*",
        " ",
        selected_text,
    )
    selected_text = re.sub(
        r""""(?:\\.|[^"\\\n])*(?:"|$)|'(?:\\.|[^'\\\n])*(?:'|$)""",
        " ",
        selected_text,
        flags=re.MULTILINE,
    )
    selected_text = re.sub(r"\S*[\\/]\S*", " ", selected_text)
    counts = {
        name: len(
            re.findall(r"\b" + re.escape(name) + r"\b", selected_text, re.IGNORECASE)
        )
        for name in FAILURE_TOKENS
    }
    return {
        "failure_line_count": len(selected),
        "failure_token_counts": {
            name: count for name, count in counts.items() if count
        },
    }


def compiler_diagnostics(text: str) -> dict:
    patterns = {**DIAGNOSTIC_PATTERNS, **COMPILE_DIAGNOSTIC_PATTERNS}
    codes = [
        name
        for name, pattern in patterns.items()
        if re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    ]
    return {
        "diagnostic_codes": codes or ["unclassified_compiler_failure"],
        **failure_vocabulary(text),
        "observed_targets": [
            name
            for name in KNOWN_BUILD_TARGETS
            if re.search(r"in target ['\"]" + re.escape(name) + r"['\"]", text)
        ],
        "observed_stages": [
            name
            for name in KNOWN_BUILD_STAGES
            if re.search(r"^\s*" + re.escape(name) + r"\b", text, re.MULTILINE)
        ],
        "referenced_modules": [
            name
            for name in KNOWN_BUILD_MODULES
            if re.search(
                r"(?:module|framework|library) ['\"]" + re.escape(name) + r"['\"]", text
            )
        ],
    }


def xcresult_error_messages(payload: object) -> str:
    """Return xcresult error messages. Locations and source URLs stay out."""
    if not isinstance(payload, dict):
        return ""
    errors = payload.get("errors")
    if not isinstance(errors, list):
        return ""
    lines: list[str] = []
    for item in errors:
        if not isinstance(item, dict):
            continue
        message = item.get("message")
        if isinstance(message, str) and message.strip():
            lines.append(message)
    return "\n".join(lines)


def read_xcresult_errors(bundle: Path) -> str:
    """Read allowlisted error text from a run-local bundle, or nothing."""
    if not bundle.is_dir():
        return ""
    try:
        completed = subprocess.run(
            [
                "/usr/bin/xcrun",
                "xcresulttool",
                "get",
                "build-results",
                "--path",
                str(bundle),
                "--compact",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0 or not completed.stdout:
        return ""
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return ""
    return xcresult_error_messages(payload)


def compile_evidence(
    text: str, failed: bool = False, *, xcresult_errors: str = ""
) -> dict:
    # Only the actual compile stream can prove command execution. Structured
    # error messages may contain command-shaped text but are diagnostic evidence.
    diagnostic_text = text + ("\n" + xcresult_errors if xcresult_errors else "")
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
        diagnostic_text,
    )
    return {
        "compiled_targets": targets,
        "signing_operation": signing,
        "expected_negative_diagnostic": missing is not None,
        "diagnostic": missing.group(0) if missing else None,
        **(compiler_diagnostics(diagnostic_text) if failed else {}),
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
        receipt["stage"] = "initial-source-identity"
        before = source_identity(root, args.expected_source)
        receipt["stage"] = "project-phase-identity"
        validate_project(root)
        receipt["stage"] = "source-mode-identity"
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
        require_run(
            ["xcodebuild", "-version"], root, env, private / "xcode-version", receipt
        )
        receipt["stage"] = "validate-xcode-version"
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
            receipt,
        )
        receipt["signing_sandbox"] = (
            "deny codesign/ldid execution; read-only media signature verification runs separately"
        )
        original = args.flutter_sdk.resolve()
        isolated = private / "flutter-3.47.8-no-sign"
        receipt["stage"] = "prepare-isolated-sdk"
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
            receipt,
        )
        require_run(
            protected_command(["flutter", "--version", "--machine"], profile),
            root,
            env,
            private / "flutter-version",
            receipt,
        )
        receipt["stage"] = "validate-flutter-version"
        info = json.loads((private / "flutter-version").read_text())
        if (
            info.get("frameworkVersion") != "3.47.8"
            or info.get("frameworkRevision") != FLUTTER_COMMIT
        ):
            raise ValueError("Flutter runtime identity mismatch")
        receipt["stage"] = "validate-adapted-snapshot"
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
        manifest_recovery = False
        for command, label in commands:
            receipt["stage"] = label
            guarded = (
                command if label == "media" else protected_command(command, profile)
            )
            if label == "config":
                manifest_recovery = config_with_recovery(
                    command, root, env, private, profile, receipt
                )
            else:
                require_run(guarded, root, env, private / label, receipt)
            if label == "go-version":
                validate_go_version((private / label).read_text(), receipt)
        receipt["stage"] = "ffi-artifact-identity"
        receipt["ffi"] = {
            name: sha((root / name).read_bytes())
            for name in ("ios/Frameworks/libclash.a", "ios/Frameworks/libclash.h")
        }
        receipt["settings"] = {}
        for target in ("Runner", "PacketTunnel"):
            command = [
                "xcodebuild",
                *([MANIFEST_SANDBOX_ARGUMENT] if manifest_recovery else []),
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
            require_run(
                protected_command(command, profile),
                root,
                env,
                private / ("settings-" + target),
                receipt,
            )
            receipt["stage"] = "validate-settings-" + target
            receipt["settings"][target] = signing_disabled(
                (private / ("settings-" + target)).read_text(), target
            )
        receipt["stage"] = "source-identity-after-preparation"
        if source_identity(root, args.expected_source) != before:
            raise ValueError("source inputs changed during preparation")
        derived = private / "DerivedData"
        receipt["stage"] = "actual-xcodebuild"
        code = run(
            protected_command(
                compile_command(
                    derived, private / "compile.xcresult", manifest_recovery
                ),
                profile,
            ),
            root,
            env,
            private / "compile",
            timeout=1800,
        )
        compile_text = (private / "compile").read_text(errors="replace")
        bundle_errors = read_xcresult_errors(private / "compile.xcresult")
        evidence = compile_evidence(
            compile_text, failed=code != 0, xcresult_errors=bundle_errors
        )
        receipt["evidence"] = evidence
        receipt["compiler_exit"] = code
        receipt["stage"] = "validate-compile-signing"
        if violation.exists() or evidence["signing_operation"]:
            raise ValueError("signing operation rejected")
        receipt["stage"] = "source-identity-after-compilation"
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
