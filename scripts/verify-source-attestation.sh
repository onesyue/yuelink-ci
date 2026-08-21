#!/usr/bin/env bash
# Verify the one public source-attestation run named by a signed release tag.
# This is shared by release.sh (before tag creation) and build.yml (before any
# private source checkout), so the two trust boundaries cannot drift apart.
set -Eeuo pipefail

TAG="${1:?usage: verify-source-attestation.sh TAG SOURCE_COMMIT BUILDER_COMMIT RUN_ID}"
SOURCE_COMMIT="${2:?source commit is required}"
BUILDER_COMMIT="${3:?builder commit is required}"
RUN_ID="${4:?source-attestation run id is required}"
REPOSITORY=onesyue/yuelink-ci
WORKFLOW_PATH=.github/workflows/source-attestation.yml
WORKFLOW_IDENTITY=onesyue/yuelink-ci/.github/workflows/source-attestation.yml

[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] &&
  [[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] &&
  [[ "$BUILDER_COMMIT" =~ ^[0-9a-f]{40}$ ]] &&
  [[ "$RUN_ID" =~ ^[1-9][0-9]*$ ]] || {
  echo "::error::invalid source-attestation tag/source/builder/run identity" >&2
  exit 2
}
for command_name in gh jq mktemp find wc tr; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "::error::missing source-attestation verifier command: $command_name" >&2
    exit 1
  }
done

tmp_root="${TMPDIR:-/tmp}"
case "$tmp_root" in
  /*) ;;
  *) echo "::error::TMPDIR must be absolute" >&2; exit 2 ;;
esac
umask 077
snapshot=""
cleanup() {
  set +e
  if [[ "${snapshot:-}" == "$tmp_root"/yuelink-source-attestation-verify.* ]] &&
     [ -d "${snapshot:-}" ]; then
    find -P "$snapshot" -mindepth 1 -delete
    rmdir "$snapshot" 2>/dev/null || true
  fi
}
trap cleanup EXIT
snapshot="$(mktemp -d "$tmp_root/yuelink-source-attestation-verify.XXXXXX")"
run_json="$snapshot/run.json"
proof_dir="$snapshot/proof"
proof="$proof_dir/source-attestation.json"
mkdir -p "$proof_dir"

GH_PROMPT_DISABLED=1 gh api \
  "repos/$REPOSITORY/actions/runs/$RUN_ID" > "$run_json"
jq -e \
  --arg repository "$REPOSITORY" \
  --arg workflow "$WORKFLOW_PATH" \
  --arg source "$SOURCE_COMMIT" \
  --arg builder "$BUILDER_COMMIT" \
  --arg run "$RUN_ID" '
    (.id | tostring) == $run and
    .name == "Source attestation" and
    .display_title == ("Source attestation " + $source) and
    .path == $workflow and .event == "workflow_dispatch" and
    .status == "completed" and .conclusion == "success" and
    .head_sha == $builder and .head_branch == "master" and
    .repository.full_name == $repository
  ' "$run_json" >/dev/null || {
  echo "::error::source-attestation run identity does not match exact workflow/event/title/source/builder" >&2
  exit 1
}
RUN_ATTEMPT="$(jq -er '.run_attempt | tostring | select(test("^[1-9][0-9]*$"))' "$run_json")"

GH_PROMPT_DISABLED=1 gh run download "$RUN_ID" \
  -R "$REPOSITORY" \
  --name "yuelink-source-attestation-$SOURCE_COMMIT" \
  --dir "$proof_dir"
[ -f "$proof" ] && [ ! -L "$proof" ] &&
  [ "$(find -P "$proof_dir" -mindepth 1 -maxdepth 1 -type f | wc -l | tr -d '[:space:]')" = 1 ] &&
  [ -z "$(find -P "$proof_dir" -mindepth 1 -maxdepth 1 ! -type f -print -quit)" ] || {
  echo "::error::source-attestation artifact must contain exactly one regular proof file" >&2
  exit 1
}

jq -e \
  --arg source "$SOURCE_COMMIT" \
  --arg release "$TAG" \
  --arg builder "$BUILDER_COMMIT" \
  --arg run "$RUN_ID" \
  --arg attempt "$RUN_ATTEMPT" '
    type == "object" and
    (keys | sort) == ([
      "schemaVersion", "sourceRepository", "sourceSha", "releaseTag",
      "builderRepository", "workflowSha", "runId", "runAttempt", "gates"
    ] | sort) and
    .schemaVersion == 1 and
    .sourceRepository == "onesyue/yuelink" and .sourceSha == $source and
    .releaseTag == $release and
    .builderRepository == "onesyue/yuelink-ci" and
    .workflowSha == $builder and .runId == $run and .runAttempt == $attempt and
    (.gates | sort) == ([
      "source-master-ancestry",
      "android-release-signing-contract",
      "unreleased-dart-format",
      "flutter-analyze",
      "architecture-imports",
      "cocoapods-residue",
      "workflow-policy",
      "security-scan",
      "wintun-bundle",
      "release-metadata",
      "manifest-schema",
      "flutter-tests-floor-2044",
      "gitleaks-full-history",
      "govulncheck-core-service",
      "macos-integration",
      "windows-durability"
    ] | sort)
  ' "$proof" >/dev/null || {
  echo "::error::source-attestation proof is not the exact source/tag/builder/run/gate closure" >&2
  exit 1
}

GH_PROMPT_DISABLED=1 gh attestation verify "$proof" \
  --repo "$REPOSITORY" \
  --signer-workflow "$WORKFLOW_IDENTITY" \
  --source-ref refs/heads/master \
  --source-digest "$BUILDER_COMMIT" \
  --signer-digest "$BUILDER_COMMIT" \
  --deny-self-hosted-runners >/dev/null || {
  echo "::error::source-attestation proof provenance verification failed" >&2
  exit 1
}

echo "✓ exact public source attestation verified: run=$RUN_ID source=$SOURCE_COMMIT builder=$BUILDER_COMMIT"
