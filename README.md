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

## Verification-only dial checks

`YueLink dial verification` is generated from the release workflow's five-platform
dial matrix by `sync-build.sh`. Same-repository pull requests use the reviewed
`policy/dialgate-source.json` fields `sourceRepository`, `sourcePr`, and
`sourceCommit`; branch dispatches require `source_pr` and an exact `source_sha`.
The private PR head must still equal that commit when binding begins. Every
platform then checks out that exact snapshot, and all raw reports and verdicts
must name it and the pinned baseline. Fork PRs and tag dispatches cannot enter.

This workflow has no package publication or signing jobs and has a distinct name
from `Build YueLink`, so release-completion listeners do not consume its runs.
The same bound candidate also runs ten scoped Linux test targets covering the
updater (including its startup-wiring contract), Dart crash receiver and last-good
configuration recovery with the canonical pinned Flutter SDK, UTC and concurrency 2.
That Linux job first runs `go test -p 2 ./dialgate/...` from the same candidate's
`core` directory with canonical Go 1.27.4 and `GOTOOLCHAIN=local`. Ordered raw
attempt telemetry and gate regressions retain a separate log in the same artifact;
a failed Go test fails the job. The final integrated source must be explicitly bound
before these new checks execute; directory review alone is not execution evidence.
The same source also runs an unsigned `:app:compileDebugKotlin` job with the
canonical Java/Flutter/Android SDK versions. Its pre-execution task-graph guard
rejects release, signing, application packaging, installation, publication and
native compilation tasks, while allowing required library JAR and Flutter asset
compilation. The retained graph and log must show an executed Kotlin task; skipped,
cached and NO-SOURCE tasks do not count. These checks establish compilation and
Dart behavior, not Android native crash-handler runtime behavior.
The exact job set is source binding, Linux client tests, Android Kotlin compilation,
five-platform dial matrix, and dial summary.
Policy CI pull-request runs skip the live manifest read; that read is restricted
to master pushes and explicit manual runs on master.
Its artifacts are verification evidence, not release authorization. Change the
reviewed source manifest explicitly when testing a different integration head.

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
An explicit public security overlay preserves master PR50's Go 1.27.4 upgrade
when a frozen private template still uses 1.27.3; already upgraded 1.27.4 templates
remain unchanged and unknown versions fail closed. Source and builder/toolchain
identities are distinct: old Go 1.27.3 verification does not prove the new builder.

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
