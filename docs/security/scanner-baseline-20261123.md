# Scanner baseline, 2026-11-23

## Historical secret scan

Gitleaks 8.30.1 scanned a full mirror of `onesyue/yuelink-ci` with the repository's existing `.gitleaks.toml`. At the time of the scan, the mirror contained 607 reachable commits across branch, tag and pull-request refs. The lane checkout was shallow.

| History selected by `gitleaks git --redact` | Commits reported by scanner | Findings | New ignore entries |
| --- | ---: | ---: | ---: |
| `--log-opts='--all --diff-merges=first-parent'` | 602 | 0 | 0 |

The merge option includes changes introduced by merges. To reproduce from the lane checkout, pass the full mirror directory as the scan target and `--config .gitleaks.toml` explicitly.

## Workflow scan

At `67a58bc`, zizmor 1.33.1 scanned all 14 files in `.github/workflows` in its default offline mode. It reported 13 findings and exited with code 14. Online-only audits are outside these counts. Line numbers below refer to that revision.

| Rule | Severity | Count | Workflow and lines | Review and follow-up for the workflow owner |
| --- | --- | ---: | --- | --- |
| `secrets-inherit` | Medium | 1 | `build.yml:109`, plan | The plan workflow reads no named secrets. Remove the caller's `secrets: inherit`. |
| `secrets-inherit` | Medium | 3 | `build.yml:123,137,160`, validate, build, publish | The three calls forward all available caller secrets. Declare each required caller-supplied secret under the callee's `on.workflow_call.secrets` and map it by name in the matching job. Keep environment-owned credentials in the existing build and release environments. Replace the literal inheritance assertion in `test_public_release_workflow_contract.py` with checks for the three allowed sets. |
| `self-repository` | Low | 5 | `build.yml:105,119,133,155,183`, plan, validate, build, publish, dial_gate | All five paths name local reusable workflows. Migrate the five `uses` prefixes from `./.github/workflows/` to the scanner's explicit `$/.github/workflows/` form together with the source-attestation workflow-path parser and dial-gate contract tests. Verify that source attestation still covers each callee. |
| `dangerous-triggers` | High | 1 | `warm-release-caches.yml:7` | The plan job requires a successful same-repository source attestation dispatched on master. It parses a full commit ID from the run title, and the warm job checks out that revision. The scanner flags the trigger itself. Retain those restrictions and record a line-scoped exception with the trust rationale if preserving the completion trigger. |
| `github-env` | High | 1 | `warm-release-caches.yml:82`, Apple toolchain | The plan extracts a numeric Xcode version from the canonical build workflow and the step checks the developer directory exists. Set `DEVELOPER_DIR` directly through `env` on its macOS consumers and retain the directory check. |
| `github-env` | High | 2 | `warm-release-caches.yml:159,160`, Android toolchain | Both writes combine the runner SDK root with the numeric NDK version extracted from the build workflow. Pass the version to consuming Android steps and export `ANDROID_NDK_HOME` and `ANDROID_NDK` inside their shells before invoking the build tools. |
