# YueLink public builder runbook

This is the operations entry point for the public release executor. Local
policy checks and workflow ownership are in [README](../README.md).

## Release an exact source revision

From a clean, current `master` checkout:

```bash
./sync-build.sh ../yuelink
git diff -- .github/workflows/build.yml
./sync-build.sh --check ../yuelink

SOURCE_SHA=$(git -C ../yuelink rev-parse HEAD)
gh workflow run source-attestation.yml -R onesyue/yuelink-ci \
  -f source_sha="$SOURCE_SHA"
# Wait for every source gate and the final provenance job to succeed.
# Then create/push the signed private source tag and mirror it:
./release.sh vX.Y.Z
```

Hosted source attestation is mandatory; local test output is never accepted.
`release.sh` verifies the signed, GitHub-verified private source tag and builder
commit, exact generated-workflow parity and required sideload
secrets before creating the signed public tag. Its annotation binds
`ATTESTED_SOURCE_COMMIT=<40hex>` and `SOURCE_ATTESTATION_RUN_ID=<id>`.

The builder's [shared verifier](../scripts/verify-source-attestation.sh)
authenticates the run, exact proof artifact, complete gate closure and
GitHub artifact attestation before private checkout. `BUILT_SOURCE_COMMIT` must match;
a same-named private tag alone is not build authority. Before any push, a local
annotated tag must match the complete expected annotation bytes.

An existing name succeeds only when it is annotated, peels to the exact builder
commit and has a GitHub `verified/valid` signature; an uncertain push is followed by a fresh
remote read. New and existing tags are polled for GitHub verification. The script
never deletes, overwrites or re-tags a failed immutable name. Tag-existence reads
have at most three attempts with one- and two-second pauses; transport failure
is never interpreted as absence. Tag mutations are not retried.

Other GitHub preflight reads use [gh-read-retry.py](../scripts/gh-read-retry.py):
three attempts, 60 seconds per attempt and 150 seconds total, plus at most one
second to reap the process group. Only explicit HTTPS GET/HEAD transport failures
retry. HTTP, certificate/signature, malformed-content and unknown failures are
fatal. Failed stdout is discarded and artifact attempts use private directories.
Dispatch, push, tag mutation and promotion are never replayed by this helper.

## Source gates and evidence reuse

Hosted proof binds exact master ancestry, source/builder identities, complete
format/analyze/import checks, Android signing, workflow/security policy, Flutter
coverage, core/service race tests and native Linux/macOS/Windows behavior.
The full test floor and action closure remain in [README](../README.md) and
[source-attestation policy](../tests/test_source_attestation_contract.py).
A timed-out or unexecuted test is not evidence of success.

[Flutter coverage](../scripts/run-flutter-coverage.py) inventories tracked tests
(at least 500 files), admits workers only within known capacity and keeps locked
offline dependencies, per-group deadlines, terminal events and LCOV requirements.
Unknown capacity permits one compatibility worker. Only admitted directory-group
timeouts may split once; missing reports, source mutation, engine or merge errors
fail. Preserve the unchanged aggregate coverage ratchet and known crash cases.
The pinned SDK/compiler, source/test/runtime/lock identities must match the plan;
a stale or symlinked feedback plan cannot authorize execution or reuse.

Schema2 proof binds successful receipts to source, builder, run/attempt and
SDK/compiler/lock hashes, with GitHub provenance. Stable preflight repeats issuer,
predicate and exact workflow/builder verification. Only Flutter analysis, full
coverage and service Go race are reusable; requested reuse failure aborts.
Core Go race, vulnerability scans, credentials, tag/environment checks, native
artifacts and promotion remain mandatory. Branch dispatch uses full preflight.

## Packages, signing and promotion

Stable preflight requires the Android upgrade key, immutable R2 credentials and
private-source deploy key. Apple Developer ID/notarization and Windows
Authenticode are optional for direct sideloading; each platform publishes its
actual signing mode and installation notice. A missing platform never borrows
an old binary. Successful current-tag platforms publish their own checksums,
provenance and source identity, but only the complete five-platform/nine-artifact
set can create an unsigned updater candidate.

The protected final `release` step runs `scripts/ci/promote_in_pipeline.sh`:
authenticate the current signed root, sign the complete candidate, enforce a
monotonic version transition, publish the immutable archive/root and verify live
artifacts. The updater seed stays in protected Secrets, never source or artifacts.
Private/local promotion is recovery-only. The manual credential-recovery workflow
verifies both public signing identities and emits only two-recipient age
ciphertext to its recovery bucket and one-day encrypted artifact; no PR trigger
or plaintext upload is permitted.

In the full Yue.to workspace, use the maintained
client recovery procedure (`yuelink/docs/RUNBOOK.md` in the workspace)
for interrupted release jobs and forward repair. This procedure assumes the full Yue.to workspace is available. The normal publication authority
remains this repository's exact public run; the linked procedure does not
authorize a second signer, root downgrade, or extra workstation package-download
acceptance.

The manifest watchdog's reviewed signed floor is
[update-manifest-v1.json](../tests/fixtures/update-manifest-v1.json). It rejects
older semantic versions or `publishedAt`, even with valid signatures/expiry.
After promotion, review the exact signed root into that fixture and retain the
prior root as a replay regression. Never learn the floor from the CDN being
verified. Retained installers remain available for manual recovery; the updater
root may never decrease.

Every R2 object below `v` is permanently retained by the live enabled indefinite
bucket-lock rule `yuelink-release-versioned-indefinite` (`prefix=v`). The historical
`prune-r2.yml` performs a read-only archive/lock audit; it cannot delete or overwrite.
There is no implicitly cleanable namespace. Any future temporary namespace needs
an explicit prefix, age and ownership contract outside locked `v` and `security/`.

Normal promotion queries that live lock. Manual fallback uses
`r2-lock-attestation.yml` and the configuration-read-only Cloudflare token to issue
a 30-minute, GitHub-attested proof bound to version, private source, candidate
SHA-256, builder/run and a fresh 32-byte challenge. Its workflow commit must be
the candidate's exact signed master builder, not a later revision. It contains
no Cloudflare token, R2 write key or signing seed. The private promoter checks
workflow identity, provenance and expiry before archive publication and again
before root CAS. Start immediately; replace an expired proof rather than extend
it. This is point-in-time evidence, not a second live Cloudflare query.

Use the exact inputs in [r2-lock-attestation.yml](../.github/workflows/r2-lock-attestation.yml)
for manual recovery; do not improvise a proof or replay an expired challenge.

`minimumSystemVersion` applies only to the `macos-universal` asset: one to three
ASCII numeric components, one to three digits each, signed with the asset.
Unknown fields/other-platform extensions fail. Deploy compatible Ops/site readers
first; derive the floor from the exact Runner target. Preserve the
[isolated fixture](../tests/fixtures/macos-system-floor-20261008).

## Health checks and common incidents

A release is healthy only when the exact source/builder run completes its
required jobs, artifact/provenance verification and protected promotion.
`manifest-health.yml` checks the signed updater; `prune-r2.yml` audits archive
retention read-only. Neither a green source commit nor a partial platform set
establishes a complete release. Use the existing public run's receipts.

| Incident | Response |
|---|---|
| Generated workflow parity fails | Fix the private template, regenerate with `sync-build.sh`, then check parity and re-attest the exact identities. |
| Attestation/signature/environment check fails | Stop before tag/promotion; never substitute local test output or an older proof. |
| Terminal transient build failure | Follow the client's bounded same-run recovery; preserve source/builder/tag and finite observation deadline. |
| Missing platform / immutable-byte conflict | Do not borrow old binaries or overwrite an archive; use guarded recovery or a higher-version repair. |
| Signed root replays | Compare against the reviewed local fixture floor; never learn the floor from the CDN under test. |
| R2 lock proof expired | Obtain a fresh proof bound to the exact candidate; never extend its lifetime. |

## Rollback and secret locations

The signed updater root cannot decrease. Preserve published tags/objects and
use a higher-version repair with fresh source attestation. A retained installer
is only separately scoped manual recovery. Do not run a parallel private signer.

Actions repository/protected-environment Secrets hold `SRC_DEPLOY_KEY`,
`KEYSTORE_BASE64`, `KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD`,
`UPDATE_MANIFEST_ED25519_PRIVATE_KEY_B64`, `R2_KEY_ID` and `R2_APP_KEY`.
Public verification/recipient inputs are named
`UPDATE_MANIFEST_ED25519_PUBLIC_KEY_B64`, `YUEOPS_KEYVAULT_BREAKGLASS_RECIPIENT`
and `YUEOPS_KEYVAULT_VERIFIER_RECIPIENT`. Keep all secret values out of source,
logs and artifacts; the existing manual recovery workflow emits ciphertext only.

## Sources

Consolidated from README.md at base `dda16708a19405d0822a1862fc2ecb6b5a2ec519`,
[release.sh](../release.sh), [build workflow](../.github/workflows/build.yml),
[source attestation](../.github/workflows/source-attestation.yml),
[manifest watchdog](../.github/workflows/manifest-health.yml) and
[key-recovery workflow](../.github/workflows/backup-critical-workstation-keys.yml).
