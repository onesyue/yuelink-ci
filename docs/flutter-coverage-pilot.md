# Bounded Flutter coverage A/B pilot

This is a diagnostic candidate for W12.3, not a production runner replacement,
release receipt, complete-suite result, or demonstrated performance improvement.
The manual `flutter-coverage-pilot.yml` workflow invokes the frozen implementation
in a separate diagnostic job. The production coverage runner, release workflow
and source-attestation workflow are unchanged.

## Frozen inputs and acceptance

`tests/fixtures/flutter-coverage-pilot-20261019.json` freezes application
`473af71077c61db9e42170b304bce1fc446c9077`, builder base
`113c864b869d6b206a06d6ed5fe138a6f657bd69`, Flutter 3.47.7 / Dart 3.13.5 and
framework/engine hashes. The existing runner and machine-protocol helper hashes
are checked before execution. The initialized source checkout must have that
exact HEAD, clean tracked files and exact recursive Gitlinks.

The 36 files are the previously recorded 32-file storage crash prefix, minus
its separately assigned macOS keychain test, plus the custom-rules/parser/ledger
proofs and two observed slow files (`panel_api` and connections fallback).
The sorted newline-terminated inventory hash is
`45efd35b18d868b7aa4ea14d380c2834e6c6d7957c70cc8fc3b358fa0e1187f6`.

Both variants use four new detached worktrees, offline locked pub resolution,
UTC, concurrency one inside each Flutter process, coverage, the unchanged
machine-protocol parser, a 300-second group limit and a 30-second kill grace.
Each process starts after removal of its disposable worker's compiler cache.

- A: 36 single-file processes.
- B: five singleton isolation boundaries (storage schema pair, custom rules,
  subscription parser and fork ledger), plus four groups of at most eight files.
  Eight is an experimental upper bound, not a production recommendation.

A and B must execute every selected file and have identical visible test-name /
file / skip-status multisets. Every started test requires a successful terminal
event. Hidden loading/framework events are validated but are not counted as
visible passing cases. The union of instrumented and covered lines, branches and
functions must be identical. Missing reports, unsuccessful commands, incomplete
protocols, escaped source paths, case/skip changes or coverage loss fail closed.
Source-only files may emit empty coverage, but the complete sample may not.

The existing full-suite coverage ratchet is deliberately not applied to a
36-file sample. Instead, the sample compares A/B coverage unions. Any later
full-suite candidate must still satisfy the production inventory, floor, native
platform assignments, merged coverage and coverage ratchet. A diagnostic cannot
replace those gates.

## Invocation after the coordinator releases the heavy-work window

Inspect the manifest without executing Flutter:

```sh
python3 scripts/flutter-coverage-pilot.py --describe
```

Execution requires Linux, GNU `time` at `/usr/bin/time`, `timeout`, Git,
initialized exact-source Gitlinks, the frozen Flutter SDK on PATH, and a warm
locked pub cache. The script does not install dependencies or download packages.
Use an existing Linux environment with measured CPU/memory limits; do not run it
on production hosts. Keep each output directory outside the source and new.

```sh
/path/to/yueto/scripts/heavy-run.sh \
  python3 /path/to/pilot/scripts/flutter-coverage-pilot.py \
  --source /path/to/frozen-yuelink --output /path/to/evidence/pilot-ab --order AB

/path/to/yueto/scripts/heavy-run.sh \
  python3 /path/to/pilot/scripts/flutter-coverage-pilot.py \
  --source /path/to/frozen-yuelink --output /path/to/evidence/pilot-ba --order BA
```

These are separate paired runs; do not execute them concurrently. The reversed
order exposes warm-cache/order effects. A pair starts each variant with fresh
worktrees, while shared SDK and pub caches remain warm. If local Linux is ARM64
or x64 emulation, report that fact and compare within-environment A/B ratios;
do not present it as a matched native GitHub x64 baseline. A later explicitly
scheduled CI diagnostic uses these same inputs and commands. No automatic
workflow dispatch, cancellation or release publication is provided.

Evidence includes SDK/input identity, host/cgroup resource bounds, selected
groups, complete JSONL and LCOV, per-command exit/elapsed time and GNU time's
CPU/maximum-RSS data. GNU time's RSS is process-family accounting, not a sampled
aggregate peak for all four concurrent workers. Machine-event timestamps remain
available in JSONL; they do not by themselves isolate compiler overhead.
`comparison.json` exists only after both variants validate and compare, and
explicitly contains `release_acceptance: false`. Raw command evidence survives a
failure; absence of comparison.json is not success. Never reuse its directory.

## Candidate validation and limits

The unit suite exercises the real CLI description, manifest mutation refusals,
terminal/case/skip controls, coverage mutations, and on Linux real detached Git
worktrees plus real timeout/GNU time command execution with only the expensive
Flutter executable substituted. That Linux case also injects engine exit,
missing terminal events and missing coverage, verifies source cleanup, and
ensures the complete 36-file A/B inventories are compared. Native macOS skips
that GNU-time case; only its actual Linux execution counts as evidence.

No real Flutter A/B performance run has been performed for this candidate.
Use the paired pilot to decide whether further investigation is justified; only
a later unchanged-denominator full suite can validate production applicability.

## Manual native Linux diagnostic

The coordinator integrates and dispatches this workflow only after the frozen
1.3.140 release completes. It has no push, PR, schedule, workflow-call or release
trigger, and never publishes a source proof or release artifact. A fixed
concurrency group queues another manual invocation instead of cancelling an
active run. One invocation performs **A, B, B, A exactly once**, on the same
GitHub-hosted Ubuntu 24.04 x64 job. There is no repeated-trial loop or matrix.

The workflow freezes source `473af71077c61db9e42170b304bce1fc446c9077`, baseline
builder `113c864b869d6b206a06d6ed5fe138a6f657bd69` and pilot implementation
`9965321062a98a273fb92d8cd00cdc004f165866`. The workflow itself has a newer commit,
recorded separately in `execution.json`; do not describe that as a run of the
old builder workflow. The pilot verifies unchanged baseline helper hashes and
the exact Flutter/framework/engine/Dart tuple. No user input can substitute a
floating source or SDK. Recursive submodules and the signed native QUIC source
export are checked before execution.

Source access reuses the existing source-attestation job's read-only
`SRC_DEPLOY_KEY` authorization. The pinned checkout action receives a fixed
GitHub Ed25519 known-host key, strict SSH checking, full history and
`persist-credentials: false`; its temporary authentication is removed before
tests, and a real Git-config guard rejects retained SSH/header credentials.
The GitHub host key was read from the official metadata API on 2026-10-19. This
diagnostic adds no App/PAT configuration, fallback path or permission grant.
When the main source-access chain migrates to the GitHub App under the existing
security work item, migrate this diagnostic in the same reviewed batch.

SDK installation reuses the pinned, digest-verifying setup action. The existing
lockfile-keyed pub cache is warmed once before either timed pair. Each disposable
worker still resolves with `--offline --enforce-lockfile`. GNU time is required;
the workflow installs the distribution package only if the hosted image lacks
it, then records its version and host resources. The workflow never builds a
local emulator, downloads an app release package or changes the formal gate.

The job has a **120-minute total ceiling** and the combined experiment step a
**105-minute ceiling**, leaving up to 15 minutes for setup/evidence handling.
The pilot's existing 300-second group timeout, 30-second kill grace and four
workers remain fixed. These are failure limits, not promised completion times:
if many groups approach their individual limit, the overall ceiling can expire
first. A timeout is a failed diagnostic, never partial acceptance or permission
to raise the production gate's limits. Raw results upload on normal success or
failure; a runner loss or hard job cancellation can still prevent upload.

After both pair-level comparators pass, the workflow reparses the actual JSONL
and LCOV with the same frozen helpers. It compares case-name/file/skip multisets
and instrumented/hit unions across **all four variants**, not only within each
pair, and rejects changed SDK or CPU/memory resource bounds. Missing second-pair
reports, terminal events, LCOV or successful pair receipts cannot create the
aggregate `DIAGNOSTIC_ABBA_PASSED` receipt. All receipts explicitly retain
`release_acceptance: false`. Artifacts include successful or partial raw data,
GNU-time accounting, host facts and input identities for seven days.

The current exact-source baseline is run `39188122863`: its complete coverage
step took **81m11s** on 2026-10-19. The 36 pilot files have 464 passing and two
macOS-skipped visible cases in that Linux execution. Their protocol-duration
sum is 1,209.674 seconds across concurrent processes; 982.663 seconds precede
initial hidden loading completion. Loading includes compilation and framework
startup and is not isolated compiler CPU time. These observations justify the
bounded experiment; they do not establish a speedup or replace full-suite
coverage. The earlier 73m20s run used a different source and is not this baseline.

After integration and the coordinator's release-window check, dispatch manually:

```sh
gh workflow run flutter-coverage-pilot.yml --repo onesyue/yuelink-ci --ref master
gh run list --repo onesyue/yuelink-ci --workflow flutter-coverage-pilot.yml \
  --event workflow_dispatch --branch master --limit 1 \
  --json databaseId,headSha,status,url
```

Bind the returned run to the integrated workflow commit before accepting its
artifact. Do not dispatch the side branch while the current release is frozen.
Offline workflow tests execute the real shell wiring and frozen report
comparators with only the expensive pilot invocation substituted. They include
first-pair failure, removed second invocation, wrong source, changed cases/skips,
missing terminals/LCOV, lost coverage, changed resources and retained Git
credentials. They are wiring evidence, not a real Flutter benchmark.
