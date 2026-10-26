# YueLink public build mirror

Public build and release control for the private `onesyue/yuelink` application.
This repository contains no application source. [sync-build.sh](sync-build.sh)
generates the public build workflow from the private release workflow.

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

The builder's [shared verifier](scripts/verify-source-attestation.sh)
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

Other GitHub preflight reads use [gh-read-retry.py](scripts/gh-read-retry.py):
three attempts, 60 seconds per attempt and 150 seconds total, plus at most one
second to reap the process group. Only explicit HTTPS GET/HEAD transport failures
retry. HTTP, certificate/signature, malformed-content and unknown failures are
fatal. Failed stdout is discarded and artifact attempts use private directories.
Dispatch, push, tag mutation and promotion are never replayed by this helper.

## Source gates and evidence reuse

The exact-source attestation covers master ancestry, the complete unreleased
Dart format delta, Android signing, Flutter analysis/import rules, CocoaPods
residue, workflow policy, the full Flutter suite with a reviewed floor of 2044
tests, security scanning, Wintun hashes, release metadata/schema, full-history
Gitleaks, production-target govulncheck, real Linux TUN transfer, GSettings and
missing-schema behavior, macOS integration/proxy IPC/preferences, service request
and race tests, and Windows durability. Windows also builds/packages its DLL and
runs three startup/traffic/shutdown cycles with engine leases. Real listening
and closed sockets verify refusal/rebind; a timeout is not port-release evidence.

Flutter coverage inventories every tracked test (at least 500 files). Its
[runner](scripts/run-flutter-coverage.py) selects the admitted execution plan and
up to four exact-source workers within CPU, memory and operator limits; unknown
capacity permits only one compatibility worker. Dependencies remain locked and
offline. Each group has a five-minute deadline and must emit a successful terminal
event and LCOV report. Source-only reports may be empty, but aggregate application
coverage must pass the unchanged ratchet. Missing execution/reports, engine errors,
source mutation or merge failures fail the gate. Only directory-group timeouts
may split into two halves, each attempted once; other failures are not retried.
The complete step has a 100-minute deadline and retains the four known crash
cases. Historical failures remain in Git and their original run receipts.

Flutter 3.47.8 / Dart 3.13.7 uses a source-bound singleton plan and a fresh compiler
cache per file. Selection requires the exact private-workflow SDK pin and rejects
absent, symlinked or mismatched feedback plans. Source commit, test blobs, runtime
tree, lockfile and all four SDK identity fields must match. Retained reviewed
group plans also enforce their baseline case/skip and coverage union; the previous
3.47.7 fixtures remain unchanged.

The final source-SHA proof has GitHub build provenance. Schema2 additionally
binds successful executed Flutter/service-race receipts to source, builder,
run/attempt, SDK/compiler and lock hashes. Stable preflight repeats verification
with the explicit OIDC issuer, predicate, exact workflow/builder policy and
installed Linux x64 toolchains. Only Flutter analysis, full coverage tests and
service Go race are reusable. A requested reuse failure aborts; stale proofs,
changed runtimes, failed runs and bad signatures never authorize a skip. Core
full Go race, fresh vulnerability scans, credentials, tag/environment checks,
native artifacts and promotion remain mandatory. Branch dispatch keeps full
preflight; local fixtures do not establish end-to-end release acceptance.

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

The manifest watchdog's reviewed signed floor is
[update-manifest-v1.json](tests/fixtures/update-manifest-v1.json). It rejects
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

```bash
challenge=$(openssl rand -hex 32)
gh workflow run r2-lock-attestation.yml -R onesyue/yuelink-ci \
  -f version=X.Y.Z -f source_commit="$SOURCE_SHA" \
  -f candidate_sha256="$CANDIDATE_SHA256" \
  -f builder_commit="$BUILDER_SHA" -f build_run_id="$BUILD_RUN_ID" \
  -f challenge="$challenge"
```

`minimumSystemVersion` is optional only for the `macos-universal` asset: one to
three ASCII numeric components, each one to three digits, signed with the asset.
Unknown fields and other-platform extensions are rejected. Install the compatible
Ops/site verifier before using it. The producer derives the floor from the exact
Runner target; a release-history link or old installer is not an OS fallback.
The [isolated fixture](tests/fixtures/macos-system-floor-20261008) uses only a test key.

## Policy checks and Actions permissions

Run `python3 -m unittest discover -s tests -p 'test_*.py' -v`. Cases must be
`unittest.TestCase` methods; the discovery contract rejects silently omitted
pytest functions. Policy CI also runs pinned actionlint, including ShellCheck of
workflow `run:` blocks, and `shellcheck --severity=warning` on repository scripts.

All external `uses:` references require full 40-character SHAs. Dependabot
maintains immutable pins through reviewed weekly PRs. Local or dynamically
resolved actions are intentionally absent; adding one requires policy review.
The dated 2026-08-21 repository readback records `allowed_actions=selected`,
`sha_pinning_required=true`, `github_owned_allowed=true` and `verified_allowed=false`.
GitHub-owned actions use their dedicated switch. External patterns are the
following exact closed set:

```
android-actions/setup-android@*
onesyue/yuelink-ci@*
```

The contract checks every workflow/composite `uses:` against this closure and
rejects mutable refs, unknown repositories and stale or extra documented patterns.
