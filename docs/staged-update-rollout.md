# Root promotion of a signed update archive

Percentage rollout is not accepted. A release is published to the website
after the five-platform dial gates, as one manifest for every client. Forcing
non-iOS clients is a later, separate decision made after production readback.

This change does not sign an archive, write `update.json`, create a bucket,
or call a production host. Tests use local fixtures only.

## Two operations

Signing stays in the release build. The build calls
`scripts/ci/promote_in_pipeline.sh archive-only` and does not publish the
root object.

Root promotion is the manual workflow
`.github/workflows/promote-update-rollout.yml`. It runs only from `master`
via `workflow_dispatch`. Inputs are the candidate version, the baseline
version, `force` (`soft` or `hard`), and `force_reason` when force is hard.
The workflow admits that policy and exits. It has no cloud credentials and
no permission to write the root.

`soft` is the post-dial website manifest: a prompt on every platform, with
no `forcePlatforms` list. `hard` requires `forceReason` of `security` or
`incompatible` and names every platform except iOS. iOS stays a prompt for
a client that understands `forcePlatforms`.

## Three readers

| Reader | What it honors | Hard manifest from this publisher |
| --- | --- | --- |
| 1.3.174 | `force` only | Global, including iOS |
| Current client | `forcePlatforms` limits `hard` | Non-iOS hard, iOS prompt |
| Publisher | Rejects `ios` in the list and a hard value with no list | iOS is never a member |

There is no manifest that forces already-shipped 1.3.174 desktops and also
leaves 1.3.174 iOS on a prompt, because that client does not read
`forcePlatforms`. A hard root is therefore only admissible when that legacy
behavior is accepted. Until then the published root stays soft.

`rollout`, `percent`, and `salt` are not manifest fields. A retired
percentage variable fails the candidate step and the promotion script
before any rewrite.
