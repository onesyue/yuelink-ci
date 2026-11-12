"""CI generates translations after each locked pub get.

The check counts commands. It does not pin a workflow hash or a line number.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    ".github/workflows/build.yml",
    ".github/workflows/source-attestation.yml",
)


class SlangGenerationTests(unittest.TestCase):
    def test_every_locked_pub_get_runs_slang(self) -> None:
        for relative in WORKFLOWS:
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertEqual(
                text.count("flutter pub get --enforce-lockfile"),
                text.count("dart run slang"),
                relative,
            )
            self.assertIn("dart run slang", text)


if __name__ == "__main__":
    unittest.main()
