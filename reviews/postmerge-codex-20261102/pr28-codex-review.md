APPROVE

- `.github/workflows/build.yml:408–417`: No correctness, security, or gate-weakening issue evident. `failure()` preserves the existing failed status; `if-no-files-found: ignore` only tolerates missing artifacts. Uploads are restricted to the two per-group patterns with three-day retention.
- `.github/workflows/build.yml:408`: Runs after any earlier failure, not exclusively a coverage timeout; harmless but broader than the step name.

Review limited to the supplied diff; repository context was unavailable. This change does not address the separate failed-policy-CI merge issue.
