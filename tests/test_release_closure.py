from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_closure", ROOT / "scripts/verify-release-closure.py"
)
assert SPEC and SPEC.loader
closure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(closure)


class ReleaseClosureTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(
            (ROOT / "tests/fixtures/update-manifest-v1.json").read_bytes()
        )
        prefix = f"YueLink-{self.manifest['version']}-"
        self.entries = {
            item["url"].rsplit("/", 1)[1]: item["sha256"]
            for item in self.manifest["platforms"].values()
        }
        self.entries.update(
            (f"{prefix}{platform}-{suffix}", hashlib.sha256(platform.encode()).hexdigest())
            for platform in ("android", "ios", "linux", "macos", "windows")
            for suffix in ("INSTALL-NOTICE.txt", "RELEASE.json")
        )

    def body(self, entries=None):
        return "".join(
            f"{digest}  {name}\n"
            for name, digest in (entries or self.entries).items()
        ).encode()

    def test_complete_filename_bound_closure_checks_every_payload(self):
        body = self.body()
        self.manifest["sha256SumsSha256"] = hashlib.sha256(body).hexdigest()
        with (
            mock.patch.object(closure, "_read_url", return_value=body),
            mock.patch.object(
                closure, "_sha256_of_url",
                side_effect=lambda url: (self.entries[url.rsplit("/", 1)[1]], 10),
            ) as payload,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(closure.verify_release(self.manifest), 0)
        self.assertEqual(payload.call_count, 19)

    def test_unsigned_or_tampered_manifest_cannot_start_network_work(self):
        self.manifest["sig"] = "ed25519:" + "A" * 88
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(self.manifest))
            with (
                mock.patch.object(closure.sys, "argv", ["verify", str(path)]),
                mock.patch.object(closure, "_read_url") as network,
                redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(closure.main(), 1)
            network.assert_not_called()

    def test_wrong_anchor_stops_before_any_payload_request(self):
        with (
            mock.patch.object(closure, "_read_url", return_value=self.body()),
            mock.patch.object(closure, "_sha256_of_url") as payload,
        ):
            with self.assertRaisesRegex(ValueError, "哈希锚"):
                closure.verify_release(self.manifest)
        payload.assert_not_called()

    def test_exchanged_digests_fail_even_when_hash_membership_is_unchanged(self):
        entries = copy.copy(self.entries)
        first, second = list(entries)[:2]
        entries[first], entries[second] = entries[second], entries[first]
        self.assertEqual(set(entries.values()), set(self.entries.values()))
        with self.assertRaisesRegex(ValueError, "filename/hash binding"):
            closure._parse_sums(self.body(entries), self.manifest)

    def test_duplicate_missing_extra_and_path_entries_are_rejected(self):
        first = next(iter(self.entries))
        mutations = [
            self.body() + self.body().splitlines(keepends=True)[0],
            self.body({k: v for k, v in self.entries.items() if k != first}),
            self.body() + b"a" * 64 + b"  extra.zip\n",
            self.body().replace(first.encode(), b"../payload.zip"),
        ]
        for body in mutations:
            with self.subTest(body=body[-100:]), self.assertRaises(ValueError):
                closure._parse_sums(body, self.manifest)

    def test_sum_download_is_bounded(self):
        with mock.patch.object(
            closure, "_open", return_value=io.BytesIO(b"x" * (closure.MAX_SUMS_BYTES + 1))
        ):
            with self.assertRaisesRegex(ValueError, "64 KiB"):
                closure._read_url("https://example.invalid/sums")


if __name__ == "__main__":
    unittest.main()
