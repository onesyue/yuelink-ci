"""Reviewed compatibility schema; generated consumers must match these bytes."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

REPOSITORIES = {
    'mihomo': 'MetaCubeX/mihomo', 'sing-box': 'SagerNet/sing-box',
    'clash-verge-rev': 'clash-verge-rev/clash-verge-rev',
    'flclash': 'chen08209/FlClash', 'openclash': 'vernesong/OpenClash',
    'metacubexd': 'MetaCubeX/metacubexd', 'zashboard': 'Zephyruso/zashboard',
}
CORES = {'mihomo', 'sing-box'}
PLATFORMS = {'android', 'ios', 'macos', 'windows', 'linux', 'openwrt', 'web'}
POLICY_KEYS = {'schemaVersion', 'policyRevision', 'effectiveAt', 'transitionDays',
               'enforcement', 'unknownClient', 'policySourceSha256', 'releases'}


def require(condition, message):
    if not condition:
        raise ValueError('compatibility: ' + message)


def shape(value, keys, label):
    require(isinstance(value, dict) and set(value) == set(keys), label + ' shape is not exact')


def timestamp(value):
    require(isinstance(value, str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z', value),
            'timestamp must be canonical UTC seconds')
    try:
        parsed = datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError('compatibility: invalid calendar timestamp') from exc
    return parsed


def digest(value):
    require(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value), 'invalid SHA-256')


def version(value):
    require(isinstance(value, str) and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value), 'version must be stable X.Y.Z')


def evidence(value, product=None):
    shape(value, {'url', 'sha256'}, 'evidence')
    digest(value['sha256'])
    url = value['url']
    require(isinstance(url, str) and len(url) <= 1024
            and all(33 <= ord(character) <= 126 for character in url)
            and '?' not in url and '#' not in url, 'invalid public evidence URL')
    parsed = urlsplit(url)
    require(parsed.scheme == 'https' and parsed.netloc == 'github.com'
            and not parsed.query and not parsed.fragment and '%' not in url
            and '\\' not in url and all(part not in {'.', '..'} for part in parsed.path.split('/')),
            'evidence must be a public immutable GitHub URL')
    if product is None:
        pattern = r'/onesyue/yuelink-ci/blob/[0-9a-f]{40}/[A-Za-z0-9_./-]+'
    else:
        pattern = '/' + re.escape(REPOSITORIES[product]) + r'/releases/(?:tag/[A-Za-z0-9_.-]+|download/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)'
    require(re.fullmatch(pattern, parsed.path), 'evidence repository/path is not approved')


def validate(policy, *, published_at):
    shape(policy, POLICY_KEYS, 'policy')
    require(type(policy['schemaVersion']) is int and policy['schemaVersion'] == 1, 'unsupported schemaVersion')
    require(type(policy['policyRevision']) is int and policy['policyRevision'] > 0, 'invalid policyRevision')
    require(type(policy['transitionDays']) is int and policy['transitionDays'] == 90, 'transitionDays must be 90')
    require(policy['enforcement'] == 'observe' and policy['unknownClient'] == 'unverified', 'only observe/unverified is supported')
    published = timestamp(published_at)
    require(timestamp(policy['effectiveAt']) <= published, 'effectiveAt is after manifest publication')
    digest(policy['policySourceSha256'])
    releases = policy['releases']
    require(isinstance(releases, list) and len(releases) <= 256, 'releases must be a bounded array')
    identities, maintained, core_refs = set(), set(), []
    for release in releases:
        shape(release, {'product', 'version', 'releasedAt', 'evidence', 'certifications', 'maintainedLatest'}, 'release')
        product = release['product']
        require(isinstance(product, str) and product in REPOSITORIES, 'unknown product')
        version(release['version'])
        identity = (product, release['version'])
        require(identity not in identities, 'duplicate release identity')
        identities.add(identity)
        released = timestamp(release['releasedAt'])
        require(released <= published, 'release date is after manifest publication')
        evidence(release['evidence'], product)
        certificates = release['certifications']
        require(isinstance(certificates, list) and len(certificates) <= 64, 'certifications must be a bounded array')
        combinations = set()
        for cert in certificates:
            shape(cert, {'platform', 'core', 'testedAt', 'evidence'}, 'certification')
            require(isinstance(cert['platform'], str) and cert['platform'] in PLATFORMS, 'unknown certification platform')
            require(released <= timestamp(cert['testedAt']) <= published, 'invalid certification date')
            evidence(cert['evidence'])
            core = cert['core']
            if product in CORES:
                require(core is None, 'core product cannot embed another core')
                combination = (cert['platform'], None)
            else:
                shape(core, {'product', 'version'}, 'application core')
                require(isinstance(core['product'], str) and core['product'] in CORES, 'unknown core product')
                version(core['version'])
                core_identity = (core['product'], core['version'])
                core_refs.append((core_identity, timestamp(cert['testedAt'])))
                combination = (cert['platform'], core_identity)
            require(combination not in combinations, 'duplicate certification combination')
            combinations.add(combination)
        latest = release['maintainedLatest']
        if latest is not None:
            shape(latest, {'reviewedAt', 'expiresAt', 'evidence'}, 'maintainedLatest')
            require(released <= timestamp(latest['reviewedAt']) <= published, 'invalid maintenance review date')
            require(timestamp(latest['expiresAt']) > timestamp(latest['reviewedAt']), 'invalid maintenance expiry')
            evidence(latest['evidence'])
            require(product not in maintained, 'multiple latest exceptions for one product')
            maintained.add(product)
    release_dates = {(r['product'], r['version']): timestamp(r['releasedAt']) for r in releases}
    for identity, tested in core_refs:
        require(identity in release_dates and release_dates[identity] <= tested, 'certification core has no released evidence')
    return policy


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def validate_transition(current, previous):
    if previous is None:
        return
    require(current is not None, 'cannot silently remove existing policy')
    require(current['policyRevision'] >= previous['policyRevision'], 'policy revision decreased')
    if current['policyRevision'] == previous['policyRevision']:
        require(canonical(current) == canonical(previous), 'same policyRevision changed')
    require(timestamp(current['effectiveAt']) >= timestamp(previous['effectiveAt']), 'effectiveAt decreased')
    existing = {(r['product'], r['version']): r for r in current['releases']}
    for old in previous['releases']:
        new = existing.get((old['product'], old['version']))
        require(new is not None, 'release history removed')
        require(new['releasedAt'] == old['releasedAt'] and new['evidence'] == old['evidence'], 'original release date/evidence changed')


def observe(policy, product, release_version, platform, *, core=None, now):
    """Only call after signed-manifest validation; never an authorization gate."""
    require(now.tzinfo is not None and now.utcoffset() == timedelta(0), 'observation clock must be UTC')
    if now < timestamp(policy['effectiveAt']):
        return 'unverified'
    found = next((r for r in policy['releases'] if (r['product'], r['version']) == (product, release_version)), None)
    if found is None or not any(c['platform'] == platform and c['core'] == core and timestamp(c['testedAt']) <= now for c in found['certifications']):
        return 'unverified'
    if now < timestamp(found['releasedAt']):
        return 'unverified'
    latest = found['maintainedLatest']
    if latest and timestamp(latest['reviewedAt']) <= now < timestamp(latest['expiresAt']):
        return 'supported'
    return 'transitional' if now < timestamp(found['releasedAt']) + timedelta(days=90) else 'upgrade_required'
