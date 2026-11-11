#!/usr/bin/env python3
"""Validate verification-only source inputs before private checkout or execution."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

SOURCE_REPOSITORY = 'onesyue/yuelink'
BUILDER_REPOSITORY = 'onesyue/yuelink-ci'


def exact_sha(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{40}', value) is None:
        raise ValueError('source commit must be exactly 40 lowercase hexadecimal characters')
    return value


def compile_mode(value: object) -> str:
    if not isinstance(value, str) or value not in ('positive', 'negative'):
        raise ValueError('iOS compile mode must be positive or negative')
    return value


def verification_scope(event_name: str, event: dict, manifest: dict | None) -> str:
    if event_name == 'pull_request':
        if not isinstance(manifest, dict):
            raise ValueError('reviewed source manifest is missing')
        value = manifest.get('verificationScope', 'full')
    elif event_name == 'workflow_dispatch':
        value = event.get('inputs', {}).get('verification_scope', 'full')
    else:
        raise ValueError('unsupported verification event')
    if not isinstance(value, str) or value not in ('full', 'ios-only'):
        raise ValueError('verification scope must be full or ios-only')
    return value


def source_input(event_name: str, event: dict, ref: str, manifest: dict | None) -> tuple[int, str, str]:
    if event.get('repository', {}).get('full_name') != BUILDER_REPOSITORY:
        raise ValueError('unexpected builder repository')
    if event_name == 'pull_request':
        if event.get('pull_request', {}).get('head', {}).get('repo', {}).get('full_name') != BUILDER_REPOSITORY:
            raise ValueError('fork pull requests cannot access the private source')
        required = {'sourceRepository', 'sourcePr', 'sourceCommit', 'iosCompileMode'}
        if not isinstance(manifest, dict) or set(manifest) not in (required, required | {'verificationScope'}):
            raise ValueError('reviewed source manifest is missing or malformed')
        if manifest['sourceRepository'] != SOURCE_REPOSITORY:
            raise ValueError('unexpected private source repository')
        number = manifest['sourcePr']
        if type(number) is not int or number <= 0:
            raise ValueError('source PR must be a positive integer')
        verification_scope(event_name, event, manifest)
        return number, exact_sha(manifest['sourceCommit']), compile_mode(manifest['iosCompileMode'])
    if event_name == 'workflow_dispatch':
        if not ref.startswith('refs/heads/') or ref == 'refs/heads/':
            raise ValueError('verification dispatch must use a branch, never a tag')
        inputs = event.get('inputs', {})
        number = inputs.get('source_pr')
        if not isinstance(number, str) or re.fullmatch(r'[1-9][0-9]*', number) is None:
            raise ValueError('source PR must be a positive integer')
        verification_scope(event_name, event, manifest)
        return int(number), exact_sha(inputs.get('source_sha')), compile_mode(inputs.get('ios_compile_mode', 'positive'))
    raise ValueError('unsupported verification event')


def confirm_checkout(path: Path, expected: str) -> None:
    expected = exact_sha(expected)
    actual = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != expected:
        raise ValueError('private PR head changed or checkout does not match reviewed source')
    # persist-credentials:false must remove checkout authentication before code runs.
    result = subprocess.run(
        ['git', '-C', str(path), 'config', '--local', '--name-only', '--get-regexp',
         r'^(core\.sshcommand|http\..*\.extraheader)$'],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 1:
        raise ValueError('checkout credentials remain configured or cannot be inspected')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('inputs', 'confirm'))
    parser.add_argument('--manifest', type=Path, default=Path('policy/dialgate-source.json'))
    parser.add_argument('--checkout', type=Path)
    parser.add_argument('--expected')
    args = parser.parse_args()
    if args.mode == 'confirm':
        if args.checkout is None or args.expected is None:
            raise ValueError('confirm requires checkout and expected commit')
        confirm_checkout(args.checkout, args.expected)
        return
    event_name = os.environ.get('GITHUB_EVENT_NAME', '')
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    manifest = json.loads(args.manifest.read_text()) if event_name == 'pull_request' else None
    number, revision, ios_mode = source_input(event_name, event, os.environ.get('GITHUB_REF', ''), manifest)
    scope = verification_scope(event_name, event, manifest)
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
        output.write(f'source_pr={number}\ncandidate_commit={revision}\nios_compile_mode={ios_mode}\nverification_scope={scope}\n')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f'dial verification binding rejected: {exc}') from exc
