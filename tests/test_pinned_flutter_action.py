from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github/actions/setup-flutter/action.yml"
INSTALLER = ROOT / ".github/actions/setup-flutter/install_flutter_sdk.py"
HELPER = Path(os.environ.get("YUELINK_SOURCE", str(ROOT.parent / "yuelink"))) / "scripts/ci/link_platform_cache.py"


def _step_body(workflow: str, name: str) -> str:
    marker = f"- name: {name}\n"
    start = workflow.index(marker)
    rest = workflow[start + len(marker) :]
    match = re.search(r"\n      - (?:name|uses):", rest)
    return marker + (rest[: match.start()] if match else rest)


def _installer_module():
    spec = importlib.util.spec_from_file_location("install_flutter_sdk", INSTALLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PinnedFlutterActionTests(unittest.TestCase):
    def test_composite_action_has_no_mutable_nested_action(self) -> None:
        source = ACTION.read_text(encoding="utf-8")
        refs = re.findall(r"(?m)^\s*uses:\s*([^\s#]+)", source)
        assert refs == ["actions/cache@cdf6c1fa76f9f475f3d7449005a359c84ca0f306"]
        assert "subosito/flutter-action" not in source
        assert "runner.tool_cache" in source

    def test_release_table_is_exact_and_closed(self) -> None:
        installer = _installer_module()
        assert installer.BASE_URL == (
            "https://storage.googleapis.com/flutter_infra_release/releases/"
        )
        assert installer.RELEASES == {
            ("3.47.2", "Linux"): (
                "stable/linux/flutter_linux_3.47.2-stable.tar.xz",
                "447878859d01ca9bfdb99a85f245af07ed8a15fedcd9d189c4749e8e92d1f185",
            ),
            ("3.47.2", "Darwin"): (
                "stable/macos/flutter_macos_3.47.2-stable.zip",
                "b6fd6ba98c8503d5ee06a6670627b5b1c36167ece3427435ec83b66e9b28c6b5",
            ),
            ("3.47.2", "Windows"): (
                "stable/windows/flutter_windows_3.47.2-stable.zip",
                "37934f2128a55d77a38baba12fd611157ed23a47bf7d2b7d17e9e84da118409d",
            ),
            ("3.47.3", "Linux"): (
                "stable/linux/flutter_linux_3.47.3-stable.tar.xz",
                "988665565cad9091db1baa54bf6d3868bb40e29719592f3c3a164deefd4208e1",
            ),
            ("3.47.3", "Darwin"): (
                "stable/macos/flutter_macos_3.47.3-stable.zip",
                "cd1e1a877db74b8928251225d38469077994e904b1b5bb359f3376c43ac0495c",
            ),
            ("3.47.3", "Windows"): (
                "stable/windows/flutter_windows_3.47.3-stable.zip",
                "44822ab53afb543c6fb446c078ba4f495e3eeddaafb88d879bde606447ddce24",
            ),
            ("3.47.4", "Linux"): (
                "stable/linux/flutter_linux_3.47.4-stable.tar.xz",
                "5b45f0ceda99b9bebdc873e7e69f6450aeb4c30f454b505e2e62fc9255a907d3",
            ),
            ("3.47.5", "Linux"): (
                "stable/linux/flutter_linux_3.47.5-stable.tar.xz",
                "2132e990f236f8d22e7c6314b29a191a95b10d7cbcfec9b4e2e303d996652cbb",
            ),
            ("3.47.6", "Linux"): (
                "stable/linux/flutter_linux_3.47.6-stable.tar.xz",
                "f1631b9c2c8b3529323db412b0d1beacf4a748f8783b0d7cf599a8fd5f461675",
            ),
            ("3.47.7", "Linux"): (
                "stable/linux/flutter_linux_3.47.7-stable.tar.xz",
                "af7455f540df951184c7953fbe3cf884766e7541dacf8571f9f582db03dd6230",
            ),
            ("3.47.4", "Darwin"): (
                "stable/macos/flutter_macos_3.47.4-stable.zip",
                "72ced0f81a5ea48f30d221699fa45378cdd1fa15426512444f32e4e1bee9db8b",
            ),
            ("3.47.5", "Darwin"): (
                "stable/macos/flutter_macos_3.47.5-stable.zip",
                "a7893bb0feecd8bd066f4f7e850356f896a6fc3b862e6c506d21c76691a66b72",
            ),
            ("3.47.6", "Darwin"): (
                "stable/macos/flutter_macos_3.47.6-stable.zip",
                "f1f68c777b2b34153e1631670445efbeb218f42a398be32bba2051476719b0a4",
            ),
            ("3.47.7", "Darwin"): (
                "stable/macos/flutter_macos_3.47.7-stable.zip",
                "17d39b000a5d0681e3f7b61a105a36f6f31dd41ab9073f625fd466f38035f901",
            ),
            ("3.47.4", "Windows"): (
                "stable/windows/flutter_windows_3.47.4-stable.zip",
                "31173300481bd06e377fd55ee84214689648b1817563efd7b450b7b78bdf351a",
            ),
            ("3.47.5", "Windows"): (
                "stable/windows/flutter_windows_3.47.5-stable.zip",
                "0ccd71931f49c2fbe394b1eeb6d79af3d624058a043ea0d03d34160581624fb8",
            ),
            ("3.47.6", "Windows"): (
                "stable/windows/flutter_windows_3.47.6-stable.zip",
                "a01bb0d26de91bc23c97cd9ccfaad281a612fb8304213fdd5df1119a09404796",
            ),
            ("3.47.7", "Windows"): (
                "stable/windows/flutter_windows_3.47.7-stable.zip",
                "c2c875da25a1a1448d18ee4e8f398b2f6fce888b4775d8f9130538e16d826cb4",
            ),
            ("3.47.8", "Linux"): (
                "stable/linux/flutter_linux_3.47.8-stable.tar.xz",
                "baf7dcc5a3dbba60fe863bf493a6cf203195621f7786eaa5a1bba02cd0d87dd2",
            ),
            ("3.47.8", "Darwin"): (
                "stable/macos/flutter_macos_3.47.8-stable.zip",
                "b8a3c255a51a8c0c0943a4d5bdf9b26008c34f02febf69640c8aab413afd896f",
            ),
            ("3.47.8", "Windows"): (
                "stable/windows/flutter_windows_3.47.8-stable.zip",
                "11e19ea3409808fe3b3765ea8149640c299e58cce6eda7c9f38fc1b4a193eb6e",
            ),
        }

    def test_archive_member_validation_rejects_traversal_and_absolute_paths(self) -> None:
        installer = _installer_module()
        for unsafe in ("../escape", "flutter/../../escape", "/absolute", "C:\\escape"):
            assert installer._safe_member(unsafe) is False
        for safe in ("flutter/bin/flutter", "flutter/bin/cache/dart-sdk"):
            assert installer._safe_member(safe) is True

    def test_tar_links_allow_only_targets_resolving_inside_archive_root(self) -> None:
        installer = _installer_module()
        # Present in Flutter 3.47.2's official Linux archive: the target resolves
        # to flutter/engine/src/flutter/lib/web_ui/test/webparagraph/... .
        assert installer._safe_tar_link(
            "flutter/engine/src/flutter/lib/web_ui/test/ui/paragraph_performance_test.dart",
            "../webparagraph/paragraph_performance_test.dart",
            hard_link=False,
        )
        assert installer._safe_tar_link(
            "flutter/bin/cache/example", "flutter/bin/flutter", hard_link=True
        )

        for link_name in (
            "../../../../../../../../etc/passwd",
            "/etc/passwd",
            "C:\\Windows\\system.ini",
        ):
            assert not installer._safe_tar_link(
                "flutter/bin/cache/link", link_name, hard_link=False
            )
        assert not installer._safe_tar_link(
            "flutter/bin/cache/link", "../escape", hard_link=True
        )

    def test_cache_receipt_binds_platform_arch_version_and_archive_hash(self) -> None:
        installer = _installer_module()
        expected = installer.receipt("3.47.2", "Linux", "X64", "a" * 64)
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        target = Path(temp_dir.name) / "flutter"
        (target / "bin").mkdir(parents=True)
        (target / "bin/flutter").write_text("runtime", encoding="utf-8")
        (target / installer.RECEIPT_NAME).write_text(
            __import__("json").dumps(expected), encoding="utf-8"
        )
        assert installer.valid_cached_sdk(target, expected, "Linux") is True
        assert installer.valid_cached_sdk(
            target, {**expected, "runner_arch": "ARM64"}, "Linux"
        ) is False

    def test_windows_runtime_probe_uses_command_processor_and_call(self) -> None:
        installer = _installer_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            # Exercise Windows' most failure-prone case: a tool-cache path that
            # requires cmd.exe quoting.
            target = Path(temp_dir) / "SDK Root"
            executable = target / "bin" / "flutter.bat"
            executable.parent.mkdir(parents=True)
            executable.write_text("@exit /b 0\n", encoding="utf-8")
            command_processor = Path(temp_dir) / "cmd.exe"
            command_processor.write_bytes(b"runner command processor")
            observed: list[list[str]] = []

            def run(
                command: list[str], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                observed.append(command)
                assert kwargs == {
                    "check": True,
                    "capture_output": True,
                    "text": True,
                }
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=(
                        "Resolving dependencies...\n"
                        "Got dependencies.\n"
                        '{"frameworkVersion":"3.47.2"}\n'
                    ),
                    stderr="Building flutter tool...\nRunning pub upgrade...\n",
                )

            with mock.patch.dict(os.environ, {"COMSPEC": str(command_processor)}):
                with mock.patch.object(installer.subprocess, "run", run):
                    installer.verify_runtime(target, "3.47.2", "Windows")

            assert observed == [
                [
                    str(command_processor),
                    "/d",
                    "/s",
                    "/c",
                    "call "
                    + subprocess.list2cmdline(
                        [str(executable), "--version", "--machine"]
                    ),
                ]
            ]

    def test_windows_runtime_probe_fails_closed_without_command_processor(self) -> None:
        installer = _installer_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "sdk"
            (target / "bin").mkdir(parents=True)
            (target / "bin/flutter.bat").write_text("@exit /b 0\n", encoding="utf-8")
            environment = dict(os.environ)
            environment.pop("COMSPEC", None)
            with mock.patch.dict(os.environ, environment, clear=True):
                try:
                    installer.verify_runtime(target, "3.47.2", "Windows")
                except SystemExit as exc:
                    assert "Windows command processor is absent" in str(exc)
                else:  # pragma: no cover - fail-closed assertion
                    raise AssertionError("missing COMSPEC was accepted")

    def test_windows_runtime_probe_rejects_command_metacharacters(self) -> None:
        installer = _installer_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            command_processor = Path(temp_dir) / "cmd.exe"
            command_processor.write_bytes(b"runner command processor")
            for unsafe in ("SDK%TEMP%", "SDK!VAR!", "SDK&next", "SDK(test)"):
                target = Path(temp_dir) / unsafe
                (target / "bin").mkdir(parents=True)
                (target / "bin/flutter.bat").write_text(
                    "@exit /b 0\n", encoding="utf-8"
                )
                with mock.patch.dict(os.environ, {"COMSPEC": str(command_processor)}):
                    try:
                        installer.verify_runtime(target, "3.47.2", "Windows")
                    except SystemExit as exc:
                        assert "command-processor metacharacters" in str(exc)
                    else:  # pragma: no cover - fail-closed assertion
                        raise AssertionError(f"unsafe Windows path {unsafe!r} was accepted")

    def test_machine_document_parser_is_closed_after_the_final_object(self) -> None:
        installer = _installer_module()
        assert installer.parse_machine_document(
            'first-run progress\n{"frameworkVersion":"3.47.2"}\r\n'
        ) == {"frameworkVersion": "3.47.2"}

        for invalid in (
            "first-run progress only\n",
            '[{"frameworkVersion":"3.47.2"}]\n',
            '{"frameworkVersion":"3.47.2"}\ntrailing output\n',
            '{"frameworkVersion":"3.47.2"}\n{"frameworkVersion":"3.47.2"}\n',
        ):
            try:
                installer.parse_machine_document(invalid)
            except ValueError:
                pass
            else:  # pragma: no cover - fail-closed assertion
                raise AssertionError(f"invalid Flutter machine output was accepted: {invalid!r}")

    def test_native_apple_silicon_sdk_is_official_and_pinned(self) -> None:
        installer = _installer_module()
        assert installer.ARCH_RELEASES == {
            ("3.47.3", "Darwin", "ARM64"): (
                "stable/macos/flutter_macos_arm64_3.47.3-stable.zip",
                "66144a7cd691059c8c73bfce24178fb9ea2c95d798db8d56d1e2411e760f6583",
            ),
            ("3.47.4", "Darwin", "ARM64"): (
                "stable/macos/flutter_macos_arm64_3.47.4-stable.zip",
                "c6af6fa1d64946167b8637ba4c5a7bb3b8574729a1ebe931decd4924dbab7061",
            ),
            ("3.47.5", "Darwin", "ARM64"): (
                "stable/macos/flutter_macos_arm64_3.47.5-stable.zip",
                "d4dd908b5f8f65515831b6d68ae33307a813f2b68947dded7a1994ee5ea7cead",
            ),
            ("3.47.6", "Darwin", "ARM64"): (
                "stable/macos/flutter_macos_arm64_3.47.6-stable.zip",
                "a1946d3b6b3de15ce247dc89649df9035ce29e6b4e7ebe91919a25890ea2e79a",
            ),
            ("3.47.7", "Darwin", "ARM64"): (
                "stable/macos/flutter_macos_arm64_3.47.7-stable.zip",
                "dd9da574c3168460550117eb0e8dd0313ae934fabf64a82a6d557fff5fd1422e",
            ),
            ("3.47.8", "Darwin", "ARM64"): (
                "stable/macos/flutter_macos_arm64_3.47.8-stable.zip",
                "6114edc2dae3636cfefb7e4456a4510399ee856e324149b34dbacb509e329d05",
            ),
        }


class LinkPlatformCacheTests(unittest.TestCase):
    def test_generated_workflow_splits_go_cache_and_bounds_apt(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(
            encoding="utf-8"
        )
        starts = [
            match.start()
            for match in re.finditer(r"- uses: actions/setup-go@", workflow)
        ]
        assert len(starts) == 2
        for start in starts:
            end = workflow.find("\n      - ", start)
            block = workflow[start:end]
            assert "cache: false" in block
            assert "cache: true" not in block
            assert "cache-dependency-path" not in block
        java = workflow.index("- name: Set up Java for native ownership tests")
        restore = workflow.index("- name: Restore Go module cache\n")
        assert java < starts[1] < restore

        for name in (
            "Restore Go module cache",
            "Restore Go module cache (Windows)",
            "Restore Go compiler cache",
            "Restore Go compiler cache (Windows)",
            "Restore Gradle dependency caches",
        ):
            assert "continue-on-error: true" in _step_body(workflow, name)
        core = _step_body(workflow, "Build & install Go core")
        assert "cache-hit" not in core

        linux = _step_body(workflow, "Install Linux build dependencies")
        assert "sudo timeout --signal=KILL 180 apt-get" in linux
        assert "apt_bounded update -q" in linux
        assert "apt_bounded install -y --no-install-recommends" in linux
        assert "link_platform_cache.py apt-result" in linux
        assert workflow.count("apt_bounded()") == 1

        gradle = _step_body(workflow, "Restore Gradle dependency caches")
        assert workflow.index("- name: Restore Gradle dependency caches") < workflow.index(
            "- name: Decode Android keystore"
        )
        paths = [
            line.strip()
            for line in gradle.splitlines()
            if line.strip().startswith("~/") or "github.workspace" in line.lower()
        ]
        assert paths == ["~/.gradle/caches", "~/.gradle/wrapper"]
        assert "configuration-cache" not in workflow
        assert "android/gradle/wrapper/gradle-wrapper.properties" in gradle

        assert "COMPILATION_CACHE_ENABLE_CACHING = YES" in workflow
        assert "XCODE_XCCONFIG_FILE=$cache_config" in workflow
        assert "CLANG_ENABLE_PROJECT_PREFIX_MAPPING = YES" in workflow
        assert "SWIFT_ENABLE_PROJECT_PREFIX_MAPPING = YES" in workflow
        key = (
            "yue-xcode-cc-v1-${{ runner.os }}-${{ runner.arch }}"
            "-${{ matrix.platform }}-${{ github.sha }}"
        )
        prefix = (
            "yue-xcode-cc-v1-${{ runner.os }}-${{ runner.arch }}"
            "-${{ matrix.platform }}-"
        )
        restore_xcode = _step_body(workflow, "Restore Xcode compilation cache")
        save = _step_body(workflow, "Save Xcode compilation cache")
        decide = _step_body(workflow, "Decide Xcode compilation cache save")
        assert key in restore_xcode and key in save
        assert prefix in restore_xcode
        assert "actions/cache/save@" in save
        assert "steps.xcode_cache_save.outputs.save == 'true'" in save
        script = decide.split("run: |", 1)[1]
        assert "${{" not in script
        assert "xcode-save --bytes" in script
        assert 'tar -cf - -C "$cache" .' in script
        assert "bytes + bytes / 100 + 1048576" in script

        gomod = _step_body(workflow, "Restore Go module cache")
        assert "path: ~/go/pkg/mod" in gomod
        assert (
            "yue-gomod-v1-${{ runner.os }}-${{ runner.arch }}"
            "-${{ matrix.platform }}-${{ env.GO_VERSION }}-${{ github.sha }}"
        ) in gomod
        assert (
            "yue-gomod-v1-${{ runner.os }}-${{ runner.arch }}"
            "-${{ matrix.platform }}-${{ env.GO_VERSION }}-"
        ) in gomod
        windows_mod = _step_body(workflow, "Restore Go module cache (Windows)")
        assert "path: ~\\go\\pkg\\mod" in windows_mod
        compiler = _step_body(workflow, "Restore Go compiler cache")
        assert "runner.os == 'macOS' && '~/Library/Caches/go-build'" in compiler
        assert "|| '~/.cache/go-build'" in compiler
        windows_compiler = _step_body(workflow, "Restore Go compiler cache (Windows)")
        assert "path: ~\\AppData\\Local\\go-build" in windows_compiler
        assert "runner.os" in compiler

        pin = (
            "onesyue/yuelink-ci/.github/actions/setup-flutter@"
            "d6c9eb965b79818ed944b3e2df168905b535ff49"
        )
        flutter_starts = [
            match.start() for match in re.finditer(re.escape(pin), workflow)
        ]
        assert len(flutter_starts) == 3
        for start in flutter_starts:
            end = workflow.find("\n      - ", start)
            block = workflow[start:end]
            assert re.search(r"(?m)^\s+cache: true\s*$", block)
            assert re.search(r"(?m)^\s+cache: false\s*$", block) is None

    def test_dependency_cache_actions_keys_and_restore_prefixes(self) -> None:
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        self._assert_cache_identity(workflow)

    def _assert_cache_identity(self, workflow: str) -> None:
        sdk = json.loads((ROOT / "tests/fixtures/flutter-coverage-groups-20261028.json").read_text())["live_sdk"]
        fields = ("frameworkVersion", "frameworkRevision", "engineRevision", "dartSdkVersion")
        identity = "-sdk-" + "-".join(sdk[field] for field in fields)
        blocks = re.split(r"(?m)^      - ", workflow)
        caches = [block for block in blocks if re.search(r"(?m)^(?:        )?uses: actions/cache", block)]
        assert len(caches) == 9
        for block in caches:
            assert re.search(r"uses: actions/cache(?:/restore|/save)?@55cc8345863c7cc4c66a329aec7e433d2d1c52a9(?: |$)", block)
            key = re.search(r"(?m)^          key: (.+)$", block)
            assert key is not None
            assert identity in key[1]
            assert "${{ hashFiles('pubspec.lock') }}" in key[1]
            if "restore-keys:" in block:
                prefixes = re.findall(r"(?m)^            (\S.*)$", block.split("restore-keys: |\n", 1)[1])
                assert prefixes
                for prefix in prefixes:
                    assert identity in prefix
                    assert "${{ hashFiles('pubspec.lock') }}" in prefix
                    assert key[1].startswith(prefix)
        for source in (workflow, ACTION.read_text()):
            for ref in re.findall(r"uses:\s*(actions/cache[^\s#]*)", source):
                assert re.fullmatch(r"actions/cache(?:/restore|/save)?@[0-9a-f]{40}", ref)

    def test_cache_paths_cannot_restore_release_or_attestation_outputs(self) -> None:
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        self._assert_cache_boundary(workflow)

    def _assert_cache_boundary(self, workflow: str) -> None:
        allowed = {
            "~/Library/Developer/Xcode/DerivedData/CompilationCache.noindex",
            "~/go/pkg/mod", r"~\go\pkg\mod",
            "${{ runner.os == 'macOS' && '~/Library/Caches/go-build' || '~/.cache/go-build' }}",
            r"~\AppData\Local\go-build", "~/.pub-cache", r"~\AppData\Local\Pub\Cache",
            "~/.gradle/caches", "~/.gradle/wrapper",
        }
        blocks = re.split(r"(?m)^      - ", workflow)
        seen = set()
        for block in blocks:
            if re.search(r"(?m)^(?:        )?uses: actions/cache", block):
                path = block.split("          path: ", 1)[1].split("          key:", 1)[0].strip()
                paths = path[1:].splitlines() if path.startswith("|") else [path]
                for item in paths:
                    if item.strip():
                        assert item.strip() in allowed
                        seen.add(item.strip())
            title = block.splitlines()[0]
            if re.search(r"sign|attest|provenance|promot", title, re.IGNORECASE):
                assert "actions/cache" not in block
                assert "cache-hit" not in block
                for path in allowed:
                    assert path not in block
        assert seen == allowed
        attest = _step_body(workflow, "Attest build provenance")
        assert "subject-path: 'dist/*'" in attest
        assert "continue-on-error" not in attest
        for name in ("Build & install Go core", "Verify exact attested source checkout"):
            assert "cache-hit" not in _step_body(workflow, name)

    def test_cache_contract_rejects_anonymous_output_restore_and_broad_fallback(self) -> None:
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        unsafe = workflow + "\n      - uses: actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9\n        with:\n          path: dist\n          key: unsafe\n"
        with self.assertRaises(AssertionError):
            self._assert_cache_boundary(unsafe)
        with self.assertRaises(AssertionError):
            self._assert_cache_identity(unsafe)
        unsafe = workflow.replace("          restore-keys: |\n", "          restore-keys: |\n            yue-\n", 1)
        with self.assertRaises(AssertionError):
            self._assert_cache_identity(unsafe)

    def test_cache_identity_rejects_mutable_actions_and_missing_identity(self) -> None:
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        sdk = json.loads(
            (ROOT / "tests/fixtures/flutter-coverage-groups-20261028.json").read_text()
        )["live_sdk"]
        mutations = [
            workflow.replace(
                "actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9",
                "actions/cache/restore@v6", 1,
            ),
            workflow.replace("${{ hashFiles('pubspec.lock') }}", "unbound", 1),
        ]
        mutations.extend(workflow.replace(value, "unbound", 1) for value in (
            "-" + sdk[field] + "-" for field in (
                "frameworkVersion", "frameworkRevision", "engineRevision", "dartSdkVersion"
            )
        ))
        for unsafe in mutations:
            with self.assertRaises(AssertionError):
                self._assert_cache_identity(unsafe)

    def test_apt_shell_maps_kill_and_retries_without_errexit(self) -> None:
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        linux = _step_body(workflow, "Install Linux build dependencies")
        function = textwrap.dedent(linux.split("run: |\n", 1)[1].split("          apt_bounded update", 1)[0])
        # Execute the actual function with command stand-ins; no sudo/network.
        for statuses, expected, attempts in (("137 137", 124, 2), ("137 0", 0, 2), ("100 0", 0, 2), ("0", 0, 1)):
            script = r'''set -euo pipefail
count=0
sudo() {
  test "$1 $2 $3 $4" = 'timeout --signal=KILL 180 apt-get'
  count=$((count + 1))
  eval "return \${status_$count}"
}
python3() {
  echo "helper:$4:$6"
  if [ "$4:$6" = "2:124" ]; then echo APT_TIMEOUT; return 2; fi
  return 1
}
'''
            script += "\n".join(f"status_{i}={status}" for i, status in enumerate(statuses.split(), 1)) + "\n"
            result = subprocess.run(["bash", "-c", script + function + "\napt_bounded update -q\necho calls=$count\n"], check=False, capture_output=True, text=True)
            assert result.returncode == expected, result.stderr
            assert ("APT_TIMEOUT" in result.stdout) == (expected == 124)
            if expected == 0:
                assert f"calls={attempts}" in result.stdout
            else:
                assert "helper:1:124" in result.stdout
                assert "helper:2:124" in result.stdout

    @unittest.skipUnless(
        HELPER.is_file(), "private helper is absent from this checkout"
    )
    def test_second_apt_timeout_and_xcode_size_gate(self) -> None:
        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, str(HELPER), *args],
                check=False,
                capture_output=True,
                text=True,
            )

        timeout = run("apt-result", "--attempt", "2", "--status", "124")
        assert timeout.returncode == 2
        assert timeout.stdout.strip() == "APT_TIMEOUT"
        retry = run("apt-result", "--attempt", "1", "--status", "124")
        assert retry.returncode == 1
        assert "APT_TIMEOUT" not in retry.stdout
        assert run("apt-result", "--attempt", "1", "--status", "0").returncode == 0
        two_gib = 2 * 1024 * 1024 * 1024
        assert run("xcode-save", "--bytes", str(two_gib)).returncode == 0
        over = run("xcode-save", "--bytes", str(two_gib + 1))
        assert over.returncode == 3
        assert "skip Xcode cache save" in over.stdout
