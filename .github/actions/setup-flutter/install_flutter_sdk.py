#!/usr/bin/env python3
"""Install an official Flutter SDK from a version-and-hash closed set."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid
from pathlib import Path, PurePosixPath
from zipfile import ZipFile
import tarfile


BASE_URL = "https://storage.googleapis.com/flutter_infra_release/releases/"
RELEASES = {
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
RECEIPT_NAME = ".yue-flutter-sdk.json"


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"pinned Flutter installer: {message}")


def _safe_member(name: str) -> bool:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    return (
        bool(normalized)
        and not path.is_absolute()
        and re.match(r"^[A-Za-z]:/", normalized) is None
        and ".." not in path.parts
    )


def inspect_archive(archive: Path) -> None:
    if archive.suffix == ".zip":
        with ZipFile(archive) as bundle:
            for item in bundle.infolist():
                if not _safe_member(item.filename):
                    fail(f"unsafe zip member {item.filename!r}")
        return

    with tarfile.open(archive, mode="r:xz") as bundle:
        for item in bundle.getmembers():
            if not _safe_member(item.name):
                fail(f"unsafe tar member {item.name!r}")
            if item.ischr() or item.isblk() or item.isfifo():
                fail(f"special tar member {item.name!r}")
            if item.issym() or item.islnk():
                target = PurePosixPath(item.name).parent / item.linkname
                if not _safe_member(str(target)):
                    fail(f"unsafe tar link {item.name!r} -> {item.linkname!r}")


def download(url: str, destination: Path, expected_sha256: str) -> None:
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": "yuelink-ci/1"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open(
        "wb"
    ) as output:
        if response.geturl() != url:
            fail(f"unexpected SDK redirect to {response.geturl()!r}")
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    actual = digest.hexdigest()
    if actual != expected_sha256:
        fail(f"SDK SHA-256 mismatch: got {actual}, expected {expected_sha256}")


def extract(archive: Path, destination: Path, system: str) -> None:
    inspect_archive(archive)
    if system == "Linux":
        command = [
            "tar",
            "--extract",
            "--xz",
            "--file",
            str(archive),
            "--directory",
            str(destination),
            "--no-same-owner",
            "--no-same-permissions",
        ]
    elif system == "Darwin":
        command = ["/usr/bin/ditto", "-x", "-k", str(archive), str(destination)]
    elif system == "Windows":
        command = ["tar", "-xf", str(archive), "-C", str(destination)]
    else:  # pragma: no cover - selected before download
        fail(f"unsupported operating system {system!r}")
    subprocess.run(command, check=True)


def receipt(version: str, system: str, runner_arch: str, sha256: str) -> dict[str, str]:
    return {
        "schema": "yue-flutter-sdk-v1",
        "version": version,
        "system": system,
        "runner_arch": runner_arch,
        "archive_sha256": sha256,
    }


def valid_cached_sdk(target: Path, expected: dict[str, str], system: str) -> bool:
    executable = target / "bin" / ("flutter.bat" if system == "Windows" else "flutter")
    record = target / RECEIPT_NAME
    if not executable.is_file() or not record.is_file():
        return False
    try:
        return json.loads(record.read_text(encoding="utf-8")) == expected
    except (OSError, json.JSONDecodeError):
        return False


def verify_runtime(target: Path, version: str, system: str) -> None:
    executable = target / "bin" / ("flutter.bat" if system == "Windows" else "flutter")
    result = subprocess.run(
        [str(executable), "--version", "--machine"],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        fail(f"Flutter version probe was not JSON: {exc}")
    if document.get("frameworkVersion") != version:
        fail(
            "Flutter runtime version mismatch: "
            f"got {document.get('frameworkVersion')!r}, expected {version!r}"
        )


def append_line(path_text: str | None, value: str) -> None:
    if not path_text:
        fail("required GitHub Actions output path is absent")
    with Path(path_text).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(value + "\n")


def main() -> int:
    version = os.environ.get("YUE_FLUTTER_VERSION", "")
    system = platform.system()
    runner_os = os.environ.get("YUE_FLUTTER_RUNNER_OS", "")
    runner_arch = os.environ.get("YUE_FLUTTER_RUNNER_ARCH", "")
    tool_cache_text = os.environ.get("YUE_FLUTTER_TOOL_CACHE", "")
    if not version or not runner_os or not runner_arch or not tool_cache_text:
        fail("version or runner identity is absent")
    tool_cache = Path(tool_cache_text).resolve()
    release = RELEASES.get((version, system))
    if release is None:
        fail(f"unsupported release tuple {(version, system)!r}")
    archive_name, archive_sha256 = release
    expected = receipt(version, system, runner_arch, archive_sha256)
    target = tool_cache / "yue-flutter" / version / f"{runner_os}-{runner_arch}"
    target.parent.mkdir(parents=True, exist_ok=True)

    if not valid_cached_sdk(target, expected, system):
        staging = Path(tempfile.mkdtemp(prefix=".flutter-sdk-", dir=target.parent))
        archive = staging / Path(archive_name).name
        try:
            download(BASE_URL + archive_name, archive, archive_sha256)
            extracted = staging / "extracted"
            extracted.mkdir()
            extract(archive, extracted, system)
            sdk = extracted / "flutter"
            executable = sdk / "bin" / (
                "flutter.bat" if system == "Windows" else "flutter"
            )
            if not executable.is_file():
                fail("official archive did not contain flutter/bin/flutter")
            (sdk / RECEIPT_NAME).write_text(
                json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            displaced: Path | None = None
            if target.exists():
                displaced = target.with_name(f"{target.name}.stale-{uuid.uuid4().hex}")
                os.replace(target, displaced)
            try:
                os.replace(sdk, target)
            except Exception:
                if displaced is not None and not target.exists():
                    os.replace(displaced, target)
                raise
            if displaced is not None:
                shutil.rmtree(displaced)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    verify_runtime(target, version, system)
    append_line(os.environ.get("GITHUB_PATH"), str(target / "bin"))
    append_line(os.environ.get("GITHUB_OUTPUT"), f"flutter-root={target}")
    print(f"Flutter {version} verified at {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
