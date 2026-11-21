#!/usr/bin/env python3
"""Copied public endpoints for YueLink CI.

The download page and bot handle match yueto design/sources/brand.json.
The CDN host, update manifest, and download-health URL are the endpoints
this repository already pins. There is no runtime dependency on yueto.
"""

from __future__ import annotations

import sys

# brand.json urls.download
URL_DOWNLOAD = "https://yue.to/download.html"

# YueLink distribution CDN. Not a brand.json key; this repo is the publisher.
ASSET_HOST = "yuetong.app"
URL_UPDATE_MANIFEST = f"https://{ASSET_HOST}/update.json"
URL_DOWNLOAD_HEALTH = f"https://{ASSET_HOST}/health/yue-to-download.json"

# brand.json telegram.bot. Keep the public handle.
TELEGRAM_BOT = "yuetoo_bot"
TELEGRAM_BOT_HANDLE = f"@{TELEGRAM_BOT}"
TELEGRAM_BOT_URL = f"https://t.me/{TELEGRAM_BOT}"


def main(argv: list[str]) -> int:
    names = {
        "ASSET_HOST": ASSET_HOST,
        "URL_DOWNLOAD": URL_DOWNLOAD,
        "URL_UPDATE_MANIFEST": URL_UPDATE_MANIFEST,
        "URL_DOWNLOAD_HEALTH": URL_DOWNLOAD_HEALTH,
        "TELEGRAM_BOT": TELEGRAM_BOT,
        "TELEGRAM_BOT_HANDLE": TELEGRAM_BOT_HANDLE,
        "TELEGRAM_BOT_URL": TELEGRAM_BOT_URL,
    }
    if len(argv) != 2 or argv[1] not in names:
        print("usage: brand_literals.py NAME", file=sys.stderr)
        return 2
    print(names[argv[1]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
