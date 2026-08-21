# YueLink public build mirror

This repository contains no application source. Its workflow is generated
from the private `onesyue/yuelink` release workflow by `sync-build.sh`; each
public signed annotated tag carries an `ATTESTED_SOURCE_COMMIT=<40hex>` and
`SOURCE_ATTESTATION_RUN_ID=<id>` machine binding. The public build checks out
the exact builder revision's shared `verify-source-attestation.sh`, then
independently authenticates the referenced run identity, exact proof artifact,
full gate closure and GitHub provenance before it checks out that private
commit. It refuses any `BUILT_SOURCE_COMMIT` mismatch. A same-named private tag
is release-input validation, not build authority.

Stable releases are fail-closed on the Android upgrade key, immutable R2
credentials, and private-source deploy key before a public tag is created.
Apple Developer ID/notarization and Windows Authenticode are optional for
direct sideload distribution: each platform publishes its exact signing mode
and install notice. Missing or failed platforms never borrow an older binary,
and a partial release cannot produce or promote an updater manifest.

The public workflow publishes every successful current-tag platform
independently with checksums, provenance, source-commit identity and truthful
sideload notices. Only a complete five-platform / nine-artifact set can create
an unsigned updater-manifest candidate. The updater signing seed remains in
the private signing plane, which authenticates the current signed root, signs
the candidate, enforces a monotonic version transition, publishes the signed
archive/root, and verifies the live artifacts.

The independent public manifest watchdog also keeps a reviewed, signed minimum
root at `tests/fixtures/update-manifest-v1.json`. It rejects a live root whose
semantic version or `publishedAt` predates that floor even when the old root's
Ed25519 signature and expiry remain valid. After every stable promotion, copy
the exact signed root into that fixture in a reviewed follow-up and retain the
previous root as a replay regression fixture. Never discover the floor from
the CDN being authenticated. This ratchet does not remove retained installers
for manual recovery; publishing a decreasing updater root is already forbidden
by the private signer's monotonic transition contract.

From a clean, up-to-date `master` checkout:

```bash
./sync-build.sh ../yuelink
git diff -- .github/workflows/build.yml
./sync-build.sh --check ../yuelink

# Before the private source tag is cut, attest its exact 40-character master
# commit on public runners. This is the canonical source gate when private
# Actions cannot start because of billing; local test output is never accepted.
SOURCE_SHA=$(git -C ../yuelink rev-parse HEAD)
gh workflow run source-attestation.yml -R onesyue/yuelink-ci \
  -f source_sha="$SOURCE_SHA"
# Wait for every source gate and the final provenance job to succeed.

# Then create/push the signed private source tag and mirror it. release.sh downloads
# the proof, checks source/tag/builder/run/gate identity, and verifies its
# GitHub artifact attestation before it can create the public tag.
./release.sh vX.Y.Z
```

`release.sh` verifies the private tag is a GitHub-verified signed annotated
release input, the generated workflow exactly matches that source commit,
every mandatory
sideload secret is configured, and a successful source attestation from the
exact current yuelink-ci workflow commit binds all canonical gates to the
private tag's peeled commit. The builder commit itself must be signed and
GitHub-verified before the script can create a signed annotated public tag.
The remote tag namespace is handled idempotently: an existing name succeeds
only when it is annotated, peels to the exact builder commit and has a
GitHub `verified/valid` signature; an uncertain push is followed by a fresh
remote read. New and existing tags are both polled for GitHub verification,
and the script never deletes, overwrites or re-tags a failed immutable name.
Before any push, a retained local annotated tag must also match the complete
expected annotation bytes; matching only the builder commit is insufficient.
Both repositories therefore authenticate the selected source and builder
revisions instead of relying on an unsigned ref name or a successful push exit
status.

Every object below the R2 `v` prefix is now retained permanently under the
live, enabled Cloudflare indefinite bucket-lock rule
`yuelink-release-versioned-indefinite` (`prefix=v`). The historical
`prune-r2.yml` filename now runs a read-only archive/lock audit; it contains no
delete or overwrite path. There is no implicitly cleanable namespace. Any
future ephemeral area must have a separate explicit prefix, age and ownership
contract outside both locked `v` and `security/` prefixes.

Immediately before private promotion, `r2-lock-attestation.yml` reads that
same live rule with the existing configuration-read-only Cloudflare token and
emits a 30-minute proof bound to the exact stable version, private source,
candidate SHA-256, candidate builder/run and a fresh 32-byte random challenge.
The proof receives GitHub-hosted artifact provenance; it contains no
Cloudflare token, R2 write key or updater signing seed. The proof workflow
commit must be the exact same signed `master` commit named by the candidate's
builder tag/run; a later control-plane commit is never accepted. The private
promoter also pins the workflow identity and rechecks Sigstore provenance and
expiry before immutable archive publication and again immediately before the
root CAS. This is a short-lived point-in-time attestation rather than a second
live Cloudflare query; promotion starts immediately and an expired proof is
replaced, never extended or accepted.

```bash
challenge=$(openssl rand -hex 32)
gh workflow run r2-lock-attestation.yml -R onesyue/yuelink-ci \
  -f version=X.Y.Z -f source_commit="$SOURCE_SHA" \
  -f candidate_sha256="$CANDIDATE_SHA256" \
  -f builder_commit="$BUILDER_SHA" -f build_run_id="$BUILD_RUN_ID" \
  -f challenge="$challenge"
```

The source attestation is equal to or stronger than the private source jobs:
it checks master ancestry, the complete unreleased Dart format delta, Android
release-signing contract, Flutter analysis, architecture imports, CocoaPods
residue, workflow policy, the full Flutter suite with a reviewed floor of 2044
tests, the release security scanner, Wintun hashes, release metadata and
manifest schema, full-history Gitleaks, core and service production-target
govulncheck, macOS integration tests, and the Windows durability probe. The
final JSON proof is uploaded under the exact source SHA and receives GitHub
build-provenance attestation. Updater signing material is intentionally not
copied into this public repository; the protected promotion step verifies the
real key and remains independently mandatory.

Every external `uses:` reference in all public workflows is pinned to a full
40-character commit SHA. The repository-level Actions policy enforces that
pinning, and `.github/dependabot.yml` maintains those
immutable pins with reviewed weekly pull requests. Local or dynamically
resolved actions are intentionally absent; adding one requires revisiting the
repository policy before it can merge.

The live repository policy was read back on **2026-08-21** as
`allowed_actions=selected`, `sha_pinning_required=true`,
`github_owned_allowed=true`, and `verified_allowed=false`. GitHub-owned actions
are covered by that dedicated switch; the selected external patterns are the
following exact closed set, with no unused allowance:

```
android-actions/setup-android@*
onesyue/yuelink-ci@*
```

The contract inventories all seven workflow files plus the composite Flutter
action (eight action-bearing YAML definitions), checks every `uses:` against
this policy, and rejects mutable refs, an unknown external repository, or a
stale/extra documented pattern.
