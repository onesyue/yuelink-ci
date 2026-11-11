#!/usr/bin/env python3
"""Reject task graphs outside the exact unsigned Android compilation scope."""
from __future__ import annotations

import json
from pathlib import Path
import sys


def validate(graph: object) -> None:
    if not isinstance(graph, list) or not graph:
        raise ValueError('missing Android task graph')
    paths = []
    for task in graph:
        if not isinstance(task, dict) or set(task) != {'path', 'type'}:
            raise ValueError('malformed Android task identity')
        path, kind = task['path'], task['type']
        if not isinstance(path, str) or not path.startswith(':') or not isinstance(kind, str) or not kind:
            raise ValueError('malformed Android task identity')
        paths.append(path)
        name = path.rsplit(':', 1)[-1].lower()
        identity = (name + ' ' + kind).lower()
        # Library compile JAR tasks are needed for the Kotlin classpath. Only
        # application package/bundle tasks are forbidden, not those library JARs.
        forbidden = (
            any(token in name for token in ('release', 'publish', 'sign', 'install'))
            or (path.startswith(':app:') and name.startswith(('package', 'bundle', 'assemble')))
            or any(token in identity for token in ('externalnative', 'cmake', 'ndkbuild', 'cxxbuild', 'buildcxx'))
            or any(token in kind.lower() for token in ('packageapplication', 'packageandroidartifact', 'bundleapplication', 'signing'))
        )
        if forbidden:
            raise ValueError(f'forbidden Android task: {path} ({kind})')
    if len(set(paths)) != len(paths) or ':app:compileDebugKotlin' not in paths:
        raise ValueError('graph must contain the unique app debug Kotlin compilation')


def main() -> None:
    try:
        validate(json.loads(Path(sys.argv[1]).read_text()))
    except (ValueError, OSError, IndexError) as error:
        raise SystemExit(str(error)) from error


if __name__ == '__main__':
    main()
