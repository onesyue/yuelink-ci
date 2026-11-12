from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "select_source_reuse_artifact",
    ROOT / "scripts/select-source-reuse-artifact.py",
)
selector = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(selector)


RUN = "43005486604"


def artifact(name: str, *, expired: object = False) -> dict[str, object]:
    return {"name": name, "expired": expired}


def pages(*items: dict[str, object], total: int | None = None) -> list[dict[str, object]]:
    return [{"total_count": len(items) if total is None else total, "artifacts": list(items)}]


class SelectionTests(unittest.TestCase):
    def test_rerun_keeps_highest_receipt_at_or_before_signing_attempt(self) -> None:
        listed = pages(
            artifact(f"source-reuse-flutter-{RUN}-1"),
            artifact(f"source-reuse-service-{RUN}-1"),
            artifact(f"source-reuse-flutter-{RUN}-2"),
            artifact(f"source-reuse-service-{RUN}-3", expired=False),
            artifact(f"source-reuse-flutter-999-2"),
            artifact(f"source-reuse-flutter-{RUN}-0"),
            artifact(f"source-reuse-flutter-{RUN}-02"),
            artifact("unrelated"),
        )
        # Attempt 3 is newer than the signing attempt and must not win.
        flutter, flutter_attempt = selector.select_artifact(
            selector.flatten_pages(listed),
            kind="flutter",
            run_id=RUN,
            current_attempt="2",
        )
        service, service_attempt = selector.select_artifact(
            selector.flatten_pages(listed),
            kind="service",
            run_id=RUN,
            current_attempt="2",
        )
        self.assertEqual((flutter, flutter_attempt), (f"source-reuse-flutter-{RUN}-2", "2"))
        self.assertEqual((service, service_attempt), (f"source-reuse-service-{RUN}-1", "1"))

    def test_expired_newer_receipt_falls_back_to_live_earlier_attempt(self) -> None:
        listed = pages(
            artifact(f"source-reuse-flutter-{RUN}-1"),
            artifact(f"source-reuse-flutter-{RUN}-2", expired=True),
            artifact(f"source-reuse-service-{RUN}-1"),
        )
        artifacts = selector.flatten_pages(listed)
        name, attempt = selector.select_artifact(
            artifacts, kind="flutter", run_id=RUN, current_attempt="2"
        )
        self.assertEqual((name, attempt), (f"source-reuse-flutter-{RUN}-1", "1"))

    def test_missing_expired_flag_duplicate_foreign_and_future_do_not_pass(self) -> None:
        missing_flag = pages({"name": f"source-reuse-flutter-{RUN}-1"})
        with self.assertRaises(selector.SelectionError):
            selector.select_artifact(
                selector.flatten_pages(missing_flag),
                kind="flutter",
                run_id=RUN,
                current_attempt="1",
            )
        duplicate = pages(
            artifact(f"source-reuse-flutter-{RUN}-1"),
            artifact(f"source-reuse-flutter-{RUN}-1"),
        )
        with self.assertRaises(selector.SelectionError):
            selector.select_artifact(
                selector.flatten_pages(duplicate),
                kind="flutter",
                run_id=RUN,
                current_attempt="2",
            )
        for current, names in (
            ("1", [f"source-reuse-flutter-{RUN}-2"]),
            ("2", [f"source-reuse-flutter-111-1"]),
            ("2", []),
        ):
            with self.subTest(current=current, names=names):
                with self.assertRaises(selector.SelectionError):
                    selector.select_artifact(
                        selector.flatten_pages(pages(*(artifact(name) for name in names))),
                        kind="flutter",
                        run_id=RUN,
                        current_attempt=current,
                    )

    def test_incomplete_or_shifted_pages_fail_before_selection(self) -> None:
        for payload in (
            [],
            [{"total_count": 2, "artifacts": [artifact("only-one")]}],
            [
                {"total_count": 1, "artifacts": [artifact("a")]},
                {"total_count": 2, "artifacts": []},
            ],
            [{"artifacts": []}],
            [{"total_count": "1", "artifacts": []}],
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(selector.SelectionError):
                    selector.flatten_pages(payload)

    def test_cli_writes_only_the_selected_pair_and_rejects_bad_inputs(self) -> None:
        payload = pages(
            artifact(f"source-reuse-flutter-{RUN}-1"),
            artifact(f"source-reuse-service-{RUN}-1"),
            artifact(f"source-reuse-flutter-{RUN}-4"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_file = root / "pages.json"
            output = root / "github-output.txt"
            page_file.write_text(json.dumps(payload), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/select-source-reuse-artifact.py"),
                    "--run-id",
                    RUN,
                    "--current-attempt",
                    "2",
                    "--pages",
                    str(page_file),
                    "--github-output",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "\n".join(
                    [
                        f"flutter_name=source-reuse-flutter-{RUN}-1",
                        "flutter_attempt=1",
                        f"service_name=source-reuse-service-{RUN}-1",
                        "service_attempt=1",
                        "",
                    ]
                ),
            )
            self.assertNotIn(f"{RUN}-4", output.read_text(encoding="utf-8"))
            for args in (
                ["--run-id", "0", "--current-attempt", "1", "--pages", str(page_file)],
                ["--run-id", RUN, "--current-attempt", "0", "--pages", str(page_file)],
                ["--run-id", "../other", "--current-attempt", "1", "--pages", str(page_file)],
            ):
                with self.subTest(args=args):
                    failed = subprocess.run(
                        [sys.executable, str(ROOT / "scripts/select-source-reuse-artifact.py"), *args],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    self.assertNotEqual(failed.returncode, 0)
                    self.assertIn("::error::", failed.stderr)

    def test_failed_artifact_api_does_not_write_a_receipt_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "github-output.txt"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            fake = bin_dir / "gh"
            fake.write_text(
                textwrap.dedent(
                    """\
                    #!/bin/sh
                    echo 'network down' >&2
                    exit 1
                    """
                ),
                encoding="utf-8",
            )
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/select-source-reuse-artifact.py"),
                    "--run-id",
                    RUN,
                    "--current-attempt",
                    "2",
                    "--github-output",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
                env={**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]},
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("::error::", completed.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
