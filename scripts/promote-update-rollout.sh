#!/usr/bin/env bash
# Manual plan admission only. Never signs, uploads archives, or writes update.json.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ "${GITHUB_EVENT_NAME:-}" = workflow_dispatch ] && [ "${GITHUB_REF:-}" = refs/heads/master ] || {
  echo 'rollout plan changes require manual dispatch from master' >&2; exit 1;
}
[ "${ROLLOUT_CONFIGURED:-}" = network-ip-reviewed ] || {
  echo 'independent rollout environment, bucket, credentials and routes have not been reviewed' >&2; exit 1;
}
[ "${NETWORK_IP_ACKNOWLEDGEMENT:-}" = 'network IPs are not installations' ] || {
  echo 'explicit acknowledgement of network-IP scope is required' >&2; exit 1;
}
[[ "${ROLLOUT_VERSION:-}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] &&
[[ "${ROLLOUT_BASELINE_VERSION:-}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] &&
[[ "${ROLLOUT_PERCENT:-}" =~ ^(0|[1-9][0-9]?|100)$ ]] &&
[[ "${GITHUB_SHA:-}" =~ ^[0-9a-f]{40}$ ]] &&
[[ "${GITHUB_RUN_ID:-}" =~ ^[1-9][0-9]*$ ]] || { echo 'invalid explicit rollout inputs' >&2; exit 1; }
[[ "${EXPECTED_PLAN_ETAG:-}" = absent || "${EXPECTED_PLAN_ETAG:-}" =~ ^[0-9a-f]{32}$ ]] || {
  echo 'expected plan ETag must be its exact unquoted digest, or absent for initial creation' >&2; exit 1;
}
[[ "${ROLLOUT_PLAN_BUCKET:-}" =~ ^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$ ]] &&
[ "$ROLLOUT_PLAN_BUCKET" != yuelink-dist ] || { echo 'a separate rollout plan bucket is required' >&2; exit 1; }
for key in ROLLOUT_ARCHIVE_ACCESS_KEY_ID ROLLOUT_ARCHIVE_SECRET_ACCESS_KEY ROLLOUT_PLAN_ACCESS_KEY_ID ROLLOUT_PLAN_SECRET_ACCESS_KEY; do
  [ -n "${!key:-}" ] || { echo 'independent archive-read and plan-write credentials are required' >&2; exit 1; }
done
[ "$ROLLOUT_ARCHIVE_ACCESS_KEY_ID" != "$ROLLOUT_PLAN_ACCESS_KEY_ID" ] || {
  echo 'archive and plan credentials must be distinct' >&2; exit 1;
}
for key in ROLLOUT_ARCHIVE_ENDPOINT ROLLOUT_PLAN_ENDPOINT; do
  [[ "${!key:-}" =~ ^https://[0-9a-f]{32}\.r2\.cloudflarestorage\.com$ ]] || {
    echo 'reviewed R2 endpoint configuration is required' >&2; exit 1;
  }
done
unset AWS_SESSION_TOKEN AWS_PROFILE AWS_DEFAULT_PROFILE
export AWS_DEFAULT_REGION=auto AWS_REQUEST_CHECKSUM_CALCULATION=when_required AWS_RESPONSE_CHECKSUM_VALIDATION=when_required
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
archive_read() {
  AWS_ACCESS_KEY_ID="$ROLLOUT_ARCHIVE_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$ROLLOUT_ARCHIVE_SECRET_ACCESS_KEY" \
    aws s3api get-object --bucket yuelink-dist --endpoint-url "$ROLLOUT_ARCHIVE_ENDPOINT" "$@"
}
plan_api() {
  AWS_ACCESS_KEY_ID="$ROLLOUT_PLAN_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$ROLLOUT_PLAN_SECRET_ACCESS_KEY" \
    aws s3api "$@" --bucket "$ROLLOUT_PLAN_BUCKET" --endpoint-url "$ROLLOUT_PLAN_ENDPOINT"
}
# Missing is accepted only for explicit initial creation and an actual S3
# NoSuchKey response. Auth/network/unknown failures cannot become bootstrap.
if plan_api get-object --key stable/network-ip.json "$work/previous-plan.json" > "$work/plan-meta.json" 2> "$work/plan-error"; then
  [ "$EXPECTED_PLAN_ETAG" != absent ] || { echo 'plan already exists; read and explicitly name its ETag' >&2; exit 1; }
  actual_etag="$(jq -er '.ETag | select(type == "string") | ltrimstr("\"") | rtrimstr("\"")' "$work/plan-meta.json")"
  [ "$actual_etag" = "$EXPECTED_PLAN_ETAG" ] || { echo 'plan changed since operator review' >&2; exit 1; }
  cas=(--if-match "\"$EXPECTED_PLAN_ETAG\"")
else
  [ "$EXPECTED_PLAN_ETAG" = absent ] && grep -Eq 'An error occurred \((NoSuchKey|404|NotFound)\) when calling the GetObject operation:' "$work/plan-error" || {
    echo 'current plan cannot be authenticated/read; no write attempted' >&2; exit 1;
  }
  rm -f "$work/previous-plan.json"
  archive_read --key update.json "$work/initial-root.json" > /dev/null
  cas=(--if-none-match '*')
fi
archive_read --key "v$ROLLOUT_BASELINE_VERSION/update.json" "$work/baseline.json" > /dev/null
archive_read --key "v$ROLLOUT_VERSION/update.json" "$work/candidate.json" > /dev/null
archive_read --key "v$ROLLOUT_VERSION/promotion-binding.json" "$work/binding.json" > /dev/null
build_run="$(jq -er '.buildRunId | select(type == "string" and test("^[1-9][0-9]*$"))' "$work/binding.json")"
archive_builder="$(jq -er '.builderCommit | select(type == "string" and test("^[0-9a-f]{40}$"))' "$work/binding.json")"
archive_source="$(jq -er '.sourceCommit | select(type == "string" and test("^[0-9a-f]{40}$"))' "$work/binding.json")"
source_run="$(jq -er '.sourceAttestationRunId | select(type == "string" and test("^[1-9][0-9]*$"))' "$work/binding.json")"
python3 "$SCRIPT_DIR/gh-read-retry.py" api "repos/onesyue/yuelink-ci/actions/runs/$build_run" > "$work/build-run.json"
python3 "$SCRIPT_DIR/prepare-update-rollout.py" --directory "$work" \
  --version "$ROLLOUT_VERSION" --baseline-version "$ROLLOUT_BASELINE_VERSION" --percent "$ROLLOUT_PERCENT" \
  --acknowledgement "$NETWORK_IP_ACKNOWLEDGEMENT" --promotion-builder "$GITHUB_SHA" --promotion-run "$GITHUB_RUN_ID"
# Reuse the original archive's proof identity, never this promotion run's ID.
bash "$SCRIPT_DIR/verify-source-attestation.sh" "v$ROLLOUT_VERSION" "$archive_source" "$archive_builder" "$source_run"
# Existing verifier authenticates the signed manifest, checksum anchor and all
# 19 payloads. This script does not invent a second signature/closure verifier.
python3 "$SCRIPT_DIR/verify-release-closure.py" "$work/candidate.json"
if ! plan_api put-object --key stable/network-ip.json --body "$work/next-plan.json" \
    --content-type application/json --cache-control no-store "${cas[@]}" > "$work/put-result.json"; then
  echo 'conditional plan write failed; no unconditional retry or archive/root write is permitted' >&2
  exit 1
fi
jq -e '.ETag | type == "string" and length > 0' "$work/put-result.json" > /dev/null || {
  echo 'conditional write returned no object receipt; refusing to report success' >&2; exit 1;
}
plan_api get-object --key stable/network-ip.json "$work/readback.json" > /dev/null
cmp -s "$work/next-plan.json" "$work/readback.json" || { echo 'plan readback differs; inspect concurrent updates' >&2; exit 1; }
{
  echo "Network-IP rollout plan updated to $ROLLOUT_PERCENT% for version $ROLLOUT_VERSION."
  echo 'This is not an installation percentage. No root or signed archive was rewritten.'
  echo 'Withdrawing exposure does not downgrade installed clients; client watermarks can reject the older baseline.'
  echo "Original archive build run: $build_run; source-attestation run: $source_run."
  echo "Independent plan promotion run: $GITHUB_RUN_ID."
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
