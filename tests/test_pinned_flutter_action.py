from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github/actions/setup-flutter/action.yml"
INSTALLER = ROOT / ".github/actions/setup-flutter/install_flutter_sdk.py"


def _installer_module():
    spec = importlib.util.spec_from_file_location("install_flutter_sdk", INSTALLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_composite_action_has_no_mutable_nested_action() -> None:
    source = ACTION.read_text(encoding="utf-8")
    refs = re.findall(r"(?m)^\s*uses:\s*([^\s#]+)", source)
    assert refs == ["actions/cache@cdf6c1fa76f9f475f3d7449005a359c84ca0f306"]
    assert "subosito/flutter-action" not in source
    assert "runner.tool_cache" in source


def test_release_table_is_exact_and_closed() -> None:
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
    }


def test_archive_member_validation_rejects_traversal_and_absolute_paths() -> None:
    installer = _installer_module()
    for unsafe in ("../escape", "flutter/../../escape", "/absolute", "C:\\escape"):
        assert installer._safe_member(unsafe) is False
    for safe in ("flutter/bin/flutter", "flutter/bin/cache/dart-sdk"):
        assert installer._safe_member(safe) is True


def test_tar_links_allow_only_targets_resolving_inside_archive_root() -> None:
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


def test_cache_receipt_binds_platform_arch_version_and_archive_hash(tmp_path: Path) -> None:
    installer = _installer_module()
    expected = installer.receipt("3.47.2", "Linux", "X64", "a" * 64)
    target = tmp_path / "flutter"
    (target / "bin").mkdir(parents=True)
    (target / "bin/flutter").write_text("runtime", encoding="utf-8")
    (target / installer.RECEIPT_NAME).write_text(
        __import__("json").dumps(expected), encoding="utf-8"
    )
    assert installer.valid_cached_sdk(target, expected, "Linux") is True
    assert installer.valid_cached_sdk(
        target, {**expected, "runner_arch": "ARM64"}, "Linux"
    ) is False


def test_windows_runtime_probe_uses_command_processor_and_call() -> None:
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


def test_windows_runtime_probe_fails_closed_without_command_processor() -> None:
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


def test_windows_runtime_probe_rejects_command_metacharacters() -> None:
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


def test_machine_document_parser_is_closed_after_the_final_object() -> None:
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
