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
- B: the five existing early singleton boundaries (storage schema pair, custom
  rules, subscription parser and fork ledger), followed by the original four
  batches with old group7 split in place into prefix6/file7/file8:11 commands.
  Historical pilot996 has the original nine-command B shape.
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
active run. An explicit `diagnostic_scope=abba` invocation performs **A, B, B, A exactly once**, on the same
GitHub-hosted Ubuntu 24.04 x64 job. There is no repeated-trial loop or matrix.

The workflow freezes source `473af71077c61db9e42170b304bce1fc446c9077`, baseline
builder `113c864b869d6b206a06d6ed5fe138a6f657bd69`. ABBA selects schema2 pilot
`33f897007b144b411a6b0f0137276da2e27be8b3`; group7 retains historical
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
gh workflow run flutter-coverage-pilot.yml --repo onesyue/yuelink-ci --ref master -f diagnostic_scope=abba
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


## Bounded group7 follow-up (default manual mode)

Run39203863860 at workflow65be2c0 failed in AB/B group7: after all12 reset-operation
store tests and hidden tearDownAll passed, the Flutter tester reported a
segmentation fault during finalization; the enclosing command timed out at300s.
The eighth suite never began; no group LCOV/final done or AB/BA aggregate acceptance
exists. Individual A/21–28 commands completed with74 successes and2 existing skips.
These facts reject the current grouping, but do not establish an engine cause,
isolation fix, speedup or whole-suite result.

The manual selector defaults to `group7`; choose `abba` explicitly for the schema2
four-variant candidate. No source, SDK or file-list override is accepted. Both modes
retain source473/baseline113c/SDK3.47.7 and read-only authority, with mode-specific
immutable pilot revisions: group7 retains996 and ABBA uses the signed schema2 P. The production build/source-attestation workflows are unchanged.

Group7 runs precisely the existing eight-file B group7 once, serially, in one fresh
worker. It preserves coverage, --machine, --concurrency=1, compiler-cache reset and
300s timeout/30s kill grace/350s outer watchdog. A complete success must match the
previous A corpus and yields NOT_REPRODUCED, then stops. A different or incomplete
failure stops as DIAGNOSTIC_FAILED. Only the exact prior signature (exit124, seven
ordered suites, all prior prefix identities/skips, successful hidden tearDownAll,
its loading sentinel's single native finalization error, no done/LCOV) permits the
isolation control. That control uses a second fresh worker and exactly three serial
commands: first six files, reset-operation store alone, last latch file alone.
There are at most four Flutter test commands, never a retry or a second ABBA run.

The successful control must match the prior76-case identity/skip multiset and17654-row
LCOV instrumentation/hit union using the frozen actual parsers. SHA256 expectations
are canonical JSON (sorted keys, compact separators, ASCII escaping); identity rows
are sorted [file,name,skipped,multiplicity], and coverage rows are sorted
[file,counter-kind,counter-key,hit]. They were derived from run39203863860 artifact
12165665386 A/21–28. Their case digest is60f97696b06dbea7c8fd401300525a68e5f3002074773cbde2ddfaf9c1054474;
coverage digest isbf27a06a11049742d6cd6005eff8891e843cef9de8700eb571eb10237ed6e862.
The inline reference also binds each original JSONL/LCOV hash, original execution/input
identities and A/B grouping hashes. A separate canonical reference digest rejects
missing or changed reference fields. It is a reviewed prior-data reference, not a
new manifest or a runtime download; the complete original raw artifact remains the
audit source. No absent-reference fallback or narrower case-count-only success exists.

Group7 has a25-minute step/40-minute job ceiling, preserving ABBA's105/120-minute
ceilings. Setup uses the existing pinned SDK/cache stages; neither mode downloads a
client package. Group7 writes input/reference/resource/timing/report/coverage data
and an explicit non-release comparison status; partial evidence always uploads.
Preflight failures before output creation still remain failed workflow steps with
execution/host diagnostics. Only REPRODUCED_WITH_ISOLATION_CONTROL_PASS supports a
later isolation candidate; it is not DIAGNOSTIC_ABBA_PASSED, a performance result or
release acceptance. All statuses retain release_acceptance=false.

A group7 run omits the former other three concurrent workers and preceding
subscription-parser command. Resource/history differences remain recorded limitations;
a non-reproduction does not refute the original failure. The workstation has no
cached exact Linux x64 SDK; macOS ARM64 and Linux3.47.6 are not substitutes.

After separate coordinator approval of a signed workflow revision, dispatch once:

```sh
gh workflow run flutter-coverage-pilot.yml --repo onesyue/yuelink-ci \
  --ref <approved-workflow-ref> -f diagnostic_scope=group7
```

Bind the resulting run/head to that exact approved revision. This source change does
not authorize dispatch, downloading a local SDK, raising resource/time limits or
rerunning the whole ABBA experiment. Offline tests execute the extracted consumer,
real machine/coverage parsers and real main receipt handling with only expensive
Flutter/worktree setup substituted; they are not a native Flutter measurement.


## Candidate schema2 in-place isolation (not yet full-ABBA qualified)

Run39206971980 at workflow022da403 reproduced the original eight-file group7
finalization fault, then passed exactly prefix6/file7/file8 with the prior
identity/skip/coverage union. That supports a bounded grouping candidate only.
The schema2 pilot manifest adds two in-place boundaries: reset-operation store
and TUN latch. Its36-file inventory, source473, baseline113c, SDK3.47.7, previous
five early-isolated files, four workers and command deadlines are unchanged.
Schema1 is not interpreted as this new experiment; historical996 keeps its own
original manifest and behavior.

A still has36 single-file commands. B now has11 commands: only old B7 is split
into its first six files, its seventh alone and eighth alone. No file is moved
to the early-isolated list and no batch is repacked across the old boundary.
The flattened declared order and each file's single occurrence stay identical.
The unchanged modulo-four scheduler assigns those three new batches to workers
3/0/1; the former last batch now has index10 on worker2 instead of index8 on
worker0. This changes worker history/load compared with the successful serial
eight-file experiment; the grouping is not fully qualified until actual ABBA.

Freeze/sign this pilot commit P before a separate workflow successor W pins its
exact full SHA for explicit ABBA. Group7 remains bound to original996 and the
old raw reference. Do not use a floating pilot ref, retarget group7 or infer a
new pilot identity from the historical builder baseline. No release workflow,
source attestation, SDK pin, command interface or automatic trigger changes.

After independent source validation and a separate coordinator approval, the
admission is one manual full36-file A/B/B/A run with the existing105-minute
step/120-minute job, unchanged300s+30s command limit and four workers. Require
both successful pair receipts and the cross-pair DIAGNOSTIC_ABBA_PASSED result,
all466 visible cases (464 successes and2 existing skips), complete terminals,
nonempty equal LCOV instrumentation/hit unions and identical SDK/resources.
Preserve failed/partial raw evidence; do not retry automatically, enlarge
limits or shrink the corpus. Even a successful diagnostic remains
release_acceptance=false and says nothing about sourcef959/release141 or the
full client suite without their separate gates. This candidate has no timing
or performance claim.


The exact schema2 pilot candidate P is
`33f897007b144b411a6b0f0137276da2e27be8b3`. Its workflow successor selects this immutable
revision only when diagnostic_scope=abba. Group7 still selects original
`9965321062a98a273fb92d8cd00cdc004f165866`; its schema1 reference is not
reinterpreted. Offline workflow fixtures reconstruct the exact original996
module and manifest bytes with pinned SHA256 checks before replacing expensive
CLI execution. This keeps historical consumer tests valid even in a shallow
policy checkout, with no Git history fetch or added fixture/helper file.

P and its workflow successor are CODE_READY only until the separately scheduled
native/Linux validation succeeds. No full36-file ABBA or performance result has
been obtained for schema2. The completed group7 observation remains attributed
to workflow022/original996.
