import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/backup-critical-workstation-keys.yml"
BUILDER = ROOT / "scripts/build-keyvault-workstation-archive.sh"


class KeyVaultWorkstationBackupContractTest(unittest.TestCase):
    def test_manual_public_job_is_least_privilege_and_pinned(self) -> None:
        text = WORKFLOW.read_text()
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("contents: read", text)
        self.assertNotIn("pull_request", text)
        self.assertIn("timeout-minutes: 15", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn(
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            text,
        )

    def test_archive_is_ciphertext_only_and_survives_r2_failure(self) -> None:
        text = WORKFLOW.read_text()
        artifact = text.index("Retain encrypted recovery artifact")
        r2 = text.index("Round-trip encrypted archive through R2")
        self.assertLess(artifact, r2)
        self.assertIn("yueops-keyvault-workstation.tar.age", text)
        self.assertIn("s3://yueops-backup/critical-keys/", text)
        self.assertIn('cmp "$out" "$RUNNER_TEMP/roundtrip.tar.age"', text)
        self.assertNotIn("payload/", text)

    def test_all_private_inputs_are_env_only_and_never_printed(self) -> None:
        workflow = WORKFLOW.read_text()
        builder = BUILDER.read_text()
        for name in (
            "KEYSTORE_BASE64",
            "KEYSTORE_PASSWORD",
            "KEY_ALIAS",
            "KEY_PASSWORD",
            "UPDATE_MANIFEST_ED25519_PRIVATE_KEY_B64",
        ):
            self.assertIn(f"secrets.{name}", workflow)
        self.assertNotIn("set -x", builder)
        self.assertNotIn("-storepass ", builder)
        self.assertNotIn("-keypass ", builder)
        self.assertIn("-srcstorepass:env KEYSTORE_PASSWORD", builder)
        self.assertIn("-srckeypass:env KEY_PASSWORD", builder)

    def test_signing_identities_and_archive_shape_are_closed(self) -> None:
        workflow = WORKFLOW.read_text()
        builder = BUILDER.read_text()
        self.assertIn("vars.UPDATE_MANIFEST_ED25519_PUBLIC_KEY_B64", workflow)
        self.assertIn(
            "2b117c57ef715f3adb7aa4226a8d23de9a6607eff9f0d3f4df2dbdaa069148cc",
            builder,
        )
        self.assertIn("keytool -exportcert", builder)
        self.assertIn("openssl pkey -inform DER", builder)
        for slot in (
            "yuelink-android-keystore",
            "yuelink-updater-seed",
            "yuelink-keystore-credentials",
        ):
            self.assertIn(slot, builder)
        self.assertIn("#archive-version\\t1", builder)
        self.assertIn("#set\\tworkstation", builder)
        self.assertIn("MANIFEST payload", builder)
        self.assertIn('age -r "$breakglass" -r "$verifier"', builder)


if __name__ == "__main__":
    unittest.main()
