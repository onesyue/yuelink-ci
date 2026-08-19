#!/usr/bin/env python3
"""Verify the independently signed YueLink release-page health envelope.

The probe intentionally runs outside GitHub's ASN because Cloudflare Bot Fight
Mode challenges GitHub-hosted runners.  GitHub trusts only the public key
committed with this verifier; the network-delivered envelope cannot introduce
a key or relax any policy field.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_KEY = ROOT / "security/yue-to-download-health-ed25519.pem"

ALGORITHM = "Ed25519"
CANONICALIZATION = "jq-cjS-v1"
PROBE_ID = "yue-to-download-v1"
RELEASE_URL = "https://yue.to/download.html"
PUBLIC_URL = "https://yuetong.app/health/yue-to-download.json"
TRUSTED_KEY_ID = (
    "ed25519:sha256:"
    "8cab652c26734789d428b6f1aab8827e461719a52ab038d9b70f48a5596f796d"
)
MAX_AGE_SECONDS = 15 * 60
MAX_FUTURE_SKEW_SECONDS = 2 * 60
MAX_ENVELOPE_BYTES = 64 * 1024
MIN_BODY_BYTES = 1024
MAX_BODY_BYTES = 1024 * 1024
MAX_LATENCY_MS = 20_000
ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")

TOP_LEVEL_KEYS = {
    "alg",
    "canonicalization",
    "keyId",
    "payload",
    "payloadSha256",
    "signature",
}
PAYLOAD_KEYS = {
    "schemaVersion",
    "probeId",
    "url",
    "finalUrl",
    "publicUrl",
    "checkedAt",
    "checkedAtUnix",
    "httpStatus",
    "bytes",
    "latencyMs",
    "contentSha256",
    "errorCode",
    "ok",
    "checks",
}
CHECK_KEYS = {
    "http200",
    "exactFinalUrl",
    "boundedBody",
    "canonical",
    "openGraph",
    "yueLink",
    "challengeNegative",
}
SHA256_RE = re.compile(r"[0-9a-f]{64}")
UTC_SECONDS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class ReleaseHealthError(ValueError):
    """The release-page health envelope is untrusted or unhealthy."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseHealthError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseHealthError(f"{label} must be a JSON object")
    present = set(value)
    if present != expected:
        missing = sorted(expected - present)
        unknown = sorted(present - expected)
        raise ReleaseHealthError(
            f"{label} keys are not exact (missing={missing}, unknown={unknown})"
        )
    return value


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReleaseHealthError(f"{label} must be an integer")
    return value


def _run(command: list[str], *, input_bytes: bytes | None = None) -> bytes:
    try:
        completed = subprocess.run(
            command,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ReleaseHealthError(f"required verifier unavailable: {command[0]}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise ReleaseHealthError(
            f"verifier command failed ({command[0]}): {detail or completed.returncode}"
        )
    return completed.stdout


def _public_key_fingerprint(public_key: Path) -> str:
    if not public_key.is_file():
        raise ReleaseHealthError(f"pinned public key is unavailable: {public_key}")
    der = _run(
        [
            "openssl",
            "pkey",
            "-pubin",
            "-in",
            str(public_key),
            "-outform",
            "DER",
        ]
    )
    if len(der) != len(ED25519_SPKI_PREFIX) + 32 or not der.startswith(
        ED25519_SPKI_PREFIX
    ):
        raise ReleaseHealthError("pinned public key is not an Ed25519 SubjectPublicKeyInfo")
    return hashlib.sha256(der).hexdigest()


def _canonical_payload(raw: bytes) -> bytes:
    canonical = _run(["jq", "-cjS", ".payload"], input_bytes=raw)
    if not canonical or canonical.endswith(b"\n"):
        raise ReleaseHealthError("jq canonical payload is empty or newline-terminated")
    return canonical


def _verify_signature(
    canonical: bytes, signature: bytes, public_key: Path
) -> None:
    with tempfile.TemporaryDirectory(prefix="yuelink-release-health-") as temp_dir:
        temp = Path(temp_dir)
        payload_path = temp / "payload.json"
        signature_path = temp / "payload.sig"
        payload_path.write_bytes(canonical)
        signature_path.write_bytes(signature)
        try:
            completed = subprocess.run(
                [
                    "openssl",
                    "pkeyutl",
                    "-verify",
                    "-pubin",
                    "-inkey",
                    str(public_key),
                    "-rawin",
                    "-in",
                    str(payload_path),
                    "-sigfile",
                    str(signature_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ReleaseHealthError("required verifier unavailable: openssl") from exc
    if completed.returncode != 0:
        raise ReleaseHealthError("release-page health Ed25519 signature is invalid")


def _checked_epoch(checked_at: Any) -> int:
    if not isinstance(checked_at, str) or not UTC_SECONDS_RE.fullmatch(checked_at):
        raise ReleaseHealthError("checkedAt must be canonical UTC seconds")
    try:
        parsed = datetime.strptime(checked_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ReleaseHealthError("checkedAt is not a real UTC timestamp") from exc
    return int(parsed.timestamp())


def _validate_policy(payload: dict[str, Any], *, now_unix: int) -> None:
    if _integer(payload["schemaVersion"], "schemaVersion") != 1:
        raise ReleaseHealthError("schemaVersion must be integer 1")
    if payload["probeId"] != PROBE_ID:
        raise ReleaseHealthError("probeId is not the reviewed probe identity")
    if payload["url"] != RELEASE_URL or payload["finalUrl"] != RELEASE_URL:
        raise ReleaseHealthError("release-page URL/finalUrl identity mismatch")
    if payload["publicUrl"] != PUBLIC_URL:
        raise ReleaseHealthError("publicUrl is not the reviewed envelope URL")

    checked_epoch = _checked_epoch(payload["checkedAt"])
    checked_unix = _integer(payload["checkedAtUnix"], "checkedAtUnix")
    if checked_epoch != checked_unix:
        raise ReleaseHealthError("checkedAt and checkedAtUnix disagree")
    age = now_unix - checked_unix
    if age > MAX_AGE_SECONDS:
        raise ReleaseHealthError(f"signed release-page health is stale ({age}s old)")
    if age < -MAX_FUTURE_SKEW_SECONDS:
        raise ReleaseHealthError(f"signed release-page health is too far in the future ({age}s)")

    if _integer(payload["httpStatus"], "httpStatus") != 200:
        raise ReleaseHealthError("release-page health did not observe HTTP 200")
    body_bytes = _integer(payload["bytes"], "bytes")
    if not MIN_BODY_BYTES <= body_bytes <= MAX_BODY_BYTES:
        raise ReleaseHealthError("release-page body size is outside reviewed bounds")
    latency = _integer(payload["latencyMs"], "latencyMs")
    if not 0 <= latency <= MAX_LATENCY_MS:
        raise ReleaseHealthError("release-page latency is outside reviewed bounds")
    digest = payload["contentSha256"]
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise ReleaseHealthError("contentSha256 is not canonical lowercase SHA-256")
    if payload["errorCode"] != "none":
        raise ReleaseHealthError("successful release-page health must have errorCode=none")
    if payload["ok"] is not True:
        raise ReleaseHealthError("signed release-page health reports failure")

    checks = _exact_keys(payload["checks"], CHECK_KEYS, "checks")
    if any(checks[name] is not True for name in CHECK_KEYS):
        raise ReleaseHealthError("every reviewed release-page identity check must pass")


def verify(
    envelope_path: Path,
    *,
    public_key: Path = DEFAULT_PUBLIC_KEY,
    now_unix: int | None = None,
) -> dict[str, Any]:
    try:
        raw = envelope_path.read_bytes()
    except OSError as exc:
        raise ReleaseHealthError(f"cannot read health envelope: {exc}") from exc
    if not 1 <= len(raw) <= MAX_ENVELOPE_BYTES:
        raise ReleaseHealthError("health envelope size is outside reviewed bounds")
    try:
        envelope = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except UnicodeDecodeError as exc:
        raise ReleaseHealthError("health envelope is not UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ReleaseHealthError("health envelope is not valid JSON") from exc
    envelope = _exact_keys(envelope, TOP_LEVEL_KEYS, "envelope")
    payload = _exact_keys(envelope["payload"], PAYLOAD_KEYS, "payload")
    _exact_keys(payload["checks"], CHECK_KEYS, "checks")

    if envelope["alg"] != ALGORITHM:
        raise ReleaseHealthError("health envelope algorithm is not Ed25519")
    if envelope["canonicalization"] != CANONICALIZATION:
        raise ReleaseHealthError("health envelope canonicalization is not trusted")

    fingerprint = _public_key_fingerprint(public_key)
    expected_key_id = f"ed25519:sha256:{fingerprint}"
    if expected_key_id != TRUSTED_KEY_ID:
        raise ReleaseHealthError("pinned public key does not match the reviewed anchor")
    if envelope["keyId"] != TRUSTED_KEY_ID:
        raise ReleaseHealthError("health envelope keyId does not match the pinned key")

    canonical = _canonical_payload(raw)
    payload_digest = hashlib.sha256(canonical).hexdigest()
    if envelope["payloadSha256"] != payload_digest:
        raise ReleaseHealthError("health envelope payloadSha256 is invalid")
    signature_wire = envelope["signature"]
    if not isinstance(signature_wire, str):
        raise ReleaseHealthError("health envelope signature must be base64 text")
    try:
        signature = base64.b64decode(signature_wire, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ReleaseHealthError("health envelope signature encoding is invalid") from exc
    if len(signature) != 64 or base64.b64encode(signature).decode("ascii") != signature_wire:
        raise ReleaseHealthError("health envelope signature is not canonical Ed25519 base64")
    _verify_signature(canonical, signature, public_key)

    if now_unix is None:
        now_unix = int(datetime.now(timezone.utc).timestamp())
    _validate_policy(payload, now_unix=_integer(now_unix, "now_unix"))
    return payload


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(
            f"usage: {Path(sys.argv[0]).name} ENVELOPE.json [PUBLIC-KEY.pem]",
            file=sys.stderr,
        )
        return 2
    envelope_path = Path(sys.argv[1])
    public_key = Path(sys.argv[2]) if len(sys.argv) == 3 else DEFAULT_PUBLIC_KEY
    try:
        payload = verify(envelope_path, public_key=public_key)
    except ReleaseHealthError as exc:
        print(f"FATAL: untrusted release-page health: {exc}", file=sys.stderr)
        return 1
    print(
        "verified signed release-page health "
        f"probeId={payload['probeId']} checkedAt={payload['checkedAt']} "
        f"HTTP={payload['httpStatus']} bytes={payload['bytes']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
