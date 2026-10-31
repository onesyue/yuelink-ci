# YueLink public builder

Public build and release control for private `onesyue/yuelink`; this repository
contains no application source. Its workflows run on GitHub Actions and publish
immutable client packages to the `yuelink-dist` R2 bucket at `yuetong.app`.
[RUNBOOK](docs/RUNBOOK.md) owns release, recovery, health checks, common incidents
and secret locations.

## Local build and policy checks

There is no local application build here. From the repository root with the
existing Python/test toolchain:

```sh
./sync-build.sh --check ../yuelink
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The [policy workflow](.github/workflows/policy-ci.yml) also owns pinned actionlint
and ShellCheck commands. Tests are `unittest.TestCase` methods; discovery rejects
unreachable cases. For a small change, select the affected test module.

## Release contract

Finalize, review and sign the public builder and private source before requesting
hosted proof. Dispatch from the exact clean builder master; `release.sh` rejects
a dirty or stale checkout. Synchronization changes must be committed first:

```sh
SOURCE_SHA=$(git -C ../yuelink rev-parse HEAD)
gh workflow run source-attestation.yml -R onesyue/yuelink-ci \
  -f source_sha="$SOURCE_SHA"
```

Follow the runbook before creating any signed stable tag. Hosted attestation is
mandatory; local test output is never accepted. The exact source proof uses
GitHub artifact attestation with Flutter analysis and the retained source gates.
Owner decision B5 removed the full Flutter suite and coverage floor from release
attestation; the standalone coverage runner remains available. [release.sh](release.sh) binds source and builder,
verifies signatures, workflow parity and the complete attestation closure.
An existing name succeeds only with the exact annotated builder/tag identity
and valid GitHub verification; an uncertain push is followed by a fresh
remote read. The script never deletes, overwrites or re-tags an immutable name.

## Workflow ownership

[sync-build.sh](sync-build.sh) generates [build.yml](.github/workflows/build.yml)
from the private YueLink template with explicit private-source checkout patches.
The public workflow is the release executor; the private file remains the
canonical editable template and a gate input. Do not hand-maintain two builders
or delete the template. Check parity before every builder change.

The public [key-recovery workflow](.github/workflows/backup-critical-workstation-keys.yml)
is manual and hosted. The private counterpart supports an ephemeral recovery
runner and a different script path; it is not an interchangeable duplicate.
No new backup/drill task is introduced here.

## Actions permissions

All external `uses:` references require full 40-character SHAs. The dated
2026-08-21 readback recorded `allowed_actions=selected`,
`sha_pinning_required=true`, `github_owned_allowed=true` and
`verified_allowed=false`; it is not a fresh remote measurement.
The policy tests retain the following exact closed set:

```
android-actions/setup-android@*
onesyue/yuelink-ci@*
```

GitHub-owned actions use their dedicated switch. Local or dynamically
resolved actions are intentionally absent; adding one requires policy review.
`@*` permissions never permit mutable workflow pins.

Sources: [source-attestation contract](tests/test_source_attestation_contract.py),
[shared verifier](scripts/verify-source-attestation.sh), [sync generator](sync-build.sh)
and the operational material consolidated from README.md at base
`dda16708a19405d0822a1862fc2ecb6b5a2ec519` into [RUNBOOK](docs/RUNBOOK.md).
