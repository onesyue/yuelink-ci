#!/usr/bin/env python3
"""Require every dial artifact to name the reviewed candidate and baseline."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

PLATFORMS = ('windows', 'macos', 'linux', 'android', 'ios')
PROTOCOLS = {'vless-reality-tfo', 'vless-reality', 'vless-ws-tls', 'hysteria2'}


def verify(directory: Path, candidate: str, baseline: str) -> None:
    if any(re.fullmatch(r'[0-9a-f]{40}', revision) is None for revision in (candidate, baseline)):
        raise ValueError('expected revisions must be exact commit IDs')
    if {path.name for path in directory.iterdir()} != {f'dial-gate-{name}' for name in PLATFORMS}:
        raise ValueError('missing or unexpected platform artifacts')
    for platform in PLATFORMS:
        folder = directory / f'dial-gate-{platform}'
        for role, expected in (('baseline', baseline), ('candidate', candidate)):
            report = json.loads((folder / f'{role}.json').read_text())
            if not isinstance(report, dict) or (
                report.get('platform') != platform or report.get('role') != role
                or report.get('revision') != expected
            ):
                raise ValueError(f'{platform} {role} source identity mismatch')
            if not isinstance(report.get('proxies'), dict) or set(report['proxies']) != PROTOCOLS:
                raise ValueError(f'{platform} {role} protocol identity mismatch')
        verdict = json.loads((folder / 'gate.json').read_text())
        if not isinstance(verdict, dict) or (
            verdict.get('platform') != platform
            or verdict.get('candidate_revision') != candidate
            or verdict.get('baseline_revision') != baseline
        ):
            raise ValueError(f'{platform} verdict source identity mismatch')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports', type=Path, required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--baseline', required=True)
    args = parser.parse_args()
    verify(args.reports, args.candidate, args.baseline)
    builder = os.environ.get('DIAL_BUILDER_SHA', '')
    source_pr = os.environ.get('DIAL_SOURCE_PR', '')
    if builder and re.fullmatch(r'[0-9a-f]{40}', builder) is None:
        raise ValueError('builder identity must be an exact commit ID')
    if source_pr and re.fullmatch(r'[1-9][0-9]*', source_pr) is None:
        raise ValueError('source PR identity must be a positive integer')
    mode = 'verification only; no publication' if os.environ.get('DIAL_VERIFICATION_ONLY') == 'true' else 'release dial evidence'
    receipt = (f'### Dial source identity ({mode})\n\n'
               f'- Candidate: `{args.candidate}`\n- Baseline: `{args.baseline}`\n'
               f'- Builder: `{builder}`\n- Source PR: {source_pr or "attested release source"}\n')
    print(receipt)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with Path(summary).open('a') as output:
            output.write(receipt + '\n')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        raise SystemExit(f'dial report identity rejected: {exc}') from exc
