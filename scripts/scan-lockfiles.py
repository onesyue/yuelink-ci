#!/usr/bin/env python3
"""Scan the YueLink lockfiles and enforce the CI advisory policy."""

import json
import runpy
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import quote

_LIMITS = runpy.run_path(str(Path(__file__).resolve().with_name("constants.py")))


def go_sbom(core):
    if not (core / "go.sum").is_file():
        raise ValueError("missing Go checksums")

    env = {**os.environ, "GOWORK": "off", "GOFLAGS": ""}

    def go(*args, timeout):
        return subprocess.run(
            ["go", *args], cwd=core, env=env, capture_output=True, text=True,
            timeout=timeout, check=True,
        ).stdout

    # go.sum includes historical versions and modules needed only to resolve
    # the dependency graph. In a tidy Go 1.17+ module, go.mod lists the modules
    # that provide imported packages, including tests and platform build tags.
    go("mod", "tidy", "-diff", timeout=_LIMITS["GO_MOD_TIDY_TIMEOUT_SECONDS"])
    manifest = json.loads(go("mod", "edit", "-json", timeout=_LIMITS["GO_MOD_EDIT_TIMEOUT_SECONDS"]))
    required = [module["Path"] for module in manifest["Require"]]
    if not required:
        raise ValueError("empty Go requirements")

    # Report replacement coordinates when versioned. A local replacement has
    # no registry version, but its third-party imports are in the tidy graph.
    coordinates = (
        r'{{with or .Replace .}}{{if .Version}}{{printf "%s\t%s" .Path .Version}}{{end}}{{end}}'
    )
    selected = go("list", "-m", "-mod=readonly", "-f", coordinates, *required, timeout=_LIMITS["GO_LIST_TIMEOUT_SECONDS"])
    modules = {tuple(line.split("\t")) for line in selected.splitlines() if line}
    if not modules:
        raise ValueError("empty Go scan inventory")
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "components": [
            {
                "type": "library",
                "name": name,
                "version": version,
                "purl": f"pkg:golang/{quote(name, safe='/')}@{quote(version, safe='')}",
            }
            for name, version in sorted(modules)
        ],
    }


def version_key(version, ecosystem):
    match = re.fullmatch(
        r"v?(\d+)\.(\d+)\.(\d+)(?:-([\w.-]+))?(?:\+([\w.-]+))?", version
    )
    if match is None:
        return None
    major, minor, patch, prerelease, build = match.groups()

    def identifiers(value):
        return tuple(
            (0, int(part)) if part.isdecimal() else (1, part)
            for part in value.split(".")
        ) if value else ()

    # Pub orders build suffixes. Go SemVer treats +incompatible as metadata.
    return (
        int(major), int(minor), int(patch), prerelease is None,
        identifiers(prerelease), identifiers(build) if ecosystem == "Pub" else (),
    )


def has_fix(vulnerability, package):
    ecosystem = package["ecosystem"]
    installed = version_key(package["version"], ecosystem)
    if installed is None:
        return False
    for affected in vulnerability.get("affected", []):
        identity = affected.get("package", {})
        if (identity.get("name"), identity.get("ecosystem")) != (
            package["name"], ecosystem
        ):
            continue
        for version_range in affected.get("ranges", []):
            if version_range["type"] not in {"ECOSYSTEM", "SEMVER"}:
                continue
            for event in version_range["events"]:
                fixed = version_key(event.get("fixed", ""), ecosystem)
                if fixed is not None and fixed > installed:
                    return True
    return False


def summarize(report):
    counts = dict(CRITICAL=0, HIGH=0, MEDIUM=0, LOW=0, UNKNOWN=0)
    blocking = 0
    for source in report["results"]:
        for finding in source["packages"]:
            advisories = {v["id"]: v for v in finding.get("vulnerabilities", [])}
            for group in finding.get("groups", []):
                score = group["max_severity"]
                score = float(score) if score else 0
                if 9 <= score <= 10:
                    severity = "CRITICAL"
                elif 7 <= score < 9:
                    severity = "HIGH"
                elif 4 <= score < 7:
                    severity = "MEDIUM"
                elif 0 < score < 4:
                    severity = "LOW"
                else:
                    severity = "UNKNOWN"
                counts[severity] += 1
                if severity in {"HIGH", "CRITICAL"} and any(
                    has_fix(advisories[advisory], finding["package"])
                    for advisory in group["ids"]
                ):
                    blocking += 1
    return counts, blocking


def main(scanner, source):
    scanner = Path(scanner).resolve()
    source = Path(source).resolve()
    blocked = False
    with tempfile.TemporaryDirectory(prefix="yuelink-osv-") as directory:
        work = Path(directory)
        config = work / "osv-scanner.toml"
        config.touch()
        bom = work / "go-modules.cdx.json"
        try:
            bom.write_text(json.dumps(go_sbom(source / "core")))
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            print("core/go.mod + go.sum: Go dependency inventory unverified")
            return 2
        for lockfile, option in (
            ("pubspec.lock", f"--lockfile={source / 'pubspec.lock'}"),
            ("core/go.mod + go.sum", f"--sbom={bom}"),
        ):
            scan = subprocess.run(
                [str(scanner), "scan", "source", option, f"--config={config}",
                 "--no-resolve", "--no-call-analysis", "--all-vulns", "--format=json"],
                cwd=work, capture_output=True, text=True, timeout=_LIMITS["OSV_SCAN_TIMEOUT_SECONDS"], check=False,
            )
            # OSV returns 1 for findings. Other failures leave the scan unverified.
            # Raw output can contain dependency names from the private repository.
            if scan.returncode not in {0, 1}:
                print(f"{lockfile}: OSV scan unverified (exit {scan.returncode})")
                return 2
            counts, blocking = summarize(json.loads(scan.stdout))
            summary = ", ".join(f"{severity} {count}" for severity, count in counts.items())
            print(f"- `{lockfile}` (OSV exit {scan.returncode}): {summary}; blocking {blocking}")
            blocked |= blocking > 0
    return int(blocked)


if __name__ == "__main__":
    try:
        sys.exit(main(*sys.argv[1:]))
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        print("OSV scan unverified: could not read or scan both dependency inputs", file=sys.stderr)
        sys.exit(2)
