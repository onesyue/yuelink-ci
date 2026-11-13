#!/usr/bin/env bash
# Shared annotated-tag binding validation for release planning and dial baselines.
set -euo pipefail
REF_NAME="${1:?stable release tag required}"
TRIGGER_COMMIT="${2:-}"
REPOSITORY=onesyue/yuelink-ci
[[ "$REF_NAME" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 2
if [ -n "$TRIGGER_COMMIT" ] && [[ ! "$TRIGGER_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  exit 2
fi
ref_json="$(gh api "repos/$REPOSITORY/git/ref/tags/$REF_NAME")"
tag_object_sha="$(jq -er '.object.sha' <<<"$ref_json")"
if ! { [ "$(jq -er '.object.type' <<<"$ref_json")" = tag ] &&
       [[ "$tag_object_sha" =~ ^[0-9a-f]{40}$ ]]; }; then
  echo "::error::public release ref must be an annotated tag"
  exit 1
fi
if [ -z "$TRIGGER_COMMIT" ]; then
  TRIGGER_COMMIT="$(gh api "repos/$REPOSITORY/git/tags/$tag_object_sha" --jq '.object.sha')"
  [[ "$TRIGGER_COMMIT" =~ ^[0-9a-f]{40}$ ]] || exit 1
fi
tag_json=''
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
  tag_json="$(gh api "repos/$REPOSITORY/git/tags/$tag_object_sha")"
  if jq -e --arg tag "$REF_NAME" --arg builder "$TRIGGER_COMMIT" '
      .tag == $tag and .object.type == "commit" and
      .object.sha == $builder and .verification.verified == true and
      .verification.reason == "valid"
    ' <<<"$tag_json" >/dev/null; then
    break
  fi
  # Only an explicit pending verification may consume the wait budget.
  jq -e --arg tag "$REF_NAME" --arg builder "$TRIGGER_COMMIT" '
    .tag == $tag and .object.type == "commit" and
    .object.sha == $builder and .verification.verified == false and
    (.verification.reason == "gpgverify_unavailable" or .verification.reason == "gpgverify_error")
  ' <<<"$tag_json" >/dev/null || {
    echo "::error::public tag content or signature rejected"
    exit 1
  }
  [ "$attempt" -eq 12 ] || sleep 5
done
jq -e --arg tag "$REF_NAME" --arg builder "$TRIGGER_COMMIT" '
  .tag == $tag and .object.type == "commit" and
  .object.sha == $builder and .verification.verified == true and
  .verification.reason == "valid"
' <<<"$tag_json" >/dev/null || {
  echo "::error::public tag is not exact builder + verified/valid"
  exit 1
}
tag_message="$(jq -er '.message' <<<"$tag_json")"
if ! { [ "$(grep -Ec '^ATTESTED_SOURCE_COMMIT=[0-9a-f]{40}$' <<<"$tag_message")" -eq 1 ] &&
       [ "$(grep -Ec '^SOURCE_ATTESTATION_RUN_ID=[1-9][0-9]*$' <<<"$tag_message")" -eq 1 ]; }; then
  echo "::error::public signed tag lacks one closed source binding"
  exit 1
fi
source_commit="$(sed -nE 's/^ATTESTED_SOURCE_COMMIT=([0-9a-f]{40})$/\1/p' <<<"$tag_message")"
attestation_run_id="$(sed -nE 's/^SOURCE_ATTESTATION_RUN_ID=([1-9][0-9]*)$/\1/p' <<<"$tag_message")"
jq -n --arg tag "$REF_NAME" --arg tag_object "$tag_object_sha" \
  --arg builder "$TRIGGER_COMMIT" --arg source "$source_commit" \
  --arg attestation_run "$attestation_run_id" \
  '{tag:$tag, tag_object:$tag_object, builder:$builder, source:$source, attestation_run:$attestation_run}'
