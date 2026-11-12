# YueLink 1.3.165 / R-52 path preparation

## Acceptance and scope

This preparation is complete when the release entrypoints, prerequisites and
workflow triggers are reviewed, this checklist is committed and a public PR is
opened. It does not certify a release. All client release builds and packaging
execute in public `onesyue/yuelink-ci`; the private repository supplies source and
the canonical build template, not an Actions release executor. Hosted runners
are intentional. Do not re-enable private workflows or introduce a hosted-runner
ban. No tag, signing-key change, publication or live floor advance belongs here.

## Non-publishing dry-run checklist

Run from the public builder repository root. `release.sh` has **no dry-run flag**;
invoking it can create and push an immutable signed tag and start publication.
Do not invoke it during preparation.

- [ ] Record the candidate public builder SHA and the exact private source SHA.
  Reuse evidence only for those identities. The final release owner must repeat
  parity against the finalized source if its template changes.
- [ ] Use an explicit absolute source path. The default `../yuelink` does not
  exist beside this preparation worktree. Confirm that the intended source
  checkout is clean and at the reviewed commit; do not consume concurrent edits.
- [ ] Check local tools: Bash, Git, `gh`, Python 3, `jq`, OpenSSL, `patch`,
  `cmp`, `diff`, and the configured Git signing/verification tooling. For lint,
  use ShellCheck and the Policy CI pinned actionlint. Confirm `gh` access to
  public builder metadata/secrets **names** and private source reads; do not
  print credentials or secret values.
- [ ] Run the non-publishing checks below. `--check` generates a temporary file
  and compares it; it does not replace the committed workflow.

```sh
SOURCE_REPO=/absolute/path/to/reviewed/yuelink
./sync-build.sh --check "$SOURCE_REPO"
bash -n release.sh sync-build.sh scripts/verify-source-attestation.sh scripts/verify-source-version.sh
shellcheck --severity=warning release.sh sync-build.sh scripts/verify-source-attestation.sh scripts/verify-source-version.sh
YUELINK_SRC="$SOURCE_REPO" python3 -m unittest discover -s tests -p test_runner_pin_contract.py -v
python3 -m unittest discover -s tests -p 'test_release_*behavior.py' -v
```

- [ ] Inspect release helper ownership: `scripts/gh-read-retry.py`,
  `scripts/verify-source-attestation.sh` and `scripts/verify-source-version.sh`
  live in this public repository. Build source binding checks out the public
  verifier into `.source-binding-tools` at the exact builder SHA. Platform
  verification and `scripts/ci/promote_in_pipeline.sh` come from the attested
  private **source checkout**, without executing private Actions.
- [ ] Check sync inputs: the private `.github/workflows/build.yml`, public
  `scripts/public-release-retry.patch` and
  `tests/fixtures/flutter-coverage-groups-20261028.json` must exist. Sync checks
  anchored checkout patches, SDK/cache identity and exact retry-patch context.
  A missing anchor or parity failure is a stop, never a skipped check.
- [ ] If the canonical template changes, use `./sync-build.sh "$SOURCE_REPO"`,
  review `.github/workflows/build.yml`, rerun `--check`, and commit/push the
  resulting builder before source attestation. Do not hand-edit generated YAML.

## Trigger map

| Workflow | Trigger | Release-lane effect |
| --- | --- | --- |
| `build.yml` / Build YueLink | Public `push.tags: ['v*']`; `workflow_dispatch` | Exact stable `vX.Y.Z` admission; source binding, preflight, platform builds and release/promotion. Tag filter alone does not admit prereleases. |
| `source-attestation.yml` | Manual `workflow_dispatch` with `source_sha` | Public proof for exact private source and builder; required before release. |
| `policy-ci.yml` | Pull request; push to `master`; manual dispatch | Repository policy checks. Optional manual `yuelink_sha` validates source version metadata. No packaging on this PR. |
| `prune-r2.yml` | Completed Build YueLink `workflow_run`; daily 18:30 UTC; manual dispatch | Read-only archive/lock audit; build-completion path requires success and trusted run identity. No deletion. |
| `manifest-health.yml` | Daily 02:17 UTC; manual dispatch | Signed-manifest and release-page watchdog. |
| `r2-lock-attestation.yml`, `backup-critical-workstation-keys.yml`, `ephemeral-signer-bridge.yml` | Manual dispatch only | Existing recovery helpers; not preparation steps. |

No workflow subscribes to GitHub `release` events. Creating/publishing a GitHub
Release page is not the packaging trigger. Android/Linux use `ubuntu-24.04`,
iOS/macOS use `macos-26`, Windows uses `windows-latest`; public attestation also
uses hosted Linux/macOS/Windows. The existing Ubuntu version pin protects the
Linux compatibility floor; it is not a ban on GitHub-hosted runners.

## Handoff to the authorized release lane (not executed by this PR)

1. Finalize source version `1.3.165+<build>`, `CHANGELOG.md`, and both
   `docs/releases/v1.3.165.md` and `docs/releases/v1.3.165.en.md`. Run the source
   release guard `dart run tool/automation/release_guard.dart --tag v1.3.165`.
   Use the committed exact source, including its submodules and required scripts.
2. Land all builder changes. Require clean public `master` with
   `HEAD == origin/master`, a locally verified signed builder commit and GitHub
   `verification.verified=true`, `reason=valid`. Check protected environment
   admission and required secrets by name: `SRC_DEPLOY_KEY`, `R2_KEY_ID`,
   `R2_APP_KEY`, `CLOUDFLARE_R2_CONFIG_TOKEN`, `KEYSTORE_BASE64`,
   `KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD`; promotion additionally needs
   the protected updater signing configuration described in [RUNBOOK](RUNBOOK.md).
   Apple/Windows publisher identities are optional sideload modes, not grounds
   to substitute an old platform binary.
3. Run the full public Policy CI gate and exact hosted source attestation:

   ```sh
   SOURCE_SHA=<final-reviewed-private-source-40-hex>
   gh workflow run source-attestation.yml -R onesyue/yuelink-ci --ref master -f source_sha="$SOURCE_SHA"
   gh run watch <source-attestation-run-id> -R onesyue/yuelink-ci --exit-status
   ```

   Require successful proof on the final builder SHA; a local test log cannot
   replace it. A later builder commit requires matching new evidence. The current
   workflow and verifier define the gates (README records owner decision B5).
4. After the authorized source lane pushes the signed private `v1.3.165` tag,
   require its exact source identity and GitHub verified/valid signature. Local
   signer trust must satisfy `verify-source-version.sh` (including fully trusted
   OpenPGP or authorized SSH signer). From the clean final public master run:

   ```sh
   ./release.sh v1.3.165
   gh run watch <public-build-run-id> -R onesyue/yuelink-ci --exit-status
   ```

   This checks exact tagged-template parity, source version/signature and proof,
   then creates/verifies the public signed tag. It never replaces an existing
   tag. Manual `build.yml` dispatch takes `-f source_sha=<40hex>` and looks up
   the green Source attestation run itself. `only` is optional, defaults to `all`, and a
   single-platform test is not a complete release. Do not dispatch as a dry run.
5. Require the complete platform/artifact set, provenance and successful signed
   in-pipeline promotion/readback. Retain the public run's receipts. Follow
   [RUNBOOK](RUNBOOK.md) for acceptance/recovery; immutable tags and archives
   cannot be overwritten, and the signed updater root cannot be downgraded.
   Ledger reconciliation is a separate authorized owner handoff via
   [reconcile-yuelink-pin.md](reconcile-yuelink-pin.md), outside this PR.

## Post-release floor PR only

[PR #40](https://github.com/onesyue/yuelink-ci/pull/40), “Advance verified update
floor to released v1.3.164”, merged as
`26811f90034b16b5aa65fbf332eef941d4199c7c`. It copied the verified live signed root
into `tests/fixtures/update-manifest-v1.json`, archived the prior 1.3.163 root,
and updated `OLDER_FIXTURE` in `tests/test_update_manifest_verifier.py`.

Only after a signed 1.3.165 release exists and release acceptance succeeds should
a separate PR authenticate its exact root with `scripts/verify-update-manifest.py`,
archive the current 1.3.164 root, advance the reviewed fixture and replay test,
and run the relevant verifier/policy suites. Never fabricate/resign fixture
bytes or learn the trust floor from an unverified CDN response. This preparation
leaves all live floor numbers, fixtures and replay-test pointers unchanged.

## Preparation evidence and limits (2026-11-05)

Reviewed public base `ae29321904b5ca1542fa30771a5145b9f81b844b` and private
committed template `8d68cc4585161f2d951a4a355461e3009f1beeac`, isolated from
concurrent uncommitted source release metadata. Generator parity and all four
runner contract tests passed with no skips. All ten referenced verification/
promotion script paths were present in their owning checkouts, as were the five
public helper/sync inputs listed above. No blocking path bug was identified.
The 27 release behavior tests passed with no skips. Bash syntax and ShellCheck
warning-level checks passed for the four entrypoint/verifier shell scripts;
Markdown local links, code fences and whitespace checks passed. These checks do
not establish final 1.3.165 source readiness or a signed release.

Parked: existing RUNBOOK source-gate prose still describes full coverage, while
README's B5 note and current source-attestation workflow define the newer gate
set. Existing build header comments also describe a private signer while the
release job performs public in-pipeline promotion. These stale comments are not
executable packaging dependencies; no broader documentation cleanup is included.
