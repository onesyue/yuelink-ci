"""CI generates translations after each locked pub get.

The check counts commands. It does not pin a workflow hash or a line number.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github/workflows"


class SlangGenerationTests(unittest.TestCase):
    def test_every_locked_pub_get_runs_slang(self) -> None:
        files = sorted(WORKFLOW_DIR.glob("*.yml"))
        self.assertTrue(files)
        locked = 0
        for path in files:
            text = path.read_text(encoding="utf-8")
            pub_gets = text.count("flutter pub get --enforce-lockfile")
            slang = text.count("dart run slang")
            self.assertEqual(pub_gets, slang, path.name)
            if pub_gets:
                self.assertIn("dart run slang", text)
            locked += pub_gets
        self.assertGreater(locked, 0)


if __name__ == "__main__":
    unittest.main()
