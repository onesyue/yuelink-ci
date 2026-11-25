#!/usr/bin/env python3
"""Report source/channel drift. Requires the version-gate-enabled source checkout."""
import argparse
import importlib.util
import runpy
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

# lib/modules/updater/update_checker.dart: _primaryHost + /update.json.
def _load_brand_literals():
    spec = importlib.util.spec_from_file_location(
        'brand_literals',
        Path(__file__).resolve().parent / 'brand_literals.py',
    )
    if spec is None or spec.loader is None:
        raise RuntimeError('brand_literals.py is missing')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BRAND = _load_brand_literals()
CHANNEL_URL = _BRAND.URL_UPDATE_MANIFEST
_LIMITS = runpy.run_path(str(Path(__file__).resolve().with_name("constants.py")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo', type=Path, nargs='?', default=Path(__file__).resolve().parents[2] / 'yuelink')
    parser.add_argument('--no-fetch', action='store_true', help='use existing origin/master and tags (offline tests)')
    parser.add_argument('--manifest-file', type=Path, help='inspect a saved channel response instead of the live URL')
    args = parser.parse_args()
    try:
        if not args.no_fetch:
            subprocess.run(['git', 'fetch', '--quiet', '--tags', 'origin',
                            'refs/heads/master:refs/remotes/origin/master'],
                           cwd=args.repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        result = subprocess.run(['bash', str(args.repo.resolve() / 'scripts/check-version-bump.sh'),
                                 '--ref', 'refs/remotes/origin/master', '--report-json'],
                                cwd=args.repo, check=True, text=True, capture_output=True)
        data = json.loads(result.stdout)
        print(f"yuelink master sha: {data['sha']}")
        print(f"latest reachable tag: {data['tag']}")
        print(f"pubspec version: {data['version']}")
        if args.manifest_file:
            manifest = json.loads(args.manifest_file.read_text())
        else:
            with urllib.request.urlopen(CHANNEL_URL, timeout=_LIMITS["CHANNEL_FETCH_TIMEOUT_SECONDS"]) as response:
                manifest = json.load(response)
        channel = manifest['version']
        if not isinstance(channel, str):
            raise ValueError('invalid channel version')
        print(f'update channel version: {channel}')
        failures = []
        if channel != data['tag'][1:]:
            failures.append('update channel differs from latest release tag')
        first = data['first_shipped_at']
        if first is not None and time.time() - first > 24 * 60 * 60:
            failures.append('master has shipped-source changes untagged for more than 24h')
        for failure in failures:
            print('release drift: FAIL: ' + failure, file=sys.stderr)
        return int(bool(failures))
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError):
        # Do not print transport exceptions, which can contain credential URLs.
        print('release drift: UNKNOWN: unable to read source history or update channel', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
