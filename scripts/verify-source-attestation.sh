#!/usr/bin/env bash
# Verify one public source-attestation run.
# Tag releases pass the tag, source, builder and run id. A preview passes
# --same-source and the source SHA; the builder is the attestation run's
# own commit, not the workflow that is asking to reuse the receipt.
# release.sh and build.yml share this script, so the two trust boundaries
# cannot drift apart.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
gh_read() {
  python3 "$SCRIPT_DIR/gh-read-retry.py" "$@"
}

REPOSITORY=onesyue/yuelink-ci
WORKFLOW_PATH=.github/workflows/source-attestation.yml

# Each contract marker lives once. The mutation check deletes the first copy
# only; a second copy would leave the deleted gate looking present.
fetch_workflow_json() {
  GH_PROMPT_DISABLED=1 gh_read gh api \
    "repos/$REPOSITORY/actions/workflows/source-attestation.yml" > "$1"
}

read_active_workflow_id() {
  jq -er --arg expected_name "Source attestation" '
    select(.name == $expected_name and
           .path == ".github/workflows/source-attestation.yml" and
           .state == "active") |
    .id | tostring | select(test("^[1-9][0-9]*$"))
  ' "$1"
}

download_attestation_proof() {
  GH_PROMPT_DISABLED=1 gh_read gh run download "$RUN_ID" \
    -R "$REPOSITORY" \
    --name "yuelink-source-attestation-$SOURCE_COMMIT" \
    --dir "$1"
}

EXACT_RUN_FILTER='
    ((.workflow_id | tostring) == $workflow_id) and
    (.name == "Source attestation" or
     .name == ("Source attestation " + $source)) and
    .display_title == ("Source attestation " + $source) and
    .path == $workflow and .event == "workflow_dispatch" and
    .status == "completed" and .conclusion == "success" and
    .head_sha == $builder and .head_branch == "master" and
    .repository.full_name == $repository'

# Preview reuse. The tag path below stays strict: proof.workflowSha must equal
# the builder argument, and that argument is what the caller claims. This
# entry looks the run up from the source SHA, then re-enters the tag path
# with the run's own head SHA so a newer workflow can reuse an older receipt
# for the same source bytes.
if [ "${1:-}" = "--same-source" ]; then
  SOURCE_COMMIT="${2:?usage: verify-source-attestation.sh --same-source SOURCE_COMMIT [--export-proof ABS_PATH]}"
  shift 2
  same_export=""
  if [ "${1:-}" = "--export-proof" ]; then
    [ "$#" -eq 2 ] && [[ "${2:-}" = /* ]] || {
      echo "::error::invalid source proof export arguments" >&2
      exit 2
    }
    same_export="$2"
    [ ! -e "$same_export" ] && [ ! -L "$same_export" ] || {
      echo "::error::source proof export must be a new file" >&2
      exit 2
    }
  elif [ "$#" -ne 0 ]; then
    echo "::error::invalid same-source reuse arguments" >&2
    exit 2
  fi
  [[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
    echo "::error::invalid source-attestation source identity" >&2
    exit 2
  }
  for command_name in gh jq python3 mktemp find; do
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
  lookup_root="$(mktemp -d "$tmp_root/yuelink-same-source.XXXXXX")"
  cleanup_lookup() {
    if [[ "${lookup_root:-}" == "$tmp_root"/yuelink-same-source.* ]] &&
       [ -d "${lookup_root:-}" ]; then
      find -P "$lookup_root" -mindepth 1 -delete
      rmdir "$lookup_root" 2>/dev/null || true
    fi
  }
  trap cleanup_lookup EXIT
  page_json="$lookup_root/runs.json"
  workflow_json="$lookup_root/workflow.json"
  fetch_workflow_json "$workflow_json"
  WORKFLOW_ID="$(read_active_workflow_id "$workflow_json")" || {
    echo "::error::source-attestation workflow identity is not the exact active workflow" >&2
    exit 1
  }
  chosen=""
  for page in 1 2 3 4 5; do
    GH_PROMPT_DISABLED=1 gh_read gh api \
      "repos/$REPOSITORY/actions/workflows/source-attestation.yml/runs?status=success&branch=master&per_page=100&page=${page}" \
      > "$page_json"
    list_filter='[.workflow_runs[]?
      | select((.head_sha | type == "string") and (.head_sha | test("^[0-9a-f]{40}$"))
          and (.id | tostring | test("^[1-9][0-9]*$"))
          and (.created_at | type == "string"))
      | .head_sha as $builder
      | select('"$EXACT_RUN_FILTER"')]
      | sort_by(.created_at)
      | last
      | if . == null then "" else [.id, .head_sha] | @tsv end'
    chosen="$(jq -r \
      --arg source "$SOURCE_COMMIT" \
      --arg workflow_id "$WORKFLOW_ID" \
      --arg workflow "$WORKFLOW_PATH" \
      --arg repository "$REPOSITORY" \
      "$list_filter" "$page_json")"
    if [ -n "$chosen" ]; then
      break
    fi
    page_count="$(jq -er '.workflow_runs | length' "$page_json")"
    [ "$page_count" -lt 100 ] && break
  done
  if [ -z "$chosen" ]; then
    echo "::error::no green Source attestation for ${SOURCE_COMMIT}" >&2
    exit 1
  fi
  RUN_ID="${chosen%%$'\t'*}"
  BUILDER_COMMIT="${chosen#*$'\t'}"
  proof_dir="$lookup_root/proof"
  mkdir -p "$proof_dir"
  download_attestation_proof "$proof_dir"
  release_tag="$(jq -er --arg source "$SOURCE_COMMIT" '
    select(.sourceSha == $source
           and (.releaseTag | test("^v[0-9]+\\.[0-9]+\\.[0-9]+$"))
           and (.workflowSha | test("^[0-9a-f]{40}$")))
    | .releaseTag
  ' "$proof_dir/source-attestation.json")" || {
    echo "::error::same-source attestation proof does not name this source" >&2
    exit 1
  }
  # Bash does not run the EXIT trap across exec. Remove the lookup tree first.
  trap - EXIT
  cleanup_lookup
  args=("$release_tag" "$SOURCE_COMMIT" "$BUILDER_COMMIT" "$RUN_ID")
  if [ -n "$same_export" ]; then
    args+=(--export-proof "$same_export")
  fi
  # Re-enter through bash. The checkout is not required to be executable,
  # and exec of the file itself fails when the mode bit is missing.
  exec bash "$0" "${args[@]}"
fi

TAG="${1:?usage: verify-source-attestation.sh TAG SOURCE_COMMIT BUILDER_COMMIT RUN_ID}"
SOURCE_COMMIT="${2:?source commit is required}"
BUILDER_COMMIT="${3:?builder commit is required}"
RUN_ID="${4:?source-attestation run id is required}"
EXPORT_PROOF=""
EXPORT_VERIFIED_PROOF=""
PROOF_FILE=""
BUNDLE_FILE=""
shift 4
while (( $# )); do
  [ "$#" -ge 2 ] && [[ "$2" = /* ]] || {
    echo "::error::invalid source proof arguments" >&2; exit 2;
  }
  case "$1" in
    --export-proof) option=EXPORT_PROOF ;;
    --export-verified-proof) option=EXPORT_VERIFIED_PROOF ;;
    --proof-file) option=PROOF_FILE ;;
    --bundle-file) option=BUNDLE_FILE ;;
    *) echo "::error::unknown source proof option" >&2; exit 2 ;;
  esac
  [ -z "${!option}" ] || { echo "::error::duplicate source proof option" >&2; exit 2; }
  printf -v "$option" '%s' "$2"
  shift 2
done
for output in "$EXPORT_PROOF" "$EXPORT_VERIFIED_PROOF"; do
  [ -z "$output" ] || { [ ! -e "$output" ] && [ ! -L "$output" ]; } || {
    echo "::error::source proof export must be a new file" >&2; exit 2;
  }
done
for input in "$PROOF_FILE" "$BUNDLE_FILE"; do
  [ -z "$input" ] || { [ -f "$input" ] && [ ! -L "$input" ]; } || {
    echo "::error::source proof input must be a regular file" >&2; exit 2;
  }
done
[ -z "$BUNDLE_FILE" ] || [ -n "$PROOF_FILE" ] || {
  echo "::error::bundle requires explicit proof bytes" >&2; exit 2;
}
WORKFLOW_IDENTITY=onesyue/yuelink-ci/.github/workflows/source-attestation.yml

[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] &&
  [[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] &&
  [[ "$BUILDER_COMMIT" =~ ^[0-9a-f]{40}$ ]] &&
  [[ "$RUN_ID" =~ ^[1-9][0-9]*$ ]] || {
  echo "::error::invalid source-attestation tag/source/builder/run identity" >&2
  exit 2
}
for command_name in gh jq python3 mktemp find wc tr; do
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
workflow_json="$snapshot/workflow.json"
proof_dir="$snapshot/proof"
proof="$proof_dir/source-attestation.json"
mkdir -p "$proof_dir"

GH_PROMPT_DISABLED=1 gh_read gh api \
  "repos/$REPOSITORY/actions/runs/$RUN_ID" > "$run_json"
fetch_workflow_json "$workflow_json"
WORKFLOW_ID="$(read_active_workflow_id "$workflow_json")" || {
  echo "::error::source-attestation workflow identity is not the exact active workflow" >&2
  exit 1
}
identity_filter='(.id | tostring) == $run and
'"$EXACT_RUN_FILTER"
jq -e \
  --arg repository "$REPOSITORY" \
  --arg workflow "$WORKFLOW_PATH" \
  --arg workflow_id "$WORKFLOW_ID" \
  --arg source "$SOURCE_COMMIT" \
  --arg builder "$BUILDER_COMMIT" \
  --arg run "$RUN_ID" \
  "$identity_filter" "$run_json" >/dev/null || {
  echo "::error::source-attestation run identity does not match exact workflow/event/title/source/builder" >&2
  exit 1
}
RUN_ATTEMPT="$(jq -er '.run_attempt | tostring | select(test("^[1-9][0-9]*$"))' "$run_json")"

if [ -n "$PROOF_FILE" ]; then
  cp "$PROOF_FILE" "$proof"
else
  download_attestation_proof "$proof_dir"
fi
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
    (keys | sort) == (([
      "schemaVersion", "sourceRepository", "sourceSha", "releaseTag",
      "builderRepository", "workflowSha", "runId", "runAttempt", "gates"
    ] + (if .schemaVersion == 2 then ["reuse"] else [] end)) | sort) and
    (.schemaVersion == 1 or (.schemaVersion == 2 and
      (.reuse | keys | sort) == (["schemaVersion", "flutter", "service"] | sort) and
      .reuse.schemaVersion == 1 and
      ([.reuse.flutter, .reuse.service] | all(
        .schemaVersion == 1 and .sourceSha == $source and
        .workflowSha == $builder and .runId == $run and
        (.runAttempt | test("^[1-9][0-9]*$")) and
        ((.runAttempt | tonumber) <= ($attempt | tonumber)) and
        (.toolchain | type == "object" and length > 0))) and
      .reuse.flutter.kind == "flutter" and
      # B5 (2026-10-30): new proofs only attest flutter-analyze; proofs made
      # before B5 still carry the executed full-suite receipt. Each proof must
      # be one closure or the other, never a mix (checked again below).
      (.reuse.flutter.gates == ["flutter-analyze"] or
       .reuse.flutter.gates == ["flutter-analyze", "flutter-tests-coverage"]) and
      .reuse.service.kind == "service" and
      .reuse.service.gates == ["service-go-race"]
    )) and
    .sourceRepository == "onesyue/yuelink" and .sourceSha == $source and
    .releaseTag == $release and
    .builderRepository == "onesyue/yuelink-ci" and
    .workflowSha == $builder and .runId == $run and .runAttempt == $attempt and
    ((.gates | sort) as $gates |
     ($gates | index("flutter-tests-floor-2044")) as $floor |
     (if .schemaVersion == 1 then $floor != null
      else ($floor != null) == (.reuse.flutter.gates | index("flutter-tests-coverage") != null)
      end) and
     ($gates - ["flutter-tests-floor-2044"]) == ([
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
      "gitleaks-full-history",
      "govulncheck-core-service",
      "macos-integration",
      "windows-durability",
      "windows-native-core-runtime"
    ] | sort) and
     (($gates | length) == 16 or ($floor != null and ($gates | length) == 17)))
  ' "$proof" >/dev/null || {
  echo "::error::source-attestation proof is not the exact source/tag/builder/run/gate closure" >&2
  exit 1
}

verification_args=(gh attestation verify "$proof"
  --repo "$REPOSITORY"
  --cert-oidc-issuer https://token.actions.githubusercontent.com
  --predicate-type https://slsa.dev/provenance/v1
  --signer-workflow "$WORKFLOW_IDENTITY"
  --source-ref refs/heads/master
  --source-digest "$BUILDER_COMMIT"
  --signer-digest "$BUILDER_COMMIT"
  --deny-self-hosted-runners)
if [ -n "$BUNDLE_FILE" ]; then
  cp "$BUNDLE_FILE" "$snapshot/provenance.jsonl"
  verification_args+=(--bundle "$snapshot/provenance.jsonl")
fi
GH_PROMPT_DISABLED=1 gh_read "${verification_args[@]}" >/dev/null || {
  echo "::error::source-attestation proof provenance verification failed" >&2
  exit 1
}

if [ -n "$EXPORT_PROOF" ]; then
  jq -e '.schemaVersion == 2' "$proof" >/dev/null || {
    echo "::error::legacy source proof cannot authorize gate reuse" >&2; exit 1;
  }
  ( set -o noclobber; cat "$proof" > "$EXPORT_PROOF" )
fi

if [ -n "$EXPORT_VERIFIED_PROOF" ]; then
  ( set -o noclobber; cat "$proof" > "$EXPORT_VERIFIED_PROOF" )
fi

echo "✓ exact public source attestation verified: run=$RUN_ID source=$SOURCE_COMMIT builder=$BUILDER_COMMIT"
