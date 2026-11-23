import hashlib
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/backup-critical-workstation-keys.yml"
BUILDER = ROOT / "scripts/build-keyvault-workstation-archive.sh"
CERT_DIGEST = "2b117c57ef715f3adb7aa4226a8d23de9a6607eff9f0d3f4df2dbdaa069148cc"


class KeyVaultWorkstationBackupContractTest(unittest.TestCase):
    def test_age_checksum_failure_prevents_extraction_and_path_update(self) -> None:
        step = WORKFLOW.read_text().split("      - name: Install age\n", 1)[1]
        script = textwrap.dedent(step.split("        run: |\n", 1)[1].split("      - name:", 1)[0])
        with tempfile.TemporaryDirectory(prefix="backup tools ") as directory:
            root = Path(directory)
            curl = root / "curl"
            curl.write_text(
                '#!/bin/sh\nset -eu\nwhile [ "$1" != "--output" ]; do shift; done\n'
                'printf "fixture download" > "$2"\nprintf "%s" "$3" > "$RUNNER_TEMP/url"\n'
            )
            tar = root / "tar"
            tar.write_text('#!/bin/sh\ntouch "$RUNNER_TEMP/extracted"\nexit 1\n')
            for tool in (curl, tar):
                tool.chmod(0o755)
            env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}", RUNNER_TEMP=directory,
                       GITHUB_PATH=str(root / "path"))
            result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("age.tar.gz: FAILED", result.stdout)
            self.assertEqual((root / "url").read_text(),
                             "https://github.com/FiloSottile/age/releases/download/v1.3.2/age-v1.3.2-linux-amd64.tar.gz")
            self.assertFalse((root / "extracted").exists())
            self.assertFalse((root / "path").exists())
            self.assertEqual(list(root.glob("backup-age.*")), [])

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
            "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
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
            "CLIENT_CONFIG_ED25519_PRIVATE_KEY_B64",
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
        self.assertIn("vars.CLIENT_CONFIG_ED25519_PUBLIC_KEY_B64", workflow)
        self.assertIn(
            CERT_DIGEST,
            builder,
        )
        self.assertIn("keytool -exportcert", builder)
        self.assertIn("openssl pkey -inform DER", builder)
        for slot in (
            "yuelink-android-keystore",
            "yuelink-updater-seed",
            "yuelink-client-config-seed",
            "yuelink-keystore-credentials",
        ):
            self.assertIn(slot, builder)
        self.assertIn("#archive-version\\t1", builder)
        self.assertIn("#set\\tworkstation", builder)
        self.assertIn("MANIFEST payload", builder)
        self.assertIn('age -r "$breakglass" -r "$verifier"', builder)

    def test_archive_receipt_and_cleanup_on_command_failure_or_signal(self) -> None:
        with tempfile.TemporaryDirectory(prefix="backup fixture ") as directory:
            root = Path(directory)
            tools = {
                "keytool": 'printf fixture',
                "openssl": 'printf fake-public',
                "base64": 'cat',
                "sha256sum": """
                    if [ "$#" -eq 0 ]; then cat >/dev/null; echo "$CERT_DIGEST  -"; exit; fi
                    if [ "$1" = "$ARCHIVE" ] && [ "$FAIL" = hash ]; then exit 41; fi
                    exec "$REAL_HASH" "$@"
                """,
                "stat": """
                    if [ "$3" = "$ARCHIVE" ] && [ "$FAIL" = stat ]; then exit 42; fi
                    wc -c < "$3" | tr -d ' '
                """,
                "age": """
                    if [ "$FAIL" = signal ]; then kill -TERM "$PPID"; exit 0; fi
                    printf 'fixture ciphertext' > "$6"
                """,
                "date": 'if [ "$FAIL" = date ]; then exit 43; fi\nprintf fixture-date',
            }
            for name, body in tools.items():
                command = root / name
                command.write_text("#!/bin/sh\n" + textwrap.dedent(body) + "\n")
                command.chmod(0o755)
            archive = root / "fixture.age"
            real_hash = shutil.which("sha256sum")
            assert real_hash is not None, "sha256sum is required by the backup builder"
            env = {
                "PATH": f"{root}:{os.environ['PATH']}", "RUNNER_TEMP": directory,
                "CERT_DIGEST": CERT_DIGEST, "REAL_HASH": real_hash,
                "ARCHIVE": str(archive), "KEYSTORE_BASE64": "fake", "KEY_ALIAS": "fake",
                "KEYSTORE_PASSWORD": "fake", "KEY_PASSWORD": "fake",
                "YUEOPS_KEYVAULT_BREAKGLASS_RECIPIENT": "age1" + "a" * 58,
                "YUEOPS_KEYVAULT_VERIFIER_RECIPIENT": "age1" + "b" * 58,
            }
            for prefix in ("UPDATE_MANIFEST", "CLIENT_CONFIG"):
                env[prefix + "_ED25519_PRIVATE_KEY_B64"] = "fake" * 8
                env[prefix + "_ED25519_PUBLIC_KEY_B64"] = "fake-public"
            for failure, code in (("none", 0), ("hash", 41), ("stat", 42),
                                  ("date", 43), ("signal", 143)):
                with self.subTest(failure=failure):
                    archive.unlink(missing_ok=True)
                    result = subprocess.run(["bash", str(BUILDER), str(archive)],
                                            env=dict(env, FAIL=failure), capture_output=True, text=True)
                    self.assertEqual(result.returncode, code, result.stderr)
                    self.assertEqual(list(root.glob("yuelink-keyvault.*")), [])
                    if code:
                        self.assertNotIn("archive ready", result.stdout)
                    else:
                        data = archive.read_bytes()
                        self.assertIn(f"sha256={hashlib.sha256(data).hexdigest()} bytes={len(data)}",
                                      result.stdout)


if __name__ == "__main__":
    unittest.main()
