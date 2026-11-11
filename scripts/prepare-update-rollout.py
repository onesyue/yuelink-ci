#!/usr/bin/env python3
"""Admit an IP-cohort plan from authenticated immutable archives; never sign/write R2."""
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
ACKNOWLEDGEMENT = 'network IPs are not installations'


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_json(raw: bytes):
    return json.loads(raw, object_pairs_hook=VERIFIER._reject_duplicate_keys)


def policy(manifest: dict, now: datetime) -> None:
    if manifest.get('force') == 'hard':
        raise ValueError('server rollout accepts globally soft-only manifests')
    if manifest.get('rollout', {}).get('percent', 100) != 100:
        raise ValueError('client rollout must be absent or 100; double cohort selection is forbidden')
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
            version: str, baseline_version: str, percent: str, acknowledgement: str,
            previous: dict | None, initial_root: bytes | None,
            promotion_builder: str, promotion_run: str, now: datetime | None = None) -> dict:
    if acknowledgement != ACKNOWLEDGEMENT:
        raise ValueError('explicit network-IP acknowledgement is required; this does not measure installations')
    if not re.fullmatch(r'(?:0|[1-9][0-9]?|100)', percent):
        raise ValueError('percentage must be an explicit integer from 0 through 100')
    if not re.fullmatch(r'[0-9a-f]{40}', promotion_builder) or not re.fullmatch(r'[1-9][0-9]*', promotion_run):
        raise ValueError('missing independent promotion run identity')
    now = now or datetime.now(timezone.utc)
    baseline, candidate = verified_pair(baseline_raw, candidate_raw, now)
    if candidate['version'] != version or baseline['version'] != baseline_version:
        raise ValueError('requested versions differ from immutable signed bytes')
    binding = read_json(binding_raw)
    identity = binding_identity(binding, candidate, candidate_raw, build_run)
    old, new = descriptor(baseline, baseline_raw), descriptor(candidate, candidate_raw)
    salt = digest(('yuelink-rollout-v1\n' + old['sha256'] + '\n' + new['sha256']).encode())
    if previous is None:
        if initial_root != baseline_raw:
            raise ValueError('initial baseline must match the exact existing root bytes')
    else:
        if previous.get('schemaVersion') != 1 or previous.get('mode') != 'network-ip' or not isinstance(previous.get('percent'), int) or isinstance(previous['percent'], bool) or not 0 <= previous['percent'] <= 100:
            raise ValueError('unknown previous rollout plan')
        if previous.get('candidate') == new:
            if previous.get('baseline') != old or previous.get('archiveIdentity') != identity or previous.get('archiveBindingSha256') != digest(binding_raw):
                raise ValueError('existing campaign baseline or original archive identity drifted')
            salt = previous.get('salt', '')
        elif previous['percent'] == 100:
            if previous.get('candidate') != old:
                raise ValueError('next campaign must use the completed candidate as its baseline')
        elif previous['percent'] == 0:
            if previous.get('baseline') != old or VERIFIER._stable_version(version) <= VERIFIER._stable_version(previous['candidate']['version']):
                raise ValueError('replacement after withdrawal must preserve baseline and advance version')
        else:
            raise ValueError('finish or withdraw the current campaign before changing candidate')
    if not isinstance(salt, str) or not re.fullmatch(r'[0-9a-f]{64}', salt):
        raise ValueError('invalid stable campaign salt')
    return {'schemaVersion': 1, 'mode': 'network-ip', 'percent': int(percent), 'salt': salt,
            'baseline': old, 'candidate': new, 'archiveIdentity': identity,
            'archiveBindingSha256': digest(binding_raw),
            'promotion': {'builderCommit': promotion_builder, 'runId': promotion_run,
                          'workflow': '.github/workflows/promote-update-rollout.yml', 'ref': 'refs/heads/master'}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--baseline-version', required=True)
    parser.add_argument('--percent', required=True)
    parser.add_argument('--acknowledgement', required=True)
    parser.add_argument('--promotion-builder', required=True)
    parser.add_argument('--promotion-run', required=True)
    args = parser.parse_args()
    directory = args.directory
    result = prepare(baseline_raw=(directory / 'baseline.json').read_bytes(), candidate_raw=(directory / 'candidate.json').read_bytes(),
                     binding_raw=(directory / 'binding.json').read_bytes(), build_run=read_json((directory / 'build-run.json').read_bytes()),
                     version=args.version, baseline_version=args.baseline_version, percent=args.percent, acknowledgement=args.acknowledgement,
                     previous=read_json((directory / 'previous-plan.json').read_bytes()) if (directory / 'previous-plan.json').exists() else None,
                     initial_root=(directory / 'initial-root.json').read_bytes() if (directory / 'initial-root.json').exists() else None,
                     promotion_builder=args.promotion_builder, promotion_run=args.promotion_run)
    (directory / 'next-plan.json').write_text(json.dumps(result, sort_keys=True, separators=(',', ':')) + '\n')
    print(f"Admitted network-IP plan at {result['percent']}%; this is not an installation percentage")


if __name__ == '__main__':
    main()
