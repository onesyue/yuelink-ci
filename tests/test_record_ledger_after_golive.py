from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/record-ledger-after-golive.sh"
RELEASE = ROOT / "release.sh"


class RecordLedgerAfterGoLive(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "yueto/scripts").mkdir(parents=True)
        self.marker = self.dir / "recorded"
        recorder = self.dir / "yueto/scripts/record-yuelink-ledger.sh"
        recorder.write_text(
            '#!/bin/bash\nprintf "%s" "$YUELINK_CI" > "$MARKER"\n'
            "exit \"${RECORDER_RC:-0}\"\n"
        )
        recorder.chmod(0o755)

    def run_script(self, live: str, **extra: str) -> subprocess.CompletedProcess[str]:
        manifest = self.dir / "update.json"
        manifest.write_text(json.dumps({"version": live}))
        env = {
            **os.environ,
            "YUETO_ROOT": str(self.dir / "yueto"),
            "YUELINK_CHECKOUT": str(self.dir),
            "LEDGER_MANIFEST_URL": manifest.as_uri(),
            "LEDGER_WAIT_SECONDS": "1",
            "LEDGER_POLL_SECONDS": "1",
            "MARKER": str(self.marker),
            **extra,
        }
        return subprocess.run(
            ["bash", str(SCRIPT), "1.3.205"], env=env, capture_output=True, text=True, timeout=30
        )

    def test_records_once_live_matches(self) -> None:
        ran = self.run_script("1.3.205")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual(self.marker.read_text(), str(ROOT))

    def test_stale_live_version_never_records_and_never_fails(self) -> None:
        ran = self.run_script("1.3.204")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertFalse(self.marker.exists())
        self.assertIn("::warning::", ran.stderr)

    def test_recorder_failure_is_only_a_warning(self) -> None:
        ran = self.run_script("1.3.205", RECORDER_RC="3")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("::warning::", ran.stderr)

    def test_bad_version_is_refused(self) -> None:
        ran = subprocess.run(["bash", str(SCRIPT), "v1.3"], capture_output=True, text=True)
        self.assertEqual(ran.returncode, 2)

    def test_release_sh_hook_is_opt_in_and_non_fatal(self) -> None:
        source = RELEASE.read_text()
        hook = source[source.index('if [ -n "${YUETO_ROOT:-}" ]'):]
        self.assertIn('[ -n "${YUELINK_CHECKOUT:-}" ]', hook)
        self.assertIn("record-ledger-after-golive.sh", hook)
        self.assertIn("||", hook.split("fi", 1)[0])


if __name__ == "__main__":
    unittest.main()
