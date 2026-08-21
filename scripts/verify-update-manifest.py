#!/usr/bin/env python3
"""Verify the YueLink stable update manifest against its pinned Ed25519 key.

The trust anchor is intentionally repository-local and is never discovered
from the network.  Key rotation must update this file and the YueLink client
in the same reviewed release train.
"""

from __future__ import annotations

import base64
import binascii
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SIGNATURE_DOMAIN = b"yueto:update-manifest:v1\n"
CURRENT_KEY_ID = "update-manifest-2026-01"
CURRENT_PUBLIC_KEY_B64 = "cE530BJZ2rpNcn5bNAduC/uaCfgU6JLJoGZdYV7uypE="
# RFC 8410 SubjectPublicKeyInfo prefix for a 32-byte Ed25519 raw public key.
ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")
REQUIRED_TOP_LEVEL_KEYS = {
    "schemaVersion",
    "keyId",
    "version",
    "channel",
    "publishedAt",
    "expiresAt",
    "releaseUrl",
    "notes",
    "platforms",
    "sig",
}
# 显式登记的可选顶层键。
#
# 这里必须是**白名单**而不是「随便多什么都行」：这个检查的价值在于「未知顶层字段
# 一律拒绝」，那是防止有人往已签名的清单里塞东西。放开成任意扩展等于把这条看门狗
# 拆了。
#
# 但也不能维持成精确集合。2026-08-19 全项目国际化：`notes` 是**单一中文** Markdown
# 串，被应用内更新弹窗逐字渲染——英文用户每次更新都看到中文发版说明，而这是 R2 独家
# 分发渠道，任何应用内 i18n 都够不着它。加 `notesEn` 是唯一的修法。
#
# 🚨 与之配套的两条硬约束（都是历史踩过的坑）：
#   1. **不许动 schemaVersion。** 客户端 update_manifest_verifier.dart 对
#      `schemaVersion != 1` 是硬拒绝的；为了「标记现在有多语言了」而把它 +1，会让
#      **所有已装机的版本从此停止更新**，而且表现是静默的「已是最新版本」。
#   2. 客户端那侧对已签名清单用的是**子集**校验（它自己的注释写明了：精确相等会让
#      每一个存量安装拒收清单）。所以加键对老客户端是安全的；不安全的是这里——
#      这个精确集合会让公开的 manifest-health 夜巡当场变红。
#
# 新增可选键时：加进这个集合、更新 tests/test_update_manifest_verifier.py 的
# test_unknown_top_level_key_is_rejected，并确认客户端 _candidateTopLevelKeys 也认它。
# `sourceCommit` 与晋级证据来自新稳定根；真实旧根没有这些键，因此仍是可选，
# 但 policy 会要求三键全有或全无并严格交叉绑定，不能把“前向兼容”误写成任意扩展。
OPTIONAL_TOP_LEVEL_KEYS = {
    "notesEn",
    "sourceCommit",
    "sha256SumsSha256",
    "promotionEvidence",
}
TOP_LEVEL_KEYS = REQUIRED_TOP_LEVEL_KEYS | OPTIONAL_TOP_LEVEL_KEYS
EXPECTED_PLATFORM_ARTIFACTS = {
    "android-arm64-v8a": "android-arm64-v8a.apk",
    "android-armeabi-v7a": "android-armeabi-v7a.apk",
    "android-x86_64": "android-x86_64.apk",
    "android-universal": "android-universal.apk",
    "ios": "ios.ipa",
    "macos-universal": "macos-universal.dmg",
    "windows-amd64-setup": "windows-amd64-setup.exe",
    "windows-amd64-portable": "windows-amd64-portable.zip",
    "linux-amd64-appimage": "linux-amd64.AppImage",
}
ASSET_HOST = "yuetong.app"
RELEASE_URL = "https://yue.to/download.html"
MAX_MANIFEST_LIFETIME = timedelta(days=45)
MAX_PUBLISH_CLOCK_SKEW = timedelta(hours=6)
MINIMUM_MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "tests/fixtures/update-manifest-v1.json"
)


class ManifestError(ValueError):
    """The manifest is ambiguous or is not signed by the pinned key."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _canonical_json(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_canonical_json(item) for item in value) + "]"
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return "{" + ",".join(
            f"{_canonical_json(key)}:{_canonical_json(value[key])}"
            for key in sorted(value)
        ) + "}"
    raise ManifestError(f"unsupported canonical JSON value: {type(value).__name__}")


def _verify_signed_manifest(raw: bytes) -> dict[str, Any]:
    try:
        decoded = raw.decode("utf-8")
        manifest = json.loads(decoded, object_pairs_hook=_reject_duplicate_keys)
    except UnicodeDecodeError as exc:
        raise ManifestError("manifest is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError("manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise ManifestError("manifest must be a JSON object")
    present = set(manifest)
    missing = REQUIRED_TOP_LEVEL_KEYS - present
    if missing:
        raise ManifestError(
            "manifest top-level schema is missing required keys: "
            + ", ".join(sorted(missing))
        )
    unknown = present - TOP_LEVEL_KEYS
    if unknown:
        raise ManifestError(
            "manifest has unknown top-level keys: " + ", ".join(sorted(unknown))
        )
    if manifest.get("keyId") != CURRENT_KEY_ID:
        raise ManifestError("manifest signing key is not trusted")

    signature_wire = manifest.get("sig")
    if not isinstance(signature_wire, str) or not signature_wire.startswith(
        "ed25519:"
    ):
        raise ManifestError("manifest signature is missing")
    try:
        signature = base64.b64decode(
            signature_wire.removeprefix("ed25519:"), validate=True
        )
        public_key = base64.b64decode(CURRENT_PUBLIC_KEY_B64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ManifestError("manifest signature encoding is invalid") from exc
    if len(signature) != 64 or len(public_key) != 32:
        raise ManifestError("manifest Ed25519 key or signature length is invalid")

    payload = dict(manifest)
    payload.pop("sig")
    signed_bytes = SIGNATURE_DOMAIN + _canonical_json(payload).encode("utf-8")
    with tempfile.TemporaryDirectory(prefix="yuelink-manifest-") as tmp:
        tmp_path = Path(tmp)
        public_path = tmp_path / "public.der"
        signature_path = tmp_path / "signature.bin"
        payload_path = tmp_path / "payload.bin"
        public_path.write_bytes(ED25519_SPKI_PREFIX + public_key)
        signature_path.write_bytes(signature)
        payload_path.write_bytes(signed_bytes)
        completed = subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-verify",
                "-pubin",
                "-keyform",
                "DER",
                "-inkey",
                str(public_path),
                "-rawin",
                "-in",
                str(payload_path),
                "-sigfile",
                str(signature_path),
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    if completed.returncode != 0:
        raise ManifestError("manifest signature is invalid")
    return manifest


def _stable_version(raw: Any) -> tuple[int, int, int]:
    if not isinstance(raw, str):
        raise ManifestError("stable manifest version must be X.Y.Z")
    parts = raw.split(".")
    if (
        len(parts) != 3
        or any(not part.isascii() or not part.isdigit() for part in parts)
        or any(len(part) > 1 and part.startswith("0") for part in parts)
    ):
        raise ManifestError("stable manifest version must be canonical X.Y.Z")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def _utc_timestamp(raw: Any, field: str) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise ManifestError(f"{field} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise ManifestError(f"{field} is not a valid timestamp") from exc
    if parsed.tzinfo != timezone.utc:
        raise ManifestError(f"{field} must use UTC")
    return parsed


def _published_at(raw: Any) -> datetime:
    return _utc_timestamp(raw, "publishedAt")


def _validate_manifest_policy(
    manifest: dict[str, Any], *, now: datetime | None = None
) -> None:
    if manifest.get("schemaVersion") != 1 or isinstance(
        manifest.get("schemaVersion"), bool
    ):
        raise ManifestError("schemaVersion must be integer 1")
    if manifest.get("channel") != "stable":
        raise ManifestError("channel must be stable")
    version = manifest.get("version")
    _stable_version(version)
    if manifest.get("releaseUrl") != RELEASE_URL:
        raise ManifestError("releaseUrl is outside the reviewed release page")
    if not isinstance(manifest.get("notes"), str) or not manifest["notes"].strip():
        raise ManifestError("notes must be a non-empty string")
    if "notesEn" in manifest and (
        not isinstance(manifest["notesEn"], str) or not manifest["notesEn"].strip()
    ):
        raise ManifestError("notesEn must be a non-empty string when present")

    evidence_keys = {"sourceCommit", "sha256SumsSha256", "promotionEvidence"}
    present_evidence_keys = set(manifest) & evidence_keys
    if present_evidence_keys and present_evidence_keys != evidence_keys:
        raise ManifestError(
            "sourceCommit, sha256SumsSha256, and promotionEvidence "
            "must appear as one complete bundle"
        )

    source_commit = manifest["sourceCommit"] if present_evidence_keys else None
    if present_evidence_keys and (
        not isinstance(source_commit, str)
        or len(source_commit) != 40
        or any(character not in "0123456789abcdef" for character in source_commit)
    ):
        raise ManifestError("sourceCommit must be a canonical lowercase Git SHA-1")

    sums_digest = manifest["sha256SumsSha256"] if present_evidence_keys else None
    if present_evidence_keys and (
        not isinstance(sums_digest, str)
        or len(sums_digest) != 64
        or any(character not in "0123456789abcdef" for character in sums_digest)
    ):
        raise ManifestError("sha256SumsSha256 must be a canonical lowercase SHA-256")

    promotion_evidence = (
        manifest["promotionEvidence"] if present_evidence_keys else None
    )
    if present_evidence_keys:
        if not isinstance(promotion_evidence, dict) or set(promotion_evidence) != {
            "schemaVersion",
            "candidateSha256",
            "sha256SumsSha256",
            "payloadBundleSha256",
            "payloadCount",
        }:
            raise ManifestError("promotionEvidence must use the reviewed exact schema")
        if promotion_evidence.get("schemaVersion") != 1 or isinstance(
            promotion_evidence.get("schemaVersion"), bool
        ):
            raise ManifestError("promotionEvidence schemaVersion must be integer 1")
        for field in (
            "candidateSha256",
            "sha256SumsSha256",
            "payloadBundleSha256",
        ):
            digest = promotion_evidence.get(field)
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ManifestError(
                    f"promotionEvidence {field} must be a canonical lowercase SHA-256"
                )
        if promotion_evidence["sha256SumsSha256"] != sums_digest:
            raise ManifestError(
                "promotionEvidence sha256SumsSha256 must match the signed top-level digest"
            )
        payload_count = promotion_evidence.get("payloadCount")
        if payload_count != 19 or isinstance(payload_count, bool):
            raise ManifestError("promotionEvidence payloadCount must be integer 19")

    published = _utc_timestamp(manifest.get("publishedAt"), "publishedAt")
    expires = _utc_timestamp(manifest.get("expiresAt"), "expiresAt")
    if expires <= published:
        raise ManifestError("expiresAt must be later than publishedAt")
    if expires - published > MAX_MANIFEST_LIFETIME:
        raise ManifestError("manifest lifetime exceeds 45 days")
    effective_now = now or datetime.now(timezone.utc)
    if published > effective_now + MAX_PUBLISH_CLOCK_SKEW:
        raise ManifestError("publishedAt is implausibly far in the future")

    platforms = manifest.get("platforms")
    if not isinstance(platforms, dict) or set(platforms) != set(
        EXPECTED_PLATFORM_ARTIFACTS
    ):
        raise ManifestError("platforms must contain exactly the reviewed 9 targets")
    for platform, artifact_name in EXPECTED_PLATFORM_ARTIFACTS.items():
        asset = platforms[platform]
        if not isinstance(asset, dict) or set(asset) != {"url", "sha256"}:
            raise ManifestError(f"platform {platform} must contain only url and sha256")
        digest = asset.get("sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)
        ):
            raise ManifestError(f"platform {platform} has invalid SHA-256")
        expected_path = f"/v{version}/YueLink-{version}-{artifact_name}"
        url = asset.get("url")
        if not isinstance(url, str):
            raise ManifestError(f"platform {platform} URL is missing")
        if url != f"https://{ASSET_HOST}{expected_path}":
            raise ManifestError(f"platform {platform} URL is outside the reviewed CDN path")


def _enforce_minimum(manifest: dict[str, Any], floor: dict[str, Any]) -> None:
    candidate_version = _stable_version(manifest.get("version"))
    floor_version = _stable_version(floor.get("version"))
    candidate_published = _published_at(manifest.get("publishedAt"))
    floor_published = _published_at(floor.get("publishedAt"))
    if candidate_version < floor_version:
        raise ManifestError("manifest version is below the reviewed minimum")
    if candidate_published < floor_published:
        raise ManifestError("manifest publishedAt is below the reviewed minimum")


def verify(raw: bytes, *, minimum_raw: bytes | None = None) -> dict[str, Any]:
    """Verify signature/schema and reject roots older than the reviewed floor.

    The private signing plane already forbids a decreasing root version.  This
    repository-local, signed floor gives the independent public watchdog the
    same fail-closed property against replay of a still-unexpired old root.
    Updating the floor is an explicit reviewed release change; it is never
    learned from the CDN being authenticated.
    """

    manifest = verify_historical_archive(raw)
    try:
        floor_raw = minimum_raw if minimum_raw is not None else MINIMUM_MANIFEST.read_bytes()
    except OSError as exc:
        raise ManifestError("reviewed minimum manifest is unavailable") from exc
    floor = _verify_signed_manifest(floor_raw)
    _enforce_minimum(manifest, floor)
    return manifest


def verify_historical_archive(raw: bytes) -> dict[str, Any]:
    """Verify an immutable archived root without applying today's replay floor.

    Rollback archives are expected to become older than the live updater's
    reviewed minimum. They still must be authentic roots from the pinned
    Ed25519 key and satisfy the complete manifest policy before an R2 pruning
    decision may treat their installer prefix as recoverable.
    """

    manifest = _verify_signed_manifest(raw)
    _validate_manifest_policy(manifest)
    return manifest


def main() -> int:
    args = sys.argv[1:]
    historical_archive = False
    if args[:1] == ["--historical-archive"]:
        historical_archive = True
        args = args[1:]
    if len(args) != 1:
        print(
            f"usage: {Path(sys.argv[0]).name} [--historical-archive] MANIFEST",
            file=sys.stderr,
        )
        return 2
    try:
        raw = Path(args[0]).read_bytes()
        manifest = (
            verify_historical_archive(raw) if historical_archive else verify(raw)
        )
    except (OSError, ManifestError) as exc:
        print(f"FATAL: untrusted YueLink update manifest: {exc}", file=sys.stderr)
        return 2
    policy = (
        "historical-archive"
        if historical_archive
        else f"minimum={MINIMUM_MANIFEST.name}"
    )
    print(
        "verified YueLink update manifest "
        f"version={manifest['version']} keyId={manifest['keyId']} "
        f"policy={policy}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
