# CI failure and timeout semantics

The `ci/fail-fast-20261027` candidate adds finite job timeouts across the
manifest, release audit, source attestation and visual receipt workflows.
The `govulncheck` matrix uses `fail-fast: true`, which cancels its queued or
running matrix siblings after a matrix job fails.

The source attestation `abort-on-gate-failure` job depends on all five gate
jobs. GitHub Actions waits for those dependencies to finish before evaluating
the failure handler. Its cancellation request is therefore terminal cleanup;
it does not interrupt an ongoing Flutter suite immediately when another gate
fails. Remaining gate execution is bounded by each job timeout. No claim of
immediate cancellation across independent gate jobs is made.

The attestation job keeps its existing required gate dependencies and is
eligible only when they succeed. The cleanup job holds only `actions: write`,
executes no checked-out source code, and targets the current run ID.
