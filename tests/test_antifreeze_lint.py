"""The anti-freeze lint must see this tree and reject a new pin."""

from __future__ import annotations

import unittest
from pathlib import Path

import scripts.antifreeze_lint as lint

ROOT = Path(lint.__file__).resolve().parents[1]


class AntifreezeLintTests(unittest.TestCase):
    def test_live_tree_matches_the_baseline(self) -> None:
        self.assertEqual(lint.issues(ROOT), [])
        self.assertGreaterEqual(len(lint.test_sources(ROOT)), lint.MIN_TEST_FILES)

    def test_missing_baseline_is_not_a_pass(self) -> None:
        missing = ROOT / "does-not-exist-antifreeze"
        with self.assertRaises(FileNotFoundError):
            lint.read_baseline(missing)

    def test_repo_file_hash_is_an_offender(self) -> None:
        digest = "ab" * 32
        hits = lint.text_hits(
            "tests/test_new.py",
            f"PIN = '{digest}'\n",
            {digest: "scripts/antifreeze_lint.py"},
            {},
        )
        self.assertEqual(
            hits,
            {"repo-hash tests/test_new.py scripts/antifreeze_lint.py"},
        )

    def test_external_digest_is_not_an_offender(self) -> None:
        hits = lint.text_hits("tests/test_new.py", "PIN = '" + ("cd" * 32) + "'\n", {}, {})
        self.assertEqual(hits, set())

    def test_line_sha_tuple_is_an_offender(self) -> None:
        # The pieces stay split in this file so the live scan does not pin itself.
        text = "(131, " + "'0b5096da')"
        hits = lint.text_hits("tests/test_new.py", text + "\n", {}, {})
        self.assertIn("line-sha tests/test_new.py", hits)

    def test_count_and_sha_pair_is_an_offender(self) -> None:
        text = "line_" + "count = 131\nsha = '" + "0b5096da'\n"
        hits = lint.text_hits("tests/test_new.py", text, {}, {})
        self.assertIn("line-sha tests/test_new.py", hits)

    def test_inventory_length_equality_is_an_offender(self) -> None:
        text = "assert len(WORKFLOWS) " + "== 12\n"
        hits = lint.text_hits("tests/test_new.py", text, {}, {})
        self.assertIn("inventory-len tests/test_new.py", hits)
        floor = lint.text_hits(
            "tests/test_new.py",
            "assert len(WORKFLOWS) >= 8\n",
            {},
            {},
        )
        self.assertEqual(floor, set())

    def test_file_line_reference_is_an_offender(self) -> None:
        text = "pin = 'build.yml:" + "2196'\n"
        hits = lint.text_hits("tests/test_new.py", text, {}, {})
        self.assertTrue(any(item.startswith("file-line ") for item in hits))

    def test_exact_directory_listing_is_an_offender(self) -> None:
        names = ["a.yml", "b.yml", "c.yml", "d.yml"]
        text = "EXPECTED = {" + ", ".join(repr(name) for name in names) + "}\n"
        full = lint.text_hits(
            "tests/test_new.py",
            text,
            {},
            {".github/workflows": set(names)},
        )
        self.assertIn("path-list tests/test_new.py .github/workflows", full)
        partial = lint.text_hits(
            "tests/test_new.py",
            "names = {'a.yml', 'b.yml'}\n",
            {},
            {".github/workflows": set(names)},
        )
        self.assertEqual(partial, set())

    def test_compare_rejects_a_new_or_grown_baseline(self) -> None:
        self.assertTrue(lint.compare({"new"}, set(), None))
        self.assertTrue(lint.compare(set(), {"stale"}, None))
        self.assertTrue(lint.compare(set(), {"added"}, set()))
        self.assertEqual(lint.compare(set(), set(), None), [])
        self.assertEqual(lint.compare({"kept"}, {"kept"}, {"kept", "older"}), [])


if __name__ == "__main__":
    unittest.main()
