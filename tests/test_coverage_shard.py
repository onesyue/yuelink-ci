"""Shard selection and join checks. These do not launch Flutter."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


def load_runner():
    path = Path(__file__).resolve().parents[1] / "scripts/run-flutter-coverage.py"
    spec = importlib.util.spec_from_file_location("coverage_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_stitch():
    path = Path(__file__).resolve().parents[1] / "scripts/stitch-coverage-shards.py"
    spec = importlib.util.spec_from_file_location("coverage_stitch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ShardSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()
        cls.stitch = load_stitch()

    def test_four_shards_cover_every_group_once(self):
        groups = [[f"test/t{index}_test.dart"] for index in range(10)]
        seen = []
        for index in range(4):
            shard = self.runner.parse_job_shard(f"{index}/4")
            seen.extend(item[0] for item in self.runner.select_shard(groups, shard))
        self.assertEqual(sorted(seen), list(range(10)))

    def test_empty_shard_is_the_whole_corpus(self):
        groups = [["test/a_test.dart"], ["test/b_test.dart"]]
        self.assertIsNone(self.runner.parse_job_shard(""))
        self.assertEqual(
            [index for index, _ in self.runner.select_shard(groups, None)],
            [0, 1],
        )

    def test_malformed_shard_is_refused(self):
        for value in ("4/4", "1/0", "a/2", "1/2/3", "-1/2"):
            with self.assertRaises(ValueError):
                self.runner.parse_job_shard(value)

    def test_weight_isolates_the_group_that_timed_out_at_300s(self):
        """Run 43065754953 group 53 was these eight domain files.

        The second half (43 declarations) hit the 300s budget, then the
        two-file splits finished. Packing by declaration count keeps the
        heavy files alone so the runner does not spend that dead wait.
        """
        specs = {
            "notice_window_test.dart": 2,
            "paused_subscription_20260926_test.dart": 4,
            "proxy_group_icon_test.dart": 1,
            "quota_reminder_test.dart": 5,
            "store_order_balance_20260926_test.dart": 8,
            "traffic_log_test.dart": 1,
            "unlimited_and_expiry_test.dart": 21,
            "withdrawal_test.dart": 13,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inventory = []
            for name, count in specs.items():
                path = root / "test/domain" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                body = "\n".join(f"  test('t{index}', () {{}});" for index in range(count))
                path.write_text(body + "\n", encoding="utf-8")
                inventory.append([str(path.relative_to(root))])
            packed = self.runner.directory_groups(inventory)
            weighted = self.runner.directory_groups(inventory, root)
        self.assertEqual(len(packed), 1)
        self.assertEqual(len(packed[0]), 8)
        alone = {
            "test/domain/unlimited_and_expiry_test.dart",
            "test/domain/withdrawal_test.dart",
            "test/domain/store_order_balance_20260926_test.dart",
        }
        self.assertTrue(all([path] in weighted for path in alone))
        self.assertFalse(any(len(group) > 4 for group in weighted))

    def test_missing_shard_is_not_a_green_corpus(self):
        groups = [["test/a_test.dart"], ["test/b_test.dart"]]
        manifests = [{
            "schema": 1,
            "shard": 0,
            "count": 2,
            "indexes": [0],
            "files": ["test/a_test.dart"],
            "observed": 3,
        }]
        with self.assertRaises(SystemExit) as raised:
            self.stitch.planned_observation(manifests, groups)
        self.assertIn("missing a shard", str(raised.exception))

    def test_complete_shards_sum_the_observed_tests(self):
        groups = [["test/a_test.dart"], ["test/b_test.dart"]]
        manifests = [
            {"shard": 0, "count": 2, "indexes": [0], "files": ["test/a_test.dart"], "observed": 4},
            {"shard": 1, "count": 2, "indexes": [1], "files": ["test/b_test.dart"], "observed": 5},
        ]
        self.assertEqual(self.stitch.planned_observation(manifests, groups), 9)

    def test_join_refuses_an_empty_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(SystemExit) as raised:
                self.stitch.manifests_from(Path(temporary))
        self.assertIn("no shard manifest", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
