"""Force-overlay anchors. The real transform must fail when the helper moves."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "scripts/apply-update-force-overlay.py"
WORKFLOW = ROOT / ".github/workflows/build.yml"
SYNC = ROOT / "sync-build.sh"


def load():
    spec = importlib.util.spec_from_file_location("apply_update_force_overlay", OVERLAY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OVERLAY_MODULE = load()


def wrapper(
    env: str, comment: str, call: str, *, helper_after_sign: bool = False
) -> str:
    step = (
        "      - name: Generate updater manifest candidate\n"
        "        env:\n"
        f"{env}"
        "        run: |\n"
        "          set -euo pipefail\n"
        f"{comment}"
        f"{call}\n"
        "          echo done\n"
        "\n"
        "      - name: Attach reviewed observation policy\n"
        "        run: echo observe\n"
    )
    sign = "      - name: Sign immutable release archive\n        run: echo sign\n"
    gate = "      needs.dial_gate.result == 'success' &&\n"
    if helper_after_sign:
        return gate + sign + step
    return gate + step + sign


class ForceOverlayTest(unittest.TestCase):
    def test_known_profiles_become_canonical_and_keep_the_dial_gate(self) -> None:
        for env, comment in (
            (OVERLAY_MODULE.PROFILE_A, OVERLAY_MODULE.COMMENT_A),
            (OVERLAY_MODULE.PROFILE_B, OVERLAY_MODULE.COMMENT_B),
        ):
            source = wrapper(env, comment, OVERLAY_MODULE.CALL_OLD)
            updated = OVERLAY_MODULE.transform(source)
            self.assertIn(OVERLAY_MODULE.PROFILE_C, updated)
            self.assertIn(OVERLAY_MODULE.CALL_NEW, updated)
            self.assertIn(OVERLAY_MODULE.COMMENT_C, updated)
            self.assertNotIn("apply-update-rollout.py", updated)
            self.assertNotIn("YUELINK_UPDATE_ROLLOUT_", updated)
            self.assertIn("needs.dial_gate.result == 'success'", updated)
            self.assertEqual(OVERLAY_MODULE.transform(updated), updated)

    def test_canonical_profile_is_unchanged(self) -> None:
        source = wrapper(
            OVERLAY_MODULE.PROFILE_C,
            OVERLAY_MODULE.COMMENT_C,
            OVERLAY_MODULE.CALL_NEW,
        )
        self.assertEqual(OVERLAY_MODULE.transform(source), source)

    def test_bad_anchors_fail_closed(self) -> None:
        commented = wrapper(
            OVERLAY_MODULE.PROFILE_C,
            OVERLAY_MODULE.COMMENT_C,
            "          # " + OVERLAY_MODULE.CALL_NEW.replace("          ", "", 1),
        )
        mixed = wrapper(
            OVERLAY_MODULE.PROFILE_C + OVERLAY_MODULE.ENV_SALT,
            OVERLAY_MODULE.COMMENT_C,
            OVERLAY_MODULE.CALL_NEW,
        )
        partial = wrapper(
            OVERLAY_MODULE.PROFILE_C,
            OVERLAY_MODULE.COMMENT_C,
            OVERLAY_MODULE.CALL_OLD,
        )
        duplicated = wrapper(
            OVERLAY_MODULE.PROFILE_B,
            OVERLAY_MODULE.COMMENT_B,
            OVERLAY_MODULE.CALL_OLD,
        ).replace(
            "      - name: Generate updater manifest candidate\n",
            "      - name: Generate updater manifest candidate\n",
            1,
        ) + wrapper(
            OVERLAY_MODULE.PROFILE_B,
            OVERLAY_MODULE.COMMENT_B,
            OVERLAY_MODULE.CALL_OLD,
        )
        late = wrapper(
            OVERLAY_MODULE.PROFILE_B,
            OVERLAY_MODULE.COMMENT_B,
            OVERLAY_MODULE.CALL_OLD,
            helper_after_sign=True,
        )
        for source in (commented, mixed, partial, duplicated, late, "no step\n"):
            with self.subTest(source=source[:80]), self.assertRaises(ValueError):
                OVERLAY_MODULE.transform(source)

    def test_executable_call_must_be_unique_and_outside_heredocs(self) -> None:
        source = WORKFLOW.read_text()
        variants = [
            source
            + "\n      - name: extra policy\n        run: |\n"
            + OVERLAY_MODULE.CALL_NEW
            + "\n",
            source.replace(
                OVERLAY_MODULE.CALL_NEW,
                "          bash <<'FORCE'\n"
                + OVERLAY_MODULE.CALL_NEW
                + "\n          FORCE",
            ),
            source.replace(
                OVERLAY_MODULE.CALL_NEW,
                OVERLAY_MODULE.CALL_NEW + "\n" + OVERLAY_MODULE.CALL_NEW,
            ),
            source.replace(
                OVERLAY_MODULE.CALL_NEW, OVERLAY_MODULE.CALL_NEW + " --force soft"
            ),
            source.replace(
                OVERLAY_MODULE.CALL_NEW, "          # " + OVERLAY_MODULE.CALL_NEW
            ),
            source.replace(
                OVERLAY_MODULE.PROFILE_C,
                OVERLAY_MODULE.PROFILE_C + OVERLAY_MODULE.ENV_PLATFORMS,
            ),
        ]
        for index, changed in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(ValueError):
                OVERLAY_MODULE.transform(changed)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "build.yml"
                path.write_text(changed)
                result = subprocess.run(
                    [sys.executable, str(OVERLAY), str(path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(path.read_text(), changed)

    def test_helper_outside_named_run_cannot_satisfy_anchor(self) -> None:
        source = wrapper(
            OVERLAY_MODULE.PROFILE_C, OVERLAY_MODULE.COMMENT_C, OVERLAY_MODULE.CALL_NEW
        )
        changed = source.replace("        run: |\n", "        description: |\n", 1)
        with self.assertRaises(ValueError):
            OVERLAY_MODULE.transform(changed)

    def test_unrelated_receipt_heredoc_remains_accepted(self) -> None:
        source = WORKFLOW.read_text()
        receipt = "\n      - name: receipt fixture\n        run: |\n          python3 - <<'RECEIPT'\n          print('fixture')\n          RECEIPT\n"
        self.assertEqual(OVERLAY_MODULE.transform(source + receipt), source + receipt)

    def test_checked_in_workflow_is_canonical_and_sync_calls_the_overlay(self) -> None:
        text = WORKFLOW.read_text()
        self.assertEqual(OVERLAY_MODULE.transform(text), text)
        self.assertEqual(text.count(OVERLAY_MODULE.CALL_NEW), 1)
        self.assertNotIn("apply-update-rollout.py", text)
        self.assertIn(OVERLAY_MODULE.ENV_PLATFORM, text)
        self.assertIn(OVERLAY_MODULE.ENV_PLATFORMS, text)
        self.assertIn("needs.dial_gate.result == 'success'", text)
        self.assertIn(
            'python3 "$SCRIPT_DIR/scripts/apply-update-force-overlay.py" "$GENERATED"',
            SYNC.read_text(),
        )


if __name__ == "__main__":
    unittest.main()
