"""Durable provenance cannot silently fall back or overwrite release evidence."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "preserve", ROOT / "scripts/preserve-source-proof.py"
)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
BINDING = {
    "tag": "v1.3.176",
    "source": "a" * 40,
    "builder": "b" * 40,
    "attestation_run": "123",
}


class DurableProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_durable_pair_verifies_exact_bytes(self):
        pair = {
            name: {"id": i, "size": 4}
            for i, name in enumerate((mod.PROOF, mod.BUNDLE), 1)
        }
        with (
            patch.object(mod, "assets", return_value=pair),
            patch.object(mod, "read", return_value=b"data"),
            patch.object(mod, "verify") as verify,
        ):
            self.assertEqual(mod.acquire(BINDING, 9, self.directory), "release-assets")
        verify.assert_called_once_with(
            BINDING, self.directory / mod.PROOF, self.directory / mod.BUNDLE
        )
        self.assertEqual((self.directory / mod.PROOF).read_bytes(), b"data")

    def test_partial_or_invalid_assets_never_fall_back(self):
        for pair in (
            {mod.PROOF: {"id": 1, "size": 4}},
            {
                name: {"id": i, "size": 4}
                for i, name in enumerate((mod.PROOF, mod.BUNDLE), 1)
            },
        ):
            with (
                self.subTest(pair=pair),
                patch.object(mod, "assets", return_value=pair),
                patch.object(mod, "read", return_value=b"data"),
                patch.object(
                    mod, "verify", side_effect=ValueError("bad signature")
                ) as verify,
                patch.object(mod.subprocess, "run") as run,
            ):
                with self.assertRaises(ValueError):
                    mod.acquire(BINDING, 9, self.directory)
                run.assert_not_called()
                self.assertLessEqual(verify.call_count, 1)

    def test_archive_survives_expired_artifact_without_download(self):
        archive = self.directory / "archive"
        tag = archive / BINDING["tag"]
        tag.mkdir(parents=True)
        for name in (mod.PROOF, mod.BUNDLE):
            (tag / name).write_bytes(b"original signed bytes")
        with (
            patch.object(mod, "ARCHIVE", archive),
            patch.object(mod, "assets", return_value={}),
            patch.object(mod, "verify") as verify,
            patch.object(mod.subprocess, "run") as run,
        ):
            self.assertEqual(
                mod.acquire(BINDING, 9, self.directory), "signed-migration-archive"
            )
            verify.assert_called_once_with(
                BINDING, self.directory / mod.PROOF, self.directory / mod.BUNDLE
            )
            run.assert_not_called()

    def test_incomplete_archive_fails_closed(self):
        archive = self.directory / "archive"
        (archive / BINDING["tag"]).mkdir(parents=True)
        with (
            patch.object(mod, "ARCHIVE", archive),
            patch.object(mod, "assets", return_value={}),
            self.assertRaisesRegex(ValueError, "incomplete migration"),
        ):
            mod.acquire(BINDING, 9, self.directory)

    def test_bundle_download_timeout_is_bounded_and_propagates(self):
        with (
            patch.object(mod, "ARCHIVE", self.directory / "absent"),
            patch.object(mod, "assets", return_value={}),
            patch.object(mod, "verify"),
            patch.object(
                mod.subprocess,
                "run",
                side_effect=mod.subprocess.TimeoutExpired("gh", 150),
            ) as run,
            self.assertRaises(mod.subprocess.TimeoutExpired),
        ):
            mod.acquire(BINDING, 9, self.directory)
        self.assertEqual(run.call_args.kwargs["timeout"], 150)

    def test_read_only_never_uploads(self):
        with (
            patch.object(mod, "acquire", return_value="signed-migration-archive"),
            patch.object(mod.subprocess, "run") as run,
        ):
            mod.preserve(BINDING, 9, self.directory, False)
            run.assert_not_called()

    def test_publication_no_clobber_and_readback_required(self):
        for name in (mod.PROOF, mod.BUNDLE):
            (self.directory / name).write_bytes(b"data")
        with (
            patch.object(
                mod, "acquire", side_effect=["actions-artifact", "actions-artifact"]
            ),
            patch.object(mod.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "absent after"):
                mod.preserve(BINDING, 9, self.directory, True)
            args = run.call_args.args[0]
            self.assertEqual(args[:4], ["gh", "release", "upload", BINDING["tag"]])
            self.assertNotIn("--clobber", args)

    def test_existing_durable_pair_does_not_upload(self):
        with (
            patch.object(mod, "acquire", return_value="release-assets"),
            patch.object(mod.subprocess, "run") as run,
        ):
            mod.preserve(BINDING, 9, self.directory, True)
            run.assert_not_called()

    def test_duplicate_asset_and_unfinished_upload_rejected(self):
        for values in (
            [{"id": 1, "name": mod.PROOF, "state": "new"}],
            [
                {"id": 1, "name": mod.PROOF, "state": "uploaded"},
                {"id": 2, "name": mod.PROOF, "state": "uploaded"},
            ],
        ):
            with (
                self.subTest(values=values),
                patch.object(mod, "read", return_value=json.dumps(values).encode()),
                self.assertRaises(ValueError),
            ):
                mod.assets(9)

    def test_workflow_preserves_before_promotion(self):
        text = (ROOT / ".github/workflows/publish.yml").read_text()
        self.assertLess(
            text.index("Preserve verified source proof before promotion"),
            text.index("- name: Sign root manifest and promote"),
        )
        self.assertIn('"$ATTESTATION_RUN" --publish', text)


if __name__ == "__main__":
    unittest.main()
