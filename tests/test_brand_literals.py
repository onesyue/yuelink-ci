"""Pin the official endpoints this repository publishes and verifies."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRAND_PATH = ROOT / "scripts" / "brand_literals.py"
HELPER_URLS = (
    "https://yue.to/download.html",
    "yuetong.app",
)
RETIRED_BOT = "yue" + "_to_bot"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"missing {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BRAND = load("brand_literals_under_test", BRAND_PATH)


class BrandLiteralTests(unittest.TestCase):
    def test_official_values_stay_pinned(self) -> None:
        self.assertEqual(BRAND.URL_DOWNLOAD, "https://yue.to/download.html")
        self.assertEqual(BRAND.ASSET_HOST, "yuetong.app")
        self.assertEqual(BRAND.URL_UPDATE_MANIFEST, "https://yuetong.app/update.json")
        self.assertEqual(
            BRAND.URL_DOWNLOAD_HEALTH,
            "https://yuetong.app/health/yue-to-download.json",
        )
        self.assertEqual(BRAND.TELEGRAM_BOT, "yuetoo_bot")
        self.assertEqual(BRAND.TELEGRAM_BOT_HANDLE, "@yuetoo_bot")
        self.assertEqual(BRAND.TELEGRAM_BOT_URL, "https://t.me/yuetoo_bot")

    def test_helpers_reexport_the_same_values(self) -> None:
        manifest = load(
            "verify_update_manifest_brand",
            ROOT / "scripts/verify-update-manifest.py",
        )
        health = load(
            "verify_release_page_health_brand",
            ROOT / "scripts/verify-release-page-health.py",
        )
        drift = load("release_drift_brand", ROOT / "scripts/release_drift.py")
        self.assertEqual(manifest.ASSET_HOST, BRAND.ASSET_HOST)
        self.assertEqual(manifest.RELEASE_URL, BRAND.URL_DOWNLOAD)
        self.assertEqual(health.RELEASE_URL, BRAND.URL_DOWNLOAD)
        self.assertEqual(health.PUBLIC_URL, BRAND.URL_DOWNLOAD_HEALTH)
        self.assertEqual(drift.CHANNEL_URL, BRAND.URL_UPDATE_MANIFEST)

    def test_first_party_tree_does_not_restore_retired_bot(self) -> None:
        for path in (ROOT / "scripts").rglob("*"):
            if path.suffix not in {".py", ".sh"}:
                continue
            self.assertNotIn(RETIRED_BOT, path.read_text(encoding="utf-8"), str(path))
        for path in (ROOT / "tests").rglob("*.py"):
            if "fixtures" in path.parts:
                continue
            self.assertNotIn(RETIRED_BOT, path.read_text(encoding="utf-8"), str(path))

    def test_helper_scripts_keep_official_urls_in_the_constant_module(self) -> None:
        for path in (ROOT / "scripts").rglob("*"):
            if path.suffix not in {".py", ".sh"} or path.name == "brand_literals.py":
                continue
            text = path.read_text(encoding="utf-8")
            for needle in HELPER_URLS:
                self.assertNotIn(needle, text, f"{path} still contains {needle}")

    def test_cli_prints_one_public_name_and_rejects_unknown(self) -> None:
        shown = subprocess.run(
            [sys.executable, str(BRAND_PATH), "ASSET_HOST"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(shown.stdout, BRAND.ASSET_HOST + "\n")
        self.assertNotIn("usage:", shown.stdout)
        rejected = subprocess.run(
            [sys.executable, str(BRAND_PATH), "NOT_A_PUBLIC_NAME"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(rejected.stdout, "")
        self.assertIn("usage:", rejected.stderr)
        missing = subprocess.run(
            [sys.executable, str(BRAND_PATH)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, "")


if __name__ == "__main__":
    unittest.main()
