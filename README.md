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
./release.sh vX.Y.Z
```

`release.sh` verifies the private tag exists, the public tag does not, the
generated workflow exactly matches that private tag, and every mandatory
sideload secret is configured before it creates an immutable public tag.
