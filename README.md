# YueLink public build mirror

This repository contains no application source. Its workflow is generated
from the private `onesyue/yuelink` release workflow by `sync-build.sh`; each
public tag checks out the private repository's exact same tag.

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

# Then create/push the private source tag and mirror it. release.sh downloads
# the proof, checks source/tag/builder/run/gate identity, and verifies its
# GitHub artifact attestation before it can create the public tag.
./release.sh vX.Y.Z
```

`release.sh` verifies the private tag exists, the public tag does not, the
generated workflow exactly matches that private tag, every mandatory sideload
secret is configured, and a successful source attestation from the exact
current yuelink-ci workflow commit binds all canonical gates to the private
tag's peeled commit before it creates an immutable public tag.

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
