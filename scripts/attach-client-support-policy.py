#!/usr/bin/env python3
"""Attach reviewed observation policy before the existing manifest signer.

Does not sign, fetch, promote or mutate the input candidate. Previous manifest
signature is authenticated using the existing builder verifier, including for
expired historical roots; its current time/expiry is not a new trust anchor.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def attach(candidate, source, previous, verifier):
    policy = load_module('builder_compatibility', Path(__file__).parent / 'lib/client_support_policy.py')
    if not isinstance(candidate, dict) or 'sig' in candidate or 'compatibility' in candidate:
        raise ValueError('only an unsigned candidate without compatibility can be attached')
    # Authenticity comes from the existing updater trust root, never an input key.
    old = verifier._verify_signed_manifest(previous)
    floor = verifier._verify_signed_manifest(verifier.MINIMUM_MANIFEST.read_bytes())
    verifier._enforce_minimum(old, floor)
    if 'compatibility' in old:
        policy.validate(old['compatibility'], published_at=old['publishedAt'])
    source_value = json.loads(source, object_pairs_hook=no_duplicates)
    policy.shape(source_value, policy.POLICY_KEYS - {'policySourceSha256'}, 'source')
    result = {**source_value, 'policySourceSha256': hashlib.sha256(source).hexdigest()}
    policy.validate(result, published_at=candidate['publishedAt'])
    policy.validate_transition(result, old.get('compatibility'))
    return {**candidate, 'compatibility': result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--policy', required=True, type=Path)
    parser.add_argument('--previous-manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--verifier', type=Path, default=Path(__file__).with_name('verify-update-manifest.py'))
    args = parser.parse_args()
    verifier = load_module('existing_manifest_verifier', args.verifier)
    candidate = json.loads(args.candidate.read_bytes(), object_pairs_hook=no_duplicates)
    result = attach(candidate, args.policy.read_bytes(), args.previous_manifest.read_bytes(), verifier)
    # Do not overwrite an input, a signed root or a previous result.
    with args.output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


if __name__ == '__main__':
    main()
