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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SIGNATURE_DOMAIN = b"yueto:update-manifest:v1\n"
CURRENT_KEY_ID = "update-manifest-2026-01"
CURRENT_PUBLIC_KEY_B64 = "cE530BJZ2rpNcn5bNAduC/uaCfgU6JLJoGZdYV7uypE="
# RFC 8410 SubjectPublicKeyInfo prefix for a 32-byte Ed25519 raw public key.
ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")
TOP_LEVEL_KEYS = {
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
    if set(manifest) != TOP_LEVEL_KEYS:
        raise ManifestError("manifest top-level schema is not exact")
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


def _published_at(raw: Any) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise ManifestError("publishedAt must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise ManifestError("publishedAt is not a valid timestamp") from exc
    if parsed.tzinfo != timezone.utc:
        raise ManifestError("publishedAt must use UTC")
    return parsed


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

    manifest = _verify_signed_manifest(raw)
    try:
        floor_raw = minimum_raw if minimum_raw is not None else MINIMUM_MANIFEST.read_bytes()
    except OSError as exc:
        raise ManifestError("reviewed minimum manifest is unavailable") from exc
    floor = _verify_signed_manifest(floor_raw)
    _enforce_minimum(manifest, floor)
    return manifest


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} MANIFEST", file=sys.stderr)
        return 2
    try:
        manifest = verify(Path(sys.argv[1]).read_bytes())
    except (OSError, ManifestError) as exc:
        print(f"FATAL: untrusted YueLink update manifest: {exc}", file=sys.stderr)
        return 2
    print(
        "verified YueLink update manifest "
        f"version={manifest['version']} keyId={manifest['keyId']} "
        f"minimum={MINIMUM_MANIFEST.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
