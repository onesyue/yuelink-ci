# YueLink public build mirror

This repository contains no application source. Its workflow is generated
from the private `onesyue/yuelink` release workflow by `sync-build.sh`; each
public tag checks out the private repository's exact same tag.

Stable releases are fail-closed before a public tag is created. The release
driver requires Android keystore values, Apple signing and notarization
values, Windows publisher certificate values, R2 credentials, and the private
source deploy key. There is no unsigned platform-binary exception.

The public workflow publishes immutable binaries, checksums, provenance, and
an unsigned updater-manifest candidate. The updater signing seed remains in
the private signing plane, which authenticates the current signed root, signs
the candidate, enforces a monotonic version transition, publishes the signed
archive/root, and verifies the live artifacts.

From a clean, up-to-date `master` checkout:

```bash
./sync-build.sh ../yuelink
git diff -- .github/workflows/build.yml
./sync-build.sh --check ../yuelink
./release.sh vX.Y.Z
```

`release.sh` verifies the private tag exists, the public tag does not, the
generated workflow exactly matches that private tag, and every stable signing
secret is configured before it creates an immutable public tag.
