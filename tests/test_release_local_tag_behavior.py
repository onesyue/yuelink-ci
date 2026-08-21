from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release.sh"
TAG = "v9.8.7"
SOURCE = "a" * 40
RUN = "12345"
MESSAGE = (
    f"YueLink public builder {TAG}\n\n"
    f"ATTESTED_SOURCE_COMMIT={SOURCE}\n"
    f"SOURCE_ATTESTATION_RUN_ID={RUN}"
)


def local_inspector(source: str) -> str:
    start = source.index("inspect_local_public_tag() {")
    end = source.index("\n}\n", start) + len("\n}\n")
    return source[start:end]


class ReleaseLocalTagBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        gpg = shutil.which("gpg")
        if gpg is None:
            raise AssertionError("gpg is required for real signed-tag behavior tests")
        cls._gpg_home_owner = tempfile.TemporaryDirectory()
        cls.gpg_home = Path(cls._gpg_home_owner.name) / "gnupg"
        cls.gpg_home.mkdir(mode=0o700)
        cls.gpg = gpg
        cls.gpg_env = {**os.environ, "GNUPGHOME": str(cls.gpg_home)}
        subprocess.run(
            [
                gpg,
                "--batch",
                "--pinentry-mode",
                "loopback",
                "--passphrase",
                "",
                "--quick-generate-key",
                "YueLink Release Test <release-test@example.invalid>",
                "ed25519",
                "sign",
                "0",
            ],
            env=cls.gpg_env,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        listing = subprocess.run(
            [gpg, "--batch", "--with-colons", "--list-secret-keys"],
            env=cls.gpg_env,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout
        cls.signing_fingerprint = next(
            line.split(":")[9]
            for line in listing.splitlines()
            if line.startswith("fpr:")
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._gpg_home_owner.cleanup()

    def setUp(self) -> None:
        self.release = RELEASE.read_text(encoding="utf-8")
        self.inspector = local_inspector(self.release)

    def run_inspector(
        self, *, raw_annotation: str | None = None, lightweight: bool = False
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            real_git = shutil.which("git")
            if real_git is None:
                self.fail("git is required")
            subprocess.run([real_git, "init", "-q", str(repo)], check=True)
            subprocess.run(
                [real_git, "-C", str(repo), "config", "user.name", "Release Test"],
                check=True,
            )
            subprocess.run(
                [real_git, "-C", str(repo), "config", "user.email", "release@example.invalid"],
                check=True,
            )
            # Never inherit the developer machine's global commit.gpgsign/key.
            # The fixture commit is merely the peeled builder identity; only
            # the annotated release tag is intentionally signed below.
            subprocess.run(
                [real_git, "-C", str(repo), "config", "commit.gpgsign", "false"],
                check=True,
            )
            subprocess.run(
                [
                    real_git,
                    "-C",
                    str(repo),
                    "config",
                    "user.signingkey",
                    self.signing_fingerprint,
                ],
                check=True,
            )
            subprocess.run(
                [real_git, "-C", str(repo), "config", "gpg.program", self.gpg],
                check=True,
            )
            (repo / "fixture.txt").write_text("builder\n", encoding="utf-8")
            subprocess.run([real_git, "-C", str(repo), "add", "fixture.txt"], check=True)
            subprocess.run(
                [real_git, "-C", str(repo), "commit", "-q", "-m", "builder"],
                check=True,
            )
            builder = subprocess.run(
                [real_git, "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            if lightweight:
                subprocess.run([real_git, "-C", str(repo), "tag", TAG], check=True)
            elif raw_annotation is None:
                subprocess.run(
                    [real_git, "-C", str(repo), "tag", "-s", "-m", MESSAGE, TAG],
                    env=self.gpg_env,
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            else:
                subprocess.run(
                    [
                        real_git,
                        "-C",
                        str(repo),
                        "tag",
                        "-s",
                        "--cleanup=verbatim",
                        "-F",
                        "-",
                        TAG,
                    ],
                    input=raw_annotation,
                    env=self.gpg_env,
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                subprocess.run(
                    [real_git, "-C", str(repo), "verify-tag", TAG],
                    env=self.gpg_env,
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            script = self.inspector + "\ninspect_local_public_tag\n"
            return subprocess.run(
                ["/bin/bash", "-c", script],
                cwd=repo,
                env={
                    **self.gpg_env,
                    "TAG": TAG,
                    "BUILDER_COMMIT": builder,
                    "PUBLIC_TAG_MESSAGE": MESSAGE,
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

    def test_exact_annotated_message_is_accepted(self) -> None:
        result = self.run_inspector()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_same_builder_with_stale_run_binding_is_rejected(self) -> None:
        stale = MESSAGE.replace("SOURCE_ATTESTATION_RUN_ID=12345", "SOURCE_ATTESTATION_RUN_ID=99999")
        result = self.run_inspector(raw_annotation=stale + "\n")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("annotation", result.stdout)
        self.assertIn("绝不 push", result.stdout)

    def test_real_signed_tag_with_extra_trailing_lf_is_rejected(self) -> None:
        result = self.run_inspector(raw_annotation=MESSAGE + "\n\n")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("annotation", result.stdout)

    def test_real_signed_tag_with_missing_blank_line_is_rejected(self) -> None:
        result = self.run_inspector(
            raw_annotation=MESSAGE.replace("\n\n", "\n", 1) + "\n"
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("annotation", result.stdout)

    def test_real_signed_tag_with_extra_content_is_rejected(self) -> None:
        result = self.run_inspector(raw_annotation=MESSAGE + "\nUNEXPECTED=1\n")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("annotation", result.stdout)

    def test_lightweight_local_tag_is_rejected(self) -> None:
        result = self.run_inspector(lightweight=True)
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_prerelease_is_rejected_before_any_remote_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            marker = Path(tmp) / "remote-command-ran"
            fake_gh = fake_bin / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\nprintf ran > \"$REMOTE_MARKER\"\nexit 99\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            result = subprocess.run(
                ["/bin/bash", str(RELEASE), "v9.8.7-pre.1"],
                env={
                    **os.environ,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "REMOTE_MARKER": str(marker),
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("prerelease", result.stdout)
            self.assertFalse(marker.exists(), result.stdout)

    def test_inspection_is_unconditionally_before_public_push(self) -> None:
        creation = self.release.index('git tag -s -m "$PUBLIC_TAG_MESSAGE"')
        inspection = self.release.index("  inspect_local_public_tag\n", creation)
        push = self.release.index(
            '  if ! git push origin "refs/tags/$TAG:refs/tags/$TAG"; then',
            creation,
        )
        self.assertLess(creation, inspection)
        self.assertLess(inspection, push)


if __name__ == "__main__":
    unittest.main()
