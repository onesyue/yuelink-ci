APPROVE_WITH_NOTES

- `scripts/run-flutter-coverage.py:36`: Adds the test to the existing isolation list; no explicit test skip, failure suppression, or threshold relaxation appears in this diff. Full runner source is unavailable, so isolated execution, coverage inclusion, and failure propagation remain unverified.
- `tests/test_flutter_coverage_runner.py:35`: Fixture preserves the test count and updates the mock isolation list consistently. No correctness or security defect identified.

The prior yueto-ci#56 policy-CI failure is separate; #58’s fix does not establish validation of this PR.
