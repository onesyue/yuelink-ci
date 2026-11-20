"""policy-ci rejects the retired Telegram bot on first-party builder paths.

The official handle is @yuetoo_bot (t1218u). Needles are built by
concatenation so this test can name them without storing the retired text.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.check_bot_handle as gate

ROOT = Path(__file__).resolve().parents[1]
HANDLE = "yue" + "_to_bot"
URL = "t.me/" + HANDLE


def write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def allowed_tree() -> dict[str, str]:
    kept = "\n".join((
        "@yuetoo_bot",
        "https://t.me/yuetoo_bot",
        "https://t.me/yue_to",
        "https://t.me/yueto_news",
    )) + "\n"
    return {
        ".github/workflows/build.yml": kept,
        "scripts/ok.py": kept,
        "policy/client-support-policy.json": "{}\n",
        "docs/RUNBOOK.md": kept,
        "tests/test_ok.py": "import unittest\n",
        "README.md": "builder\n",
        "FINDINGS.md": "notes\n",
        "release.sh": "#!/bin/sh\n",
        "sync-build.sh": "#!/bin/sh\n",
    }


class BotHandleGateTests(unittest.TestCase):
    def test_live_tree_is_clean_and_reaches_the_builder(self) -> None:
        result = gate.scan(ROOT)
        self.assertEqual(gate.evaluate(result), [])
        self.assertGreaterEqual(result.count, gate.MIN_FILES)
        self.assertIn(".github/workflows/policy-ci.yml", result.paths)
        self.assertIn("release.sh", result.paths)
        self.assertIn("scripts/check_bot_handle.py", result.paths)
        self.assertNotIn(HANDLE, (ROOT / "scripts/check_bot_handle.py").read_text(encoding="utf-8"))
        self.assertNotIn(URL, (ROOT / "tests/test_bot_handle_gate.py").read_text(encoding="utf-8"))
        workflow = (ROOT / ".github/workflows/policy-ci.yml").read_text(encoding="utf-8")
        self.assertIn("python3 scripts/check_bot_handle.py", workflow)
        self.assertIn("python3 -m unittest discover -s tests -p 'test_*.py'", workflow)

    def test_official_handles_stay_allowed(self) -> None:
        self.assertEqual(gate.needle_labels(allowed_tree()["scripts/ok.py"]), ())

    def test_retired_handle_and_url_fail_on_their_own(self) -> None:
        cases = {
            "scripts/bare.py": (HANDLE + "\n", ("retired-handle",)),
            "scripts/url.py": ("see " + URL + "\n", ("retired-url", "retired-handle")),
            "docs/upper.md": (HANDLE.upper() + "\n", ("retired-handle",)),
            ".github/workflows/upper.yml": (URL.upper() + "\n", ("retired-url", "retired-handle")),
        }
        floor = len(allowed_tree()) + 1
        for rel, (text, labels) in cases.items():
            with self.subTest(rel=rel), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                files = allowed_tree()
                files[rel] = text
                write_tree(root, files)
                result = gate.scan(root)
                self.assertEqual(
                    gate.evaluate(result, min_files=floor),
                    [f"{rel}: {label}" for label in labels],
                )

    def test_excluded_history_is_neither_a_hit_nor_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = allowed_tree()
            files["tests/fixtures/legacy.json"] = URL + "\n"
            files["docs/release-proofs/v1/source-attestation.json"] = HANDLE + "\n"
            files["reviews/note.md"] = HANDLE + "\n"
            files["reports/note.md"] = URL + "\n"
            write_tree(root, files)
            result = gate.scan(root)
            self.assertEqual(gate.evaluate(result, min_files=len(allowed_tree())), [])
            self.assertEqual(result.count, len(allowed_tree()))
            self.assertFalse(any(
                "fixtures" in path or "release-proofs" in path for path in result.paths
            ))

        with tempfile.TemporaryDirectory() as tmp:
            write_tree(Path(tmp), {
                "tests/fixtures/legacy.json": "{}\n",
                "reviews/note.md": "ok\n",
            })
            empty = gate.scan(Path(tmp))
            self.assertEqual(empty.count, 0)
            self.assertIn("scanned only 0", gate.evaluate(empty)[0])

    def test_floor_rejects_a_short_walk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(Path(tmp), {"README.md": "@yuetoo_bot\n"})
            result = gate.scan(Path(tmp))
            self.assertEqual(result.hits, ())
            self.assertEqual(
                gate.evaluate(result),
                [f"scanned only 1 first-party file; need at least {gate.MIN_FILES}"],
            )

    def test_bad_encoding_symlink_and_unreadable_file_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside_dir:
            root = Path(tmp)
            write_tree(root, allowed_tree())
            outside = Path(outside_dir) / "outside.py"
            outside.write_text(HANDLE + "\n", encoding="utf-8")
            link = root / "scripts" / "linked.py"
            link.symlink_to(outside)
            binary = root / "scripts" / "binary.py"
            binary.write_bytes(b"\xff")
            blocked = root / "scripts" / "blocked.py"
            blocked.write_text("ok\n", encoding="utf-8")
            original = Path.read_text

            def deny(self: Path, *args, **kwargs):
                if self.name == "blocked.py":
                    raise OSError("denied")
                return original(self, *args, **kwargs)

            with mock.patch.object(Path, "read_text", deny):
                result = gate.scan(root)
            problems = gate.evaluate(result, min_files=len(allowed_tree()))
            self.assertIn("scripts/linked.py: symlink", problems)
            self.assertIn("scripts/binary.py: not utf-8", problems)
            self.assertIn("scripts/blocked.py: unreadable", problems)
            self.assertFalse(any(item.endswith("retired-handle") for item in problems))

    def test_cli_accepts_this_tree_and_rejects_a_planted_file(self) -> None:
        clean = subprocess.run(
            [sys.executable, str(ROOT / "scripts/check_bot_handle.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertIn("bot-handle gate: scanned ", clean.stdout)
        self.assertNotIn(HANDLE, clean.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            planted = Path(tmp)
            files = allowed_tree()
            files["scripts/bad.py"] = "open " + URL + "\n"
            write_tree(planted, files)
            below_floor = subprocess.run(
                [sys.executable, str(ROOT / "scripts/check_bot_handle.py"), str(planted)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(below_floor.returncode, 1)
        self.assertIn("scanned only", below_floor.stderr)
        self.assertIn("scripts/bad.py: retired-url", below_floor.stderr)
        self.assertIn("scripts/bad.py: retired-handle", below_floor.stderr)

        usage = subprocess.run(
            [sys.executable, str(ROOT / "scripts/check_bot_handle.py"), "unused", "extra"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(usage.returncode, 2)
        self.assertIn("usage:", usage.stderr)
        self.assertEqual(usage.stdout, "")


if __name__ == "__main__":
    unittest.main()
