"""The anti-freeze lint must see this tree and reject a new pin."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import scripts.antifreeze_lint as lint

ROOT = Path(lint.__file__).resolve().parents[1]


class AntifreezeLintTests(unittest.TestCase):
    def test_live_tree_matches_the_baseline(self) -> None:
        self.assertEqual(lint.issues(ROOT), [])
        self.assertGreaterEqual(len(lint.test_sources(ROOT)), lint.MIN_TEST_FILES)

    def test_discovery_adds_languages_and_prunes_nested_checkouts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wanted = [
                "tests/test_old.py",
                "test/model_test.dart",
                "scripts/test-gate.py",
                "internal/model_test.go",
                "web/login.test.ts",
                "web/login.spec.ts",
                "web/view.test.tsx",
                "web/view.spec.tsx",
            ]
            excluded = [
                ".lane/test_hidden.py",
                "web/node_modules/x.spec.ts",
                "vendor/x_test.go",
                "test/fixtures/test_sample.py",
                "core/mihomo/x_test.go",
                "nested/test_clone.py",
                "linked/test_worktree.py",
                "web/plain.ts",
            ]
            for relative in wanted + excluded:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("// test source\n")
            (root / "nested/.git").mkdir()
            (root / "linked/.git").write_text("gitdir: elsewhere\n")
            self.assertEqual(
                lint.test_sources(root), sorted(root / name for name in wanted)
            )

    def test_unreadable_directory_cannot_produce_a_partial_pass(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blocked = root / "unreadable"
            blocked.mkdir()
            for index in range(lint.MIN_TEST_FILES):
                (root / f"test_{index}.py").write_text("# test\n")
            original = lint.os.scandir

            def scandir(path):
                if Path(path) == blocked:
                    raise PermissionError(13, "permission denied", str(path))
                return original(path)

            with patch.object(lint.os, "scandir", side_effect=scandir):
                with self.assertRaisesRegex(RuntimeError, "cannot scan test directory"):
                    lint.scan(root)

    def test_existing_python_and_dart_population_is_preserved(self) -> None:
        # Derive the old discovery set; do not freeze a repository file count.
        previous = {
            path
            for path in ROOT.rglob("*")
            if path.is_file()
            and not lint._skipped(path.relative_to(ROOT).as_posix())
            and (
                (path.name.startswith("test_") and path.name.endswith(".py"))
                or path.name.endswith("_test.dart")
            )
            and ".lane" not in path.relative_to(ROOT).parts
        }
        current = set(lint.test_sources(ROOT))
        legacy = {
            path
            for path in current
            if (path.name.startswith("test_") and path.name.endswith(".py"))
            or path.name.endswith("_test.dart")
        }
        self.assertEqual(legacy, previous)
        self.assertEqual(lint.MIN_TEST_FILES, 8)

    def test_new_language_source_reaches_offender_scanner(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(lint.MIN_TEST_FILES):
                (root / f"case{index}_test.go").write_text("// test\n")
            target = root / "bad.spec.tsx"
            target.write_text("const pin = 'build.yml:" + "2196';\n")
            with patch.object(lint, "repo_index", return_value=({}, {})):
                self.assertTrue(
                    any(
                        hit.startswith("file-line bad.spec.tsx ")
                        for hit in lint.scan(root)
                    )
                )
            target.unlink()
            with patch.object(lint, "repo_index", return_value=({}, {})):
                self.assertEqual(lint.scan(root), set())

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
        hits = lint.text_hits(
            "tests/test_new.py", "PIN = '" + ("cd" * 32) + "'\n", {}, {}
        )
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


class AntifreezeCliTests(unittest.TestCase):
    def invoke(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", str(ROOT / "scripts/antifreeze_lint.py"), *args],
            cwd=cwd, capture_output=True, text=True, timeout=30, check=False,
        )

    def make_repository(self, root: Path) -> Path:
        root.mkdir()
        (root / "tests").mkdir()
        for index in range(lint.MIN_TEST_FILES):
            (root / "tests" / f"test_case_{index}.py").write_text(f"# case {index}\n")
        for index in range(lint.MIN_HASHED_FILES):
            (root / f"source_{index}.txt").write_text(f"source {index}\n")
        baseline = root / lint.BASELINE_NAME
        baseline.write_text("# shrink only\n")
        def git(*args: str) -> None:
            subprocess.run(
                ["git", "-C", str(root), *args], check=True,
                capture_output=True, text=True, timeout=15,
            )
        git("init", "--quiet")
        git("add", ".")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "fixture")
        git("update-ref", "refs/remotes/origin/master", "HEAD")
        return baseline

    def test_explicit_repository_is_scanned_from_an_unrelated_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            target = cwd / "target with spaces"
            baseline = self.make_repository(target)
            result = self.invoke(cwd, "--root", target.name)
            self.assertEqual(result.returncode, 0, result.stderr)
            pin = "workflow.yml" + ":123"
            (target / "tests/test_case_0.py").write_text(f"# {pin}\n")
            result = self.invoke(cwd, "--root", str(target))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("new anti-freeze offender", result.stderr)
            # The target's own merge-base must reject widening its baseline.
            baseline.write_text(f"file-line tests/test_case_0.py {pin}\n")
            result = self.invoke(cwd, "--root", str(target))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("anti-freeze baseline grew", result.stderr)

    def test_git_owned_projection_preserves_dirty_inputs_and_target_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            target = cwd / "consumer"
            baseline = self.make_repository(target)
            (target / ".gitignore").write_text("scratch/\n")
            ignored = target / "scratch/test_generated.py"
            ignored.parent.mkdir()
            pin = "workflow.yml" + ":123"
            ignored.write_text(f"# {pin}\n")
            # A plain recursive scan sees the ignored artifact. The shared
            # Git-owned mode must exclude it without modifying the source.
            plain = self.invoke(cwd, "--root", str(target))
            self.assertNotEqual(plain.returncode, 0)
            self.assertIn("new anti-freeze offender", plain.stderr)
            def check(message: str | None = None) -> None:
                command = ["git", "-C", str(target), "status", "--porcelain=v1"]
                before = subprocess.check_output(command)
                result = self.invoke(cwd, "--root", str(target), "--git-owned")
                self.assertEqual(subprocess.check_output(command), before)
                if message is None:
                    self.assertEqual(result.returncode, 0, result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(message, result.stderr)
            check()
            from unittest.mock import patch
            with patch.dict("os.environ", {"GIT_DIR": str(cwd / "missing.git")}):
                isolated = self.invoke(cwd, "--root", str(target), "--git-owned")
            self.assertEqual(isolated.returncode, 0, isolated.stderr)
            link = target / "tests/test_link.py"
            link.symlink_to("test_case_0.py")
            check()
            link.unlink()
            link.symlink_to("../scratch/test_generated.py")
            check("source snapshot unavailable")
            link.unlink()
            added = target / "tests/test_new.py"
            added.write_text(f"# {pin}\n")
            check("new anti-freeze offender")
            added.unlink()
            tracked = target / "tests/test_case_0.py"
            tracked.write_text(f"# {pin}\n")
            check("new anti-freeze offender")
            baseline.write_text(f"file-line tests/test_case_0.py {pin}\n")
            check("anti-freeze baseline grew")
            baseline.write_text("# shrink only\n")
            tracked.unlink()
            check(f"scan saw {lint.MIN_TEST_FILES - 1} test files")
            subprocess.run(["git", "-C", str(target), "update-ref", "-d",
                            "refs/remotes/origin/master"], check=True, capture_output=True)
            check("source snapshot unavailable")

    def test_empty_or_missing_target_never_falls_back_to_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            for target, message in ((cwd, "scan saw 0 test files"),
                                    (cwd / "absent", "cannot scan test directory")):
                with self.subTest(target=target):
                    result = self.invoke(cwd, "--root", str(target))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(message, result.stderr)

    def test_default_still_scans_authority_independent_of_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self.invoke(Path(directory))
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
