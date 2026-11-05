# Item 7 acceptance contract

Deliver an offline, non-mutating reconciliation plan generator in yuelink-ci.
It must authenticate the stable manifest using the existing pinned verifier and
reviewed replay floor, reject expired/future manifests and unexpected release
version/source, and hash the exact signed bytes for yueto's native write-yuelink
command. Document this as the final release step, including Mac signing, native
lock/generation gates, fresh stable readback and release-state acceptance.
No yueto writes, publication or production calls are authorized in this lane.
Acceptance: behavioral tests for successful plans and fail-closed inputs, existing
verifier/release tests, lint, one local revertible commit. Linux validation is
required evidence but unavailable if Docker remains sandbox-blocked; report it.

Evidence: release.sh final notice only mentioned the fixture; yueto's read-only
scripts/release-state.py probe_yuelink compares version, source_commit and raw
manifest SHA-256 plus source HEAD/tag. scripts/release-yaml.py already provides
write-yuelink, advances generation and uses native mutation/registry locks.

Parked: none.

Lane ledger: primary owns all Item 7 edits in this worktree. Independent reviewer
is read-only, reviewing only the new plan generator, tests and final-step docs;
no source ownership, no tests rerun and no other repositories investigated.

Validation: new offline plan suite 10/10; existing manifest verifier 42/42;
release closure 6/6; policy discovery 3/3. Ruff check/format, bash -n,
ShellCheck warning severity and git diff --check passed. Independent static
review found no blockers; its stale temporary-file concern was fixed with
mktemp, cleanup trap and fail-fast shell instructions. Serena tools unavailable.
Docker socket access denied by sandbox, so Linux equivalence is unverified.
Full policy discovery was interrupted during unrelated fixture cleanup after
slow progress; it is NOT a pass. Detailed lane evidence/report live one level
above this worktree. No publication or production readback performed.
