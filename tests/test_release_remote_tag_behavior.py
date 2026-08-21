from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release.sh"
TAG = "v9.8.7"
TAG_OBJECT = "7" * 40
BUILDER = "b" * 40
SOURCE = "a" * 40
RUN = "12345"
MESSAGE = (
    f"YueLink public builder {TAG}\n\n"
    f"ATTESTED_SOURCE_COMMIT={SOURCE}\n"
    f"SOURCE_ATTESTATION_RUN_ID={RUN}"
)
SIGNATURE = (
    "-----BEGIN PGP SIGNATURE-----\n\n"
    "iQEzBAABCAAdFiEEfixturefixturefixturefixturefixturefixture\n"
    "=ABCD\n"
    "-----END PGP SIGNATURE-----\n"
)


def shell_function(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    end = source.index("\n}\n", start) + len("\n}\n")
    return source[start:end]


class ReleaseRemoteTagBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("jq") is None:
            self.fail("jq is required by release.sh and its behavior tests")
        release = RELEASE.read_text(encoding="utf-8")
        self.inspector = shell_function(release, "inspect_remote_public_tag")

    def run_inspector(
        self,
        *,
        message: str = MESSAGE,
        signature: str | None = SIGNATURE,
        verified: bool = True,
        reason: str = "valid",
        tag: str = TAG,
        builder: str = BUILDER,
        object_type: str = "commit",
        ref_type: str = "tag",
    ) -> subprocess.CompletedProcess[str]:
        ref_json = json.dumps(
            {"object": {"sha": TAG_OBJECT, "type": ref_type}},
            separators=(",", ":"),
        )
        verification: dict[str, object] = {
            "verified": verified,
            "reason": reason,
        }
        if signature is not None:
            verification["signature"] = signature
        tag_json = json.dumps(
            {
                "tag": tag,
                "object": {"sha": builder, "type": object_type},
                "message": message,
                "verification": verification,
            },
            separators=(",", ":"),
        )
        script = (
            "set -u\n"
            + self.inspector
            + "\n"
            + "gh() {\n"
            + "  case \"$*\" in\n"
            + "    *git/ref/tags/*) printf '%s' \"$FAKE_REF_JSON\" ;;\n"
            + "    *git/tags/*) printf '%s' \"$FAKE_TAG_JSON\" ;;\n"
            + "    *) return 99 ;;\n"
            + "  esac\n"
            + "}\n"
            + "inspect_remote_public_tag\n"
        )
        return subprocess.run(
            ["/bin/bash", "-c", script],
            env={
                **os.environ,
                "TAG": TAG,
                "BUILDER_COMMIT": BUILDER,
                "PUBLIC_TAG_MESSAGE": MESSAGE,
                "FAKE_REF_JSON": ref_json,
                "FAKE_TAG_JSON": tag_json,
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_exact_body_without_api_signature_suffix_is_accepted(self) -> None:
        result = self.run_inspector(message=MESSAGE, signature=None)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_exact_body_plus_verified_api_signature_is_accepted(self) -> None:
        result = self.run_inspector(message=MESSAGE + "\n" + SIGNATURE)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_stale_or_extended_binding_is_rejected_even_with_signature(self) -> None:
        messages = (
            MESSAGE.replace(f"SOURCE_ATTESTATION_RUN_ID={RUN}", "SOURCE_ATTESTATION_RUN_ID=9"),
            MESSAGE + "\nUNEXPECTED=1",
            MESSAGE + "\n\n" + SIGNATURE,
            MESSAGE + "\n" + SIGNATURE + "UNEXPECTED",
        )
        for message in messages:
            with self.subTest(message=message):
                result = self.run_inspector(message=message)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn("exact source/attestation message", result.stdout)

    def test_suffix_must_equal_one_well_formed_api_signature(self) -> None:
        malformed = (
            "not an armored signature",
            SIGNATURE + SIGNATURE,
            SIGNATURE.replace("-----END PGP SIGNATURE-----", "-----END OTHER-----"),
            SIGNATURE.replace("\n", "\r\n"),
        )
        for signature in malformed:
            with self.subTest(signature=signature):
                result = self.run_inspector(
                    message=MESSAGE + "\n" + signature,
                    signature=signature,
                )
                self.assertEqual(result.returncode, 2, result.stdout)

        different = SIGNATURE.replace("=ABCD", "=WXYZ")
        result = self.run_inspector(
            message=MESSAGE + "\n" + different,
            signature=SIGNATURE,
        )
        self.assertEqual(result.returncode, 2, result.stdout)

    def test_github_verified_valid_remains_mandatory(self) -> None:
        for verified, reason in ((False, "unsigned"), (True, "unknown_key")):
            with self.subTest(verified=verified, reason=reason):
                result = self.run_inspector(
                    message=MESSAGE + "\n" + SIGNATURE,
                    verified=verified,
                    reason=reason,
                )
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("尚未 valid", result.stdout)

    def test_exact_tag_object_and_builder_remain_mandatory(self) -> None:
        mutations = (
            {"tag": "v9.8.6"},
            {"builder": "c" * 40},
            {"object_type": "tag"},
            {"ref_type": "commit"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                result = self.run_inspector(
                    message=MESSAGE + "\n" + SIGNATURE,
                    **mutation,
                )
                self.assertEqual(result.returncode, 2, result.stdout)


if __name__ == "__main__":
    unittest.main()
