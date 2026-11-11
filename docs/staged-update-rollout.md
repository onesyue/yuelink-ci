# Staged exposure of signed update archives

This is reviewable code, not an activated rollout. No Worker, route, bucket,
credential or environment is created by this PR. No production request was used
for its tests. Building/signing an archive and changing client exposure are
separate operations; the global `update.json` object is never written by the
new promotion workflow.

## What the percentage measures

Released client 1.3.174 creates a new HttpClient/IOClient and sends no stable
installation ID, account identity, version header or persistent cohort cookie.
Its direct, proxy and mirror attempts can use different exits. The selector
therefore hashes **trusted Cloudflare network IP + a stable campaign salt**.
A shared NAT groups installations; an exit change can change a cohort. Ten
percent of synthetic network identities is not ten percent of installations.
The requested real approximately 10% installation acceptance remains unmet with
this legacy request interface. No documentation or receipt should claim it is
met by these tests.

The reviewed v1.3.174 source declares these two manifest endpoints:

| Endpoint | Required future routing |
| --- | --- |
| `https://yuetong.app/update.json` | Same selector and same plan bucket |
| `https://xn--jdu596h.com/update.json` | Same selector before the mirror's origin proxy |

Package `/dl/` paths are not manifest routes. Routing only the primary host, or
placing selection after a mirror proxy that hides the original IP, is not valid
cohort delivery. Direct/proxy exit differences still remain even with both
routes configured correctly. This PR leaves `routes: []`, Workers.dev/preview
URLs disabled, and empty host/fallback configuration; it is not deployable as an
active production selector without explicit configuration review.

## Immutable trust and soft-only behavior

The manual workflow reads the original `vVERSION/update.json` and
`vVERSION/promotion-binding.json` using a dedicated archive-read identity. It
uses the existing pinned Ed25519/schema/compatibility verifier, checks expiry,
and preserves the baseline's version/time/compatibility floor. Both manifests
must be globally non-hard. A candidate with client `rollout.percent` other than
100 is rejected; absent rollout is compatible. Otherwise server 10% followed
by a newer client's 10% would accidentally select approximately 1%.

The existing source-attestation proof and complete 19-payload closure checks
remain mandatory. The binding's original `builderCommit`, `buildRunId` and
`sourceAttestationRunId` must match its successful tagged build and the signed
archive's source/checksum evidence. The payload list must reproduce the signed
SHA256SUMS anchor and its exact filename inventory. The original archive
identity is copied unchanged into `archiveIdentity`; the current promotion
workflow/run gets a separate `promotion` object. A new promotion run never
replaces the original build identity or re-signs/rewrites an archive to make an
identity mismatch disappear.

The plan binds baseline and candidate versions, immutable keys, exact original
byte SHA-256 values and expiry timestamps. Python performs signature admission;
the Worker checks these bound hashes and serves the original signed bytes
unchanged. It does not implement another signature or manifest-schema verifier.
The plan-writer admission path is consequently a trust boundary and must be
restricted to the reviewed manual workflow.

## Separate storage and credentials

The existing `yuelink-release` environment is tag-only (`v*`), and its name does
not prove approval protection. The new `yuelink-rollout` environment must be
reviewed independently and restricted to master. Its policy must be configured
before setting `ROLLOUT_CONFIGURED=network-ip-reviewed`; an absent activation
value or missing credential fails before any storage request. This PR performs
none of that configuration.

Required deployment settings are `ROLLOUT_PLAN_BUCKET`,
`ROLLOUT_ARCHIVE_ENDPOINT`, and `ROLLOUT_PLAN_ENDPOINT`. The plan bucket must be
separate from `yuelink-dist`. Required independent credentials are:

- `ROLLOUT_ARCHIVE_ACCESS_KEY_ID` / `ROLLOUT_ARCHIVE_SECRET_ACCESS_KEY`: object
  read access scoped to the release archive bucket.
- `ROLLOUT_PLAN_ACCESS_KEY_ID` / `ROLLOUT_PLAN_SECRET_ACCESS_KEY`: object read/write
  access scoped to the separate rollout-plan bucket, with no release-bucket
  authority.

R2 tokens support bucket scope; this design does not claim a key-prefix ACL.
There is no fallback to repository-wide `R2_KEY_ID`, `R2_APP_KEY` or signing
credentials. No signing seed is mapped into the rollout job. Runtime code checks
that credentials differ, but their actual permissions require a separate
operational review; names alone cannot prove scope.

The Worker has two native R2 bindings, `ARCHIVES` and `PLANS`. Its reviewed code
uses only `get`; HTTP has no write/admin endpoint, dynamic code, identity logs
or outbound proxy path. **Native R2 bindings themselves grant read/write
capability**, so this is code-enforced read behavior, not a platform read-only
ACL. No real deployment/permission acceptance has been performed.

## Manual plan changes and concurrency

The manual-only workflow is `.github/workflows/promote-update-rollout.yml`.
It runs only from master, never a tag, PR, workflow-run listener or Build
completion. It requires explicit candidate version, baseline version, percentage
0–100, the current unquoted plan ETag (or `absent` for bootstrap), and the exact
acknowledgement `network IPs are not installations`. It builds, installs and
signs nothing.

The plan object is `stable/network-ip.json` in the independent plan bucket.
Bootstrap requires the baseline to equal the existing signed root bytes.
Subsequent percentage changes retain the baseline, candidate, binding identity
and salt. A new campaign can use a completed 100% candidate as its baseline, or
a strictly newer replacement after a 0% withdrawal with the old baseline.
Changing a partially exposed campaign's candidate is rejected.

The workflow first compares the operator-reviewed ETag, then uses native S3
conditional PUT (`If-Match`, or `If-None-Match: *` for bootstrap), requires an
object receipt and compares exact readback bytes. Unknown read errors cannot
become bootstrap. A concurrent stale writer fails; there is no unconditional
retry and no root/archive write path. Workflow concurrency also serializes plan
jobs, but CAS remains mandatory against other actors. Local native R2 tests
exercise actual conditional-put/null and conditional-get/no-body semantics,
in addition to real shell argument/failure tests at the S3 CLI boundary. These
are different interfaces: Miniflare does not prove production S3 CAS behavior,
and no actual R2 write or deployment acceptance has been performed.

## Serving and withdrawal

Every response, including 404/405/503, sends `Cache-Control: no-store, private`,
`CDN-Cache-Control: no-store` and `Cloudflare-CDN-Cache-Control: no-store`. The
Worker never uses the Cache API or lets request conditionals return a shared
cached cohort body. Deployment must preserve these headers and avoid overriding
cache rules at both manifest hosts.

The Worker requires platform-supplied CF metadata and a valid CF-Connecting-IP;
forwarded headers, cookies and query parameters are not identity inputs. Missing
identity/context returns baseline, even at 100%. Bad/missing/expired candidate
bytes fall back to baseline; a bad/expired baseline yields 503, including at
100%. Missing or malformed plans use the old root only when it matches the
separately reviewed `LEGACY_ROOT_SHA256` and `LEGACY_ROOT_EXPIRES_AT` deployment
values. Missing or broken fallback configuration fails closed. Those values are
never learned from the object being authenticated.

Withdrawing to 0% changes exposure only. It cannot downgrade installed clients,
and a client watermark may reject the older baseline after having seen the
candidate. Legacy clients do not reveal platform identity; globally soft-only
manifests are required, while the new client's iOS prompt-only behavior remains
a separate Lane C change.

## Validation boundary

Python tests verify real historical 1.3.172/1.3.173 signatures without contacting
production. Explicit synthetic fixtures exercise checksum/binding state and
transport bytes; they are not represented as signed releases. Worker tests use
pinned Miniflare/workerd with local native R2, test 0/100, deterministic network
identity, 10,000 synthetic identities near 10%, bad identity/context, malformed
plans, corrupt/expired objects, unchanged signed transport, cache isolation,
HTTP write rejection and conditional-write races. Policy CI installs the exact
locked runtime on pinned Node 24.19.0. Actual routes, real credential scope,
installed-client behavior and real installation percentages remain outside this
PR-only validation and must not be reported as passing.
