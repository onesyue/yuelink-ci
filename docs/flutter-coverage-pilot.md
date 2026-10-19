# Bounded Flutter coverage A/B pilot

This is a diagnostic candidate for W12.1, not a production runner replacement,
release receipt, complete-suite result, or demonstrated performance improvement.
No workflow invokes it. The production coverage runner and workflows are unchanged.

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
