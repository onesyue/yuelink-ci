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
no `forcePlatforms` list and no `platformForce`. A hard request is operator
intent. The signed global `force` stays `soft`. The manifest sets
`platformForce` to `hard` for every platform except iOS, with `forceReason`
of `security` or `incompatible`. There is no `forceDeadline`.

## Three readers

| Reader | What it honors | Manifest from a hard request |
| --- | --- | --- |
| 1.3.174 | `force` and `forceDeadline` only | Prompt on every platform, including iOS |
| Agreed new client | `platformForce: hard` on the listed platforms; iOS stays a prompt | Non-iOS hard, iOS prompt |
| Publisher | Rejects global hard, a deadline, rollout fields, and any iOS name | iOS is never a member |

Shipped 1.3.174 does not read `platformForce` or `forcePlatforms`. The first
migration of those installs can only prompt, on desktop and on iOS. A new
root is refused when global `force` is hard or a deadline is present,
because that client would force iOS as well. Draft yuelink#122
(`ad68ef80`) parses `platformForce` and keeps iOS a prompt, including for a
historical global hard. It is not merged. Shipped 1.3.174 does not parse
`platformForce`. The producer tests do not execute that Dart suite.

Historical signed manifests may still contain `rollout` or a global hard
`force`. The shared verifier still checks those objects. The new producer
and this root admission do not emit or admit them.
