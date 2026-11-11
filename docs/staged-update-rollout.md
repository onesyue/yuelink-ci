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

`soft` is a prompt on every platform, with no `forcePlatforms` list and no
`platformForce`. A hard request writes global `force: hard` and `forceReason`
(`security` or `incompatible`) and does not write `forcePlatforms` or
`platformForce`. That forces every platform, including iOS. Shipped 1.3.174
reads `force`, so the same global value forces it too. There is no
`forceDeadline` on a new root. Percentage rollout is rejected.

The archive helper is `scripts/apply-update-force.py`. `--force hard` stays
global. A selective archive policy is still `--platform-force hard`, a reason,
and an explicit non-iOS asset list. Omitted platforms stay prompts.
`policy/update-force.json` is the checked-in global hard policy for its named
version.

## Three readers

| Reader | What it honors | Manifest from a global hard request |
| --- | --- | --- |
| 1.3.174 | `force` and `forceDeadline` only | Hard on every platform, including iOS |
| Current client | `force: hard` on every platform; `platformForce` only when the global force stays soft | Hard on every platform, including iOS |
| Publisher | Emits global hard with no `forcePlatforms`; still rejects percentage fields and a deadline | No platform list |

Historical signed manifests may still contain `rollout` or a global hard
`force`. The shared verifier still checks those objects. New producers emit
global hard and do not emit a percentage.
