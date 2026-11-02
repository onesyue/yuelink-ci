#!/usr/bin/env python3
"""Release metadata gate; --report-json also supplies the release-drift monitor."""
import argparse
import json
import re
import subprocess
import sys

SHIPPED = ('lib/', 'android/', 'ios/', 'macos/', 'windows/', 'linux/',
           'core/', 'assets/', 'service/')
STABLE = r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'


def git(*args):
    return subprocess.check_output(['git', *args], stderr=subprocess.PIPE).decode()


def version(ref):
    match = re.search(r'^version:\s*[\'\"]?(' + STABLE +
                      r')(?:\+[0-9]+)?[\'\"]?\s*(?:#.*)?$',
                      git('show', f'{ref}:pubspec.yaml'), re.M)
    if not match:
        raise ValueError('pubspec.yaml must declare a stable X.Y.Z[+build] version')
    return match[1]


def pubspec_inputs(ref):
    # Fail conservatively for YAML syntax we cannot prove is dev-only. In
    # particular, quoted keys and anchors must not hide dependency changes.
    raw = git('show', f'{ref}:pubspec.yaml')
    raw = re.sub(r'^version:.*\n?', '', raw, flags=re.M)
    if re.search(r'[&*][A-Za-z0-9_-]+', raw):
        return raw
    # Only the conventional, unquoted dev_dependencies block is exempt.
    return re.sub(r'^dev_dependencies:[^\n]*\n(?:[ \t].*\n|\n)*',
                  '', raw, flags=re.M)


def shipped_between(base, head):
    paths = git('diff', '--name-only', '--no-renames', '-z', base, head).split('\0')
    if any(p.startswith(SHIPPED) or p == 'pubspec.lock' for p in paths):
        return True
    return 'pubspec.yaml' in paths and pubspec_inputs(base) != pubspec_inputs(head)


def report(ref):
    if git('rev-parse', '--is-shallow-repository').strip() != 'false':
        raise ValueError('full history and tags required; fetch with fetch-depth: 0')
    head = git('rev-parse', '--verify', f'{ref}^{{commit}}').strip()
    tags = [t for t in git('tag', '--merged', head, '--list', 'v*').splitlines()
            if re.fullmatch('v' + STABLE, t)]
    if not tags:
        raise ValueError('no reachable stable vX.Y.Z tag; fetch release tags')
    tag = max(tags, key=lambda t: tuple(map(int, t[1:].split('.'))))
    first = None
    # First-parent timestamps measure when master received a change, including
    # merges, rather than the author date of an old topic-branch commit.
    for commit in git('rev-list', '--first-parent', '--reverse', f'{tag}..{head}').splitlines():
        if shipped_between(f'{commit}^', commit):
            first = int(git('show', '-s', '--format=%ct', commit).strip())
            break
    return dict(sha=head, tag=tag, version=version(head), first_shipped_at=first)


def check(data):
    if data['first_shipped_at'] is None:
        return
    current = data['version']
    if tuple(map(int, current.split('.'))) <= tuple(map(int, data['tag'][1:].split('.'))):
        raise ValueError(f"shipped source changed since {data['tag']}; bump pubspec version above it")
    tag = 'v' + current
    changelog = git('show', f"{data['sha']}:CHANGELOG.md")
    if not re.search(r'^## ' + re.escape(tag) + r'(?:\s|$)', changelog, re.M):
        raise ValueError(f'CHANGELOG.md requires a ## {tag} entry')
    for suffix in ('.md', '.en.md'):
        path = f'docs/releases/{tag}{suffix}'
        if not git('show', f"{data['sha']}:{path}").strip():
            raise ValueError(f'release notes are empty: {path}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='HEAD')
    parser.add_argument('--report-json', action='store_true')
    args = parser.parse_args()
    try:
        data = report(args.ref)
        if args.report_json:
            print(json.dumps(data))
        else:
            check(data)
            print(f"version gate: PASS ({data['tag']} -> {data['version']})")
    except (ValueError, subprocess.CalledProcessError) as exc:
        # Never echo git stderr: remote configuration can contain credentials.
        detail = str(exc) if isinstance(exc, ValueError) else 'required Git history or release file unavailable'
        print('version gate: FAIL: ' + detail, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
