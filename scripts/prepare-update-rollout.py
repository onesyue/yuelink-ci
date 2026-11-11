#!/usr/bin/env python3
"""Admit a signed archive for root promotion. Percentage rollout is rejected."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError('reviewed verifier unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFIER = load('rollout_manifest_verifier', 'verify-update-manifest.py')
CLOSURE = load('rollout_release_closure', 'verify-release-closure.py')


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_json(raw: bytes):
    return json.loads(raw, object_pairs_hook=VERIFIER._reject_duplicate_keys)


def policy(manifest: dict, now: datetime, *, force: str = 'soft', force_reason: str | None = None) -> None:
    if 'rollout' in manifest or 'percent' in manifest:
        raise ValueError('rollout percentage fields are not accepted')
    names = manifest.get('forcePlatforms')
    if names is not None and (
            not isinstance(names, list) or any(not isinstance(name, str) for name in names)
            or any(name == 'ios' or name.startswith('ios') for name in names)):
        raise ValueError('iOS is never forced')
    signed_force = manifest.get('force', 'soft')
    if signed_force not in {'soft', 'hard'} or not isinstance(signed_force, str):
        raise ValueError('force must be soft or hard')
    if force == 'soft':
        if signed_force == 'hard' or names is not None:
            raise ValueError('soft promotion rejects a hard manifest')
        if force_reason:
            raise ValueError('soft force does not take forceReason')
    elif force == 'hard':
        if force_reason not in {'security', 'incompatible'}:
            raise ValueError('hard enforcement requires forceReason')
        platforms = manifest.get('platforms')
        if not isinstance(platforms, dict) or 'ios' not in platforms:
            raise ValueError('ios platform is required so it can be excluded from force')
        expected = sorted(name for name in platforms if isinstance(name, str) and name != 'ios' and not name.startswith('ios'))
        if signed_force != 'hard' or manifest.get('forceReason') != force_reason or names != expected:
            raise ValueError('hard promotion requires force on every non-iOS platform')
    else:
        raise ValueError('force must be soft or hard')
    if VERIFIER._utc_timestamp(manifest['expiresAt'], 'expiresAt') <= now:
        raise ValueError('expired archive cannot be admitted for client exposure')

def verified_pair(baseline_raw: bytes, candidate_raw: bytes, now: datetime) -> tuple[dict, dict]:
    # An explicitly reviewed baseline is an archived signed object, not a new
    # root. The candidate must preserve its version/time/compatibility floor.
    baseline = VERIFIER.verify_historical_archive(baseline_raw)
    candidate = VERIFIER.verify(candidate_raw, minimum_raw=baseline_raw)
    for manifest in (baseline, candidate):
        policy(manifest, now)
    if VERIFIER._stable_version(candidate['version']) <= VERIFIER._stable_version(baseline['version']):
        raise ValueError('candidate must be newer than baseline; withdrawal is not a downgrade')
    return baseline, candidate


def binding_identity(binding: dict, manifest: dict, raw: bytes, build_run: dict) -> dict:
    if binding.get('schemaVersion') != 2 or isinstance(binding.get('schemaVersion'), bool) or binding.get('mode') != 'in-pipeline':
        raise ValueError('unknown immutable archive binding schema or mode')
    evidence = manifest.get('promotionEvidence')
    if not isinstance(evidence, dict):
        raise ValueError('candidate lacks original signed build evidence')
    expected = {'version': manifest['version'], 'tag': 'v' + manifest['version'],
                'sourceCommit': manifest['sourceCommit'], 'signedArchiveSha256': digest(raw),
                'candidateSha256': evidence['candidateSha256'], 'sha256SumsSha256': manifest['sha256SumsSha256'],
                'payloadBundleSha256': evidence['payloadBundleSha256'], 'payloadCount': evidence['payloadCount']}
    if any(binding.get(key) != value for key, value in expected.items()):
        raise ValueError('immutable archive binding differs from signed candidate')
    if not re.fullmatch(r'[0-9a-f]{40}', binding.get('builderCommit', '')):
        raise ValueError('missing original archive builder identity')
    for key in ('buildRunId', 'sourceAttestationRunId'):
        if not isinstance(binding.get(key), str) or not re.fullmatch(r'[1-9][0-9]*', binding[key]):
            raise ValueError('missing original archive run identity')
    payloads = binding.get('payloads')
    if not isinstance(payloads, list) or len(payloads) != 19:
        raise ValueError('archive binding must retain the complete payload inventory')
    lines = []
    for payload in payloads:
        if not isinstance(payload, dict) or set(payload) != {'name', 'sha256'} or not re.fullmatch(r'[A-Za-z0-9._-]+', payload.get('name', '')) or not re.fullmatch(r'[0-9a-f]{64}', payload.get('sha256', '')):
            raise ValueError('invalid original payload binding')
        lines.append(f"{payload['sha256']}  {payload['name']}\n")
    sums = ''.join(lines).encode()
    if digest(sums) != manifest['sha256SumsSha256']:
        raise ValueError('binding payload inventory does not reproduce signed checksum anchor')
    CLOSURE._parse_sums(sums, manifest)
    if (build_run.get('id') != int(binding['buildRunId']) or build_run.get('head_sha') != binding['builderCommit']
            or build_run.get('repository', {}).get('full_name') != 'onesyue/yuelink-ci'
            or build_run.get('path') != '.github/workflows/build.yml'
            or build_run.get('event') != 'push' or build_run.get('head_branch') != binding['tag']
            or build_run.get('status') != 'completed' or build_run.get('conclusion') != 'success'):
        raise ValueError('original archive build run is not the exact successful tagged builder')
    return {key: binding[key] for key in ('sourceCommit', 'builderCommit', 'buildRunId', 'sourceAttestationRunId')}


def descriptor(manifest: dict, raw: bytes) -> dict:
    return {'version': manifest['version'], 'key': f"v{manifest['version']}/update.json",
            'sha256': digest(raw), 'expiresAt': manifest['expiresAt']}


def prepare(*, baseline_raw: bytes, candidate_raw: bytes, binding_raw: bytes, build_run: dict,
            version: str, baseline_version: str, force: str, force_reason: str | None,
            previous: dict | None, initial_root: bytes | None,
            promotion_builder: str, promotion_run: str, now: datetime | None = None) -> dict:
    if force not in {'soft', 'hard'}:
        raise ValueError('force must be soft or hard')
    if not re.fullmatch(r'[0-9a-f]{40}', promotion_builder) or not re.fullmatch(r'[1-9][0-9]*', promotion_run):
        raise ValueError('missing independent promotion run identity')
    now = now or datetime.now(timezone.utc)
    baseline = VERIFIER.verify_historical_archive(baseline_raw)
    candidate = VERIFIER.verify(candidate_raw, minimum_raw=baseline_raw)
    policy(baseline, now, force='soft')
    policy(candidate, now, force=force, force_reason=force_reason)
    if VERIFIER._stable_version(candidate['version']) <= VERIFIER._stable_version(baseline['version']):
        raise ValueError('candidate must be newer than baseline; withdrawal is not a downgrade')
    if candidate['version'] != version or baseline['version'] != baseline_version:
        raise ValueError('requested versions differ from immutable signed bytes')
    binding = read_json(binding_raw)
    identity = binding_identity(binding, candidate, candidate_raw, build_run)
    old, new = descriptor(baseline, baseline_raw), descriptor(candidate, candidate_raw)
    if previous is None:
        if initial_root != baseline_raw:
            raise ValueError('initial baseline must match the exact existing root bytes')
    else:
        if previous.get('schemaVersion') != 1 or previous.get('mode') != 'root-manifest' or 'percent' in previous:
            raise ValueError('unknown previous promotion record')
        if previous.get('archiveIdentity') != identity or previous.get('archiveBindingSha256') != digest(binding_raw):
            raise ValueError('existing promotion archive identity drifted')
        if previous.get('candidate') != new or previous.get('baseline') != old or previous.get('force') != force:
            raise ValueError('existing promotion record does not match this archive')
    result = {'schemaVersion': 1, 'mode': 'root-manifest', 'force': force,
              'baseline': old, 'candidate': new, 'archiveIdentity': identity,
              'archiveBindingSha256': digest(binding_raw),
              'legacyClientForce': candidate.get('force', 'soft'),
              'iosForce': 'soft',
              'promotion': {'builderCommit': promotion_builder, 'runId': promotion_run,
                            'workflow': '.github/workflows/promote-update-rollout.yml', 'ref': 'refs/heads/master'}}
    if force == 'hard':
        result['forceReason'] = force_reason
        result['forcePlatforms'] = candidate['forcePlatforms']
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--baseline-version', required=True)
    parser.add_argument('--force', required=True, choices=('soft', 'hard'))
    parser.add_argument('--force-reason', default='')
    parser.add_argument('--promotion-builder', required=True)
    parser.add_argument('--promotion-run', required=True)
    args = parser.parse_args()
    directory = args.directory
    result = prepare(baseline_raw=(directory / 'baseline.json').read_bytes(), candidate_raw=(directory / 'candidate.json').read_bytes(),
                     binding_raw=(directory / 'binding.json').read_bytes(), build_run=read_json((directory / 'build-run.json').read_bytes()),
                     version=args.version, baseline_version=args.baseline_version, force=args.force,
                     force_reason=args.force_reason or None,
                     previous=read_json((directory / 'previous-plan.json').read_bytes()) if (directory / 'previous-plan.json').exists() else None,
                     initial_root=(directory / 'initial-root.json').read_bytes() if (directory / 'initial-root.json').exists() else None,
                     promotion_builder=args.promotion_builder, promotion_run=args.promotion_run)
    (directory / 'next-plan.json').write_text(json.dumps(result, sort_keys=True, separators=(',', ':')) + '\n')
    print(f"Admitted {result['force']} root policy; iOS stays a prompt for current clients")


if __name__ == '__main__':
    main()
