from __future__ import annotations

import importlib.util
import re
from pathlib import Path


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
        ("3.44.9", "Linux"): (
            "stable/linux/flutter_linux_3.44.9-stable.tar.xz",
            "a9120fa4a01048bdef438ddc3a2d4b7389662ea98a95db86eeaf10382bc4efcb",
        ),
        ("3.44.9", "Darwin"): (
            "stable/macos/flutter_macos_3.44.9-stable.zip",
            "4ffed93b2059aa4cfa829723ce3a31c48988bbdbfc014af870c7dfe937ecc0fa",
        ),
        ("3.44.9", "Windows"): (
            "stable/windows/flutter_windows_3.44.9-stable.zip",
            "8ef1107d226654736755bc51b969d6bd46787ff0241650f942e774fb0ca7d0ac",
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
    # Present in Flutter 3.44.9's official Linux archive: the target resolves
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
    expected = installer.receipt("3.44.9", "Linux", "X64", "a" * 64)
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
