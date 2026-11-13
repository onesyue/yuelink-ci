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

[build.yml](.github/workflows/build.yml) is the canonical release workflow.
[sync-build.sh](sync-build.sh) `--check` validates that file. It does not copy
a private template. Edit the public workflow in this repository.

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
[shared verifier](scripts/verify-source-attestation.sh), [public workflow self-check](sync-build.sh)
and the operational material consolidated from README.md at base
`dda16708a19405d0822a1862fc2ecb6b5a2ec519` into [RUNBOOK](docs/RUNBOOK.md).


## Dial baseline provenance

The shared dial workflow selects one baseline before its five platform jobs.
It freezes a UTC cutoff and selects the most recently published stable GitHub
release at that cutoff. The shared annotated-tag resolver authenticates the
public tag and its source/builder/attestation binding. The existing source
attestation verifier checks the exact run, proof and provenance. Selection also
requires the latest tag Build run's exact successful attempt and an explicitly
successful `Sign root manifest and promote (in-pipeline)` step. Missing,
pending, failed, skipped or contradictory evidence blocks the gate; it never
falls back to an older version. No production URL or host is queried.

`dial-baseline-receipt` retains the selected release, source, builder, tag object,
attestation attempt, Build attempt, promotion job/step and selection cutoff.
All five checkouts use the frozen source SHA; the outer platform table requires
the same baseline SHA. The workflow exports the receipt SHA-256 for audit.
This proves the latest public pipeline-promoted stable release, **not** the live
version after a possible manual rollback. Releases after the cutoff belong to a
later verification run. Historical v1.3.174 measurements remain historical
comparisons and are not relabelled as latest-stable evidence.

Stable baseline provenance is durable: future publication preserves the original
verified `source-attestation.json` and its public Sigstore bundle as release
assets before promotion. The selector requires both assets and validates their
exact source, builder, run attempt, gates, and signature through the shared
verifier. A present but partial or invalid asset pair fails closed; it cannot
fall back to Actions artifacts or an older release. The one-time signed proof
archive under `docs/release-proofs/v1.3.176/` bridges the existing stable release
without changing its assets. It is selected by the dynamically resolved tag,
not a pinned baseline. Releases with neither durable assets nor a migration
archive still require an available valid Actions artifact. The archive contains
public provenance bytes only; future releases use automatic durable assets.
