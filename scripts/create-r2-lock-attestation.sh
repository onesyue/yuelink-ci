#!/usr/bin/env bash
# Emit a short-lived, release-bound proof of the live Cloudflare R2 lock.
# The caller attests the resulting JSON with GitHub artifact attestations.
set -Eeuo pipefail

VERSION="${1:?usage: create-r2-lock-attestation.sh VERSION SOURCE_COMMIT CANDIDATE_SHA256 BUILDER_COMMIT BUILD_RUN_ID CHALLENGE OUTPUT}"
SOURCE_COMMIT="${2:?source commit is required}"
CANDIDATE_SHA256="${3:?candidate sha256 is required}"
BUILDER_COMMIT="${4:?candidate builder commit is required}"
BUILD_RUN_ID="${5:?candidate build run id is required}"
CHALLENGE="${6:?random challenge is required}"
OUTPUT="${7:?output path is required}"

REPOSITORY=onesyue/yuelink-ci
BUCKET=yuelink-dist
R2_ACCOUNT_ID=1e192a89cfaab399d211bb1dfe77a5cd
PROOF_LIFETIME_SECONDS=1800

: "${GH_TOKEN:?GitHub token is required}"
: "${CLOUDFLARE_R2_CONFIG_TOKEN:?Cloudflare R2 configuration-read token is required}"
: "${GITHUB_REPOSITORY:?GitHub repository identity is required}"
: "${GITHUB_SHA:?GitHub workflow commit is required}"
: "${GITHUB_REF:?GitHub ref is required}"
: "${GITHUB_RUN_ID:?GitHub run id is required}"
: "${GITHUB_RUN_ATTEMPT:?GitHub run attempt is required}"

TAG="v$VERSION"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] &&
  [[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] &&
  [[ "$CANDIDATE_SHA256" =~ ^[0-9a-f]{64}$ ]] &&
  [[ "$BUILDER_COMMIT" =~ ^[0-9a-f]{40}$ ]] &&
  [[ "$BUILD_RUN_ID" =~ ^[1-9][0-9]*$ ]] &&
  [[ "$CHALLENGE" =~ ^[0-9a-f]{64}$ ]] &&
  [[ "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]] &&
  [[ "$GITHUB_RUN_ID" =~ ^[1-9][0-9]*$ ]] &&
  [[ "$GITHUB_RUN_ATTEMPT" =~ ^[1-9][0-9]*$ ]] &&
  [ "$GITHUB_REPOSITORY" = "$REPOSITORY" ] &&
  [ "$GITHUB_REF" = refs/heads/master ] &&
  [ "$GITHUB_SHA" = "$BUILDER_COMMIT" ] || {
  echo "::error::invalid R2 lock proof release/workflow identity" >&2
  exit 2
}
case "$OUTPUT" in
  /*) ;;
  *) echo "::error::R2 lock proof output must be absolute" >&2; exit 2 ;;
esac
[ ! -e "$OUTPUT" ] && [ ! -L "$OUTPUT" ] || {
  echo "::error::R2 lock proof output must not already exist" >&2
  exit 2
}
output_parent="$(dirname "$OUTPUT")"
[ -d "$output_parent" ] && [ ! -L "$output_parent" ] || {
  echo "::error::R2 lock proof output parent is missing or unsafe" >&2
  exit 2
}
runner_temp="${RUNNER_TEMP:-/tmp}"
case "$runner_temp" in
  /*) ;;
  *) echo "::error::RUNNER_TEMP must be absolute" >&2; exit 2 ;;
esac
[ -d "$runner_temp" ] && [ ! -L "$runner_temp" ] || {
  echo "::error::RUNNER_TEMP is missing or unsafe" >&2
  exit 2
}

for command_name in \
  curl jq gh sha256sum mktemp chmod date dirname rm find rmdir awk ln python3 sleep; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "::error::missing R2 lock proof command: $command_name" >&2
    exit 1
  }
done

umask 077
work_dir="$(mktemp -d "$runner_temp/yuelink-r2-lock-create.XXXXXX")"
cleanup() {
  set +e
  unset CLOUDFLARE_R2_CONFIG_TOKEN
  if [[ "${work_dir:-}" == "$runner_temp"/yuelink-r2-lock-create.* ]] &&
     [ -d "${work_dir:-}" ]; then
    find -P "$work_dir" -mindepth 1 -delete
    rmdir "$work_dir" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# Keep the original total budgets, including backoff. A monotonic clock avoids
# wall-clock adjustments extending the security-sensitive observation window.
monotonic_ms() {
  python3 -c 'import time; print(time.monotonic_ns() // 1000000)'
}
fetch_with_budget() {
  local budget=$1 attempts=$2 attempt_cap=$3 output=$4
  shift 4
  local deadline now remaining timeout_ms timeout status rc attempt delay=1 metadata
  now=$(monotonic_ms) || return 1
  deadline=$((now + budget * 1000))
  for ((attempt=1; attempt<=attempts; attempt++)); do
    now=$(monotonic_ms) || return 1
    remaining=$((deadline - now))
    if ((remaining <= 0)); then
      echo "::error::R2 lock proof request budget exhausted" >&2
      return 1
    fi
    timeout_ms=$((attempt_cap * 1000))
    ((timeout_ms <= remaining)) || timeout_ms=$remaining
    printf -v timeout '%d.%03d' "$((timeout_ms / 1000))" "$((timeout_ms % 1000))"
    rc=0
    metadata=$(curl --fail --silent --show-error \
      --connect-timeout 10 --max-time "$timeout" --retry 0 \
      --max-redirs 0 --max-filesize 262144 \
      --write-out '%{http_code} %{url_effective}' --output "$output" "$@") || rc=$?
    now=$(monotonic_ms) || return 1
    if ((now >= deadline)); then
      echo "::error::R2 lock proof request budget exhausted" >&2
      return 1
    fi
    status=${metadata%% *}
    if ((rc == 0)) && [[ "$status" =~ ^2[0-9][0-9]$ ]]; then
      printf '%s' "${metadata#* }"
      return 0
    fi
    # HTTP authorization/content errors take precedence over transport errors.
    case "$status" in
      429|5[0-9][0-9])
        case "$rc" in
          0|22|5|6|7|18|28|52|55|56|92) ;;
          *) return "$rc" ;;
        esac
        ;;
      000|2[0-9][0-9])
        case "$rc" in
          5|6|7|18|28|52|55|56|92) ;;
          *) return 1 ;;
        esac
        ;;
      *) return 1 ;;
    esac
    if ((attempt == attempts)); then
      echo "::error::R2 lock proof request attempts exhausted" >&2
      return 1
    fi
    if ((deadline - now <= delay * 1000)); then
      echo "::error::R2 lock proof request budget exhausted before retry" >&2
      return 1
    fi
    sleep "$delay" || return 1
    delay=$((delay * 2))
  done
}

candidate="$work_dir/update.candidate.json"
candidate_url="https://yuetong.app/$TAG/update.candidate.json"
effective_url="$(
  fetch_with_budget 60 4 15 "$candidate" \
    --proto '=https' --proto-redir '=https' --tlsv1.2 "$candidate_url"
)"
[ "$effective_url" = "$candidate_url" ] && [ -s "$candidate" ] || {
  echo "::error::candidate fetch redirected or returned no bytes" >&2
  exit 1
}
[ "$(sha256sum "$candidate" | awk '{print $1}')" = "$CANDIDATE_SHA256" ] || {
  echo "::error::candidate digest does not match reviewed input" >&2
  exit 1
}
jq -e --arg version "$VERSION" --arg source "$SOURCE_COMMIT" '
  type == "object" and .schemaVersion == 1 and
  .version == $version and .channel == "stable" and
  .sourceCommit == $source and
  (.platforms | type == "object" and length == 9)
' "$candidate" >/dev/null || {
  echo "::error::candidate is not bound to exact stable version/source/9-platform closure" >&2
  exit 1
}

workflow_json="$work_dir/build-workflow.json"
run_json="$work_dir/build-run.json"
GH_PROMPT_DISABLED=1 gh api \
  "repos/$REPOSITORY/actions/workflows/build.yml" > "$workflow_json"
GH_PROMPT_DISABLED=1 gh api \
  "repos/$REPOSITORY/actions/runs/$BUILD_RUN_ID" > "$run_json"
workflow_id="$(jq -er '
  select(.name == "Build YueLink" and .path == ".github/workflows/build.yml" and
         .state == "active") | .id | tostring | select(test("^[1-9][0-9]*$"))
' "$workflow_json")" || {
  echo "::error::candidate build workflow is not the exact active workflow" >&2
  exit 1
}
jq -e \
  --arg repository "$REPOSITORY" --arg tag "$TAG" \
  --arg builder "$BUILDER_COMMIT" --arg run "$BUILD_RUN_ID" \
  --arg workflow_id "$workflow_id" '
    (.id | tostring) == $run and ((.workflow_id | tostring) == $workflow_id) and
    .name == "Build YueLink" and .path == ".github/workflows/build.yml" and
    .event == "push" and .status == "completed" and .conclusion == "success" and
    .head_branch == $tag and .head_sha == $builder and
    .repository.full_name == $repository
  ' "$run_json" >/dev/null || {
  echo "::error::candidate build run is not the exact successful tag/builder run" >&2
  exit 1
}

# The scoped token is not used until every public release coordinate above has
# been authenticated; its value is never copied into the proof or logs.
lock_response="$work_dir/lock-response.json"
fetch_with_budget 30 3 10 "$lock_response" \
  --proto '=https' --tlsv1.2 \
  -H "Authorization: Bearer $CLOUDFLARE_R2_CONFIG_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$R2_ACCOUNT_ID/r2/buckets/$BUCKET/lock" \
  > /dev/null
jq -e '
  .success == true and (.errors | length) == 0 and
  ([.result.rules[]? | select(
    .id == "yuelink-release-versioned-indefinite" and
    .enabled == true and .prefix == "v" and
    .condition.type == "Indefinite"
  )] | length) == 1
' "$lock_response" >/dev/null || {
  echo "::error::live indefinite R2 version lock is absent or ambiguous" >&2
  exit 1
}

issued_epoch="$(date -u +%s)"
[[ "$issued_epoch" =~ ^[1-9][0-9]*$ ]] || {
  echo "::error::cannot resolve proof issuance time" >&2
  exit 1
}
expires_epoch="$((issued_epoch + PROOF_LIFETIME_SECONDS))"
proof_tmp="$work_dir/r2-lock-proof.json"
jq -S -n \
  --arg version "$VERSION" --arg tag "$TAG" \
  --arg source "$SOURCE_COMMIT" --arg candidate "$CANDIDATE_SHA256" \
  --arg builder "$BUILDER_COMMIT" --arg buildRun "$BUILD_RUN_ID" \
  --arg challenge "$CHALLENGE" --arg workflowCommit "$GITHUB_SHA" \
  --arg runId "$GITHUB_RUN_ID" --arg runAttempt "$GITHUB_RUN_ATTEMPT" \
  --argjson issued "$issued_epoch" --argjson expires "$expires_epoch" '
    {
      schemaVersion: 1,
      proofType: "yuelink-r2-lock-attestation-v1",
      repository: "onesyue/yuelink-ci",
      workflow: ".github/workflows/r2-lock-attestation.yml",
      workflowCommit: $workflowCommit,
      runId: $runId,
      runAttempt: $runAttempt,
      version: $version,
      releaseTag: $tag,
      sourceCommit: $source,
      candidateSha256: $candidate,
      candidateBuilderCommit: $builder,
      candidateBuildRunId: $buildRun,
      challenge: $challenge,
      bucket: "yuelink-dist",
      accountId: "1e192a89cfaab399d211bb1dfe77a5cd",
      lockRule: {
        id: "yuelink-release-versioned-indefinite",
        prefix: "v",
        conditionType: "Indefinite",
        enabled: true
      },
      issuedAt: ($issued | todateiso8601),
      expiresAt: ($expires | todateiso8601)
    }
  ' > "$proof_tmp"
chmod 0600 "$proof_tmp"
ln "$proof_tmp" "$OUTPUT"
rm "$proof_tmp"
echo "R2_LOCK_PROOF_SHA256=$(sha256sum "$OUTPUT" | awk '{print $1}')"
