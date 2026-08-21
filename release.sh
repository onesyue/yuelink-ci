#!/usr/bin/env bash
# 在本仓(yuelink-ci)打同名 tag 并推送 → 触发公开仓构建。
# 前提:私有仓 onesyue/yuelink 已经打好并推送了同名 tag。
#
#   ./release.sh vX.Y.Z
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

SOURCE_SNAPSHOT=""
cleanup() {
  if [[ "$SOURCE_SNAPSHOT" == */yuelink-ci-source-tag.* ]] &&
     [ -d "$SOURCE_SNAPSHOT" ]; then
    rm -rf -- "$SOURCE_SNAPSHOT"
  fi
}
trap cleanup EXIT

TAG="${1:-}"
if [ -z "$TAG" ]; then
  echo "用法: ./release.sh <vX.Y.Z>"; exit 1
fi
if [[ ! "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "::error::仅支持稳定版 vX.Y.Z；prerelease 发布链已停用: $TAG"
  exit 1
fi

command -v gh >/dev/null 2>&1 || {
  echo "::error::缺少 gh，不能校验私有源码 tag；拒绝 fail-open 发版。"
  exit 1
}
command -v python3 >/dev/null 2>&1 || {
  echo "::error::缺少 python3，不能逐字节校验本机 signed tag；拒绝 fail-open 发版。"
  exit 1
}

# GitHub does not expose secret values, but it does expose configured names.
# Require only identities that are structurally mandatory for a sideload
# release: the source checkout, immutable R2 publisher, and (for stable tags)
# Android's upgrade-compatible APK key. Apple Developer ID/notarization and
# Windows Authenticode are optional publisher identities; each platform records
# its actual mode and an unavailable platform never blocks unrelated artifacts.
required_secrets=(SRC_DEPLOY_KEY R2_KEY_ID R2_APP_KEY CLOUDFLARE_R2_CONFIG_TOKEN)
if [[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  required_secrets+=(
    KEYSTORE_BASE64 KEYSTORE_PASSWORD KEY_ALIAS KEY_PASSWORD
  )
fi
configured_secrets="$(
  gh secret list -R onesyue/yuelink-ci --json name --jq '.[].name'
)"
missing_secrets=0
for secret_name in "${required_secrets[@]}"; do
  if ! grep -Fxq "$secret_name" <<<"$configured_secrets"; then
    echo "::error::yuelink-ci 缺少必需的 repository secret: $secret_name"
    missing_secrets=$((missing_secrets + 1))
  fi
done
[ "$missing_secrets" -eq 0 ] || {
  echo "::error::发布前密钥门禁未通过；未创建任何 tag。"
  exit 1
}

[ "$(git branch --show-current)" = "master" ] || {
  echo "::error::必须从 yuelink-ci/master 发版。"
  exit 1
}
[ -z "$(git status --porcelain)" ] || {
  echo "::error::yuelink-ci 工作区不干净；先提交并复核同步结果。"
  exit 1
}

git fetch --prune --no-tags origin master
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/master)" ] || {
  echo "::error::本地 master 不是 origin/master；先拉取并确认最新发布门禁。"
  exit 1
}

# 私有 tag 必须先存在且为 GitHub 验真的 signed annotated tag。公开 tag 在
# 最终阶段按远端真相幂等处理；此处不以一次 ls-remote 结果决定创建或覆盖。
if ! PRIVATE_TAG_REF_JSON="$(
  gh api "repos/onesyue/yuelink/git/ref/tags/$TAG"
)"; then
  echo "::error::私有仓 onesyue/yuelink 没有 tag '$TAG' —— 先在私有仓打 tag 并推送。"
  exit 1
fi
PRIVATE_TAG_OBJECT_SHA="$(jq -er '.object.sha' <<<"$PRIVATE_TAG_REF_JSON")"
PRIVATE_TAG_REF_TYPE="$(jq -er '.object.type' <<<"$PRIVATE_TAG_REF_JSON")"
[[ "$PRIVATE_TAG_OBJECT_SHA" =~ ^[0-9a-f]{40}$ ]] &&
  [ "$PRIVATE_TAG_REF_TYPE" = tag ] || {
  echo "::error::私仓 tag $TAG 必须是 signed annotated tag，拒绝 lightweight tag。"
  exit 1
}
PRIVATE_TAG_JSON="$(
  gh api "repos/onesyue/yuelink/git/tags/$PRIVATE_TAG_OBJECT_SHA"
)"
SOURCE_COMMIT="$(jq -er '.object.sha' <<<"$PRIVATE_TAG_JSON")"
if ! jq -e --arg tag "$TAG" --arg source "$SOURCE_COMMIT" '
    .tag == $tag and
    .object.type == "commit" and .object.sha == $source and
    .verification.verified == true and .verification.reason == "valid"
  ' <<<"$PRIVATE_TAG_JSON" >/dev/null ||
   [[ ! "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  echo "::error::私仓 tag $TAG 的签名无效、未验证或未直接指向 commit。"
  exit 1
fi
echo "✓ 私仓 signed source tag 已验真: $TAG -> $SOURCE_COMMIT"

# Private-repository Actions can be prevented from starting by account billing,
# producing a red check with no steps or logs. A release must still have a
# machine-verifiable source gate; a maintainer's local test claim is not an
# acceptable substitute. Require the successful public source-attestation
# workflow produced by this exact yuelink-ci revision for the exact private
# commit behind the immutable source tag, then authenticate its proof artifact.
BUILDER_COMMIT="$(git rev-parse HEAD)"
git verify-commit "$BUILDER_COMMIT" >/dev/null || {
  echo "::error::当前 builder commit 的本机签名验证失败；拒绝授权 public tag。"
  exit 1
}
BUILDER_COMMIT_JSON="$(
  gh api "repos/onesyue/yuelink-ci/commits/$BUILDER_COMMIT"
)"
jq -e --arg builder "$BUILDER_COMMIT" '
  .sha == $builder and
  .commit.verification.verified == true and
  .commit.verification.reason == "valid"
' <<<"$BUILDER_COMMIT_JSON" >/dev/null || {
  echo "::error::GitHub 未把 builder commit $BUILDER_COMMIT 识别为 verified/valid；在创建不可变 tag 前阻断。"
  exit 1
}
ATTESTATION_TITLE="Source attestation $SOURCE_COMMIT"
ATTESTATION_RUN_ID="$(
  gh api \
    'repos/onesyue/yuelink-ci/actions/workflows/source-attestation.yml/runs?event=workflow_dispatch&status=success&per_page=100' \
    --jq ".workflow_runs
      | map(select(
          .display_title == \"$ATTESTATION_TITLE\" and
          .head_sha == \"$BUILDER_COMMIT\" and
          .conclusion == \"success\"
        ))
      | sort_by(.created_at)
      | last
      | .id // empty"
)"
if [[ ! "$ATTESTATION_RUN_ID" =~ ^[0-9]+$ ]]; then
  echo "::error::缺少 source $SOURCE_COMMIT 在 builder $BUILDER_COMMIT 上的成功公共 source attestation；拒绝用本地声明替代。"
  echo "触发: gh workflow run source-attestation.yml -R onesyue/yuelink-ci -f source_sha=$SOURCE_COMMIT"
  exit 1
fi

bash scripts/verify-source-attestation.sh \
  "$TAG" "$SOURCE_COMMIT" "$BUILDER_COMMIT" "$ATTESTATION_RUN_ID"
PUBLIC_TAG_MESSAGE="$(printf \
  'YueLink public builder %s\n\nATTESTED_SOURCE_COMMIT=%s\nSOURCE_ATTESTATION_RUN_ID=%s' \
  "$TAG" "$SOURCE_COMMIT" "$ATTESTATION_RUN_ID")"

# 对比远端私仓同名 tag 中的精确 workflow，而不是旁边 ../yuelink 当前
# 工作区。后者可能有尚未进入 tag 的修改；用它做 sync check 会让公开 tag
# 带着“未来 workflow”去 checkout 旧私仓源码，直到 runner 缺脚本才爆炸。
SOURCE_SNAPSHOT="$(mktemp -d "${TMPDIR:-/tmp}/yuelink-ci-source-tag.XXXXXX")"
mkdir -p "$SOURCE_SNAPSHOT/.github/workflows"
gh api \
  "repos/onesyue/yuelink/contents/.github/workflows/build.yml?ref=$TAG" \
  --jq '.content' \
  | tr -d '\r\n' \
  | openssl base64 -d -A \
      > "$SOURCE_SNAPSHOT/.github/workflows/build.yml"
[ -s "$SOURCE_SNAPSHOT/.github/workflows/build.yml" ] || {
  echo "::error::无法读取私仓 $TAG 的 build.yml；拒绝用本地工作区代替。"
  exit 1
}
./sync-build.sh --check "$SOURCE_SNAPSHOT"

echo "✓ 工作区、远端私仓 tag workflow、公开镜像和 tag 状态均已验证"

remote_public_tag_state() {
  local status
  set +e
  git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1
  status=$?
  set -e
  case "$status" in
    0) echo exists ;;
    2) echo absent ;;
    *)
      echo "::error::无法确定远端 public tag 状态（git ls-remote exit=${status}）；拒绝把网络错误当作不存在。" >&2
      return 1
      ;;
  esac
}

inspect_remote_public_tag() {
  local ref_json tag_object_sha ref_type tag_json
  if ! ref_json="$(
    gh api "repos/onesyue/yuelink-ci/git/ref/tags/$TAG" 2>/dev/null
  )"; then
    return 1
  fi
  tag_object_sha="$(jq -er '.object.sha' <<<"$ref_json")" || return 2
  ref_type="$(jq -er '.object.type' <<<"$ref_json")" || return 2
  [[ "$tag_object_sha" =~ ^[0-9a-f]{40}$ ]] && [ "$ref_type" = tag ] || {
    echo "::error::远端 $TAG 已存在但不是 annotated tag；绝不覆盖。" >&2
    return 2
  }
  tag_json="$(
    gh api "repos/onesyue/yuelink-ci/git/tags/$tag_object_sha" 2>/dev/null
  )" || return 1
  jq -e --arg tag "$TAG" --arg builder "$BUILDER_COMMIT" \
    --arg message "$PUBLIC_TAG_MESSAGE" '
    # GitHub currently returns a signed annotated tag message as the exact
    # annotation followed by the detached armor that it also exposes in
    # verification.signature. Some API versions return only the annotation.
    # Accept those two exact representations and nothing in between/after;
    # verification.verified/reason below remains the cryptographic authority.
    def has_exact_public_message:
      .message == $message or
      (
        (.verification.signature // null) as $signature |
        ($signature | type) == "string" and
        ($signature | startswith("-----BEGIN PGP SIGNATURE-----\n")) and
        (
          ($signature | endswith("\n-----END PGP SIGNATURE-----\n")) or
          ($signature | endswith("\n-----END PGP SIGNATURE-----"))
        ) and
        ($signature | contains("\r") | not) and
        ($signature | split("-----BEGIN PGP SIGNATURE-----") | length) == 2 and
        ($signature | split("-----END PGP SIGNATURE-----") | length) == 2 and
        .message == ($message + "\n" + $signature)
      );
    .tag == $tag and
    .object.type == "commit" and .object.sha == $builder and
    has_exact_public_message
  ' <<<"$tag_json" >/dev/null || {
    echo "::error::远端 $TAG 未绑定 exact builder ${BUILDER_COMMIT}；exact source/attestation message 不匹配，绝不覆盖。" >&2
    return 2
  }
  if jq -e '
      .verification.verified == true and .verification.reason == "valid"
    ' <<<"$tag_json" >/dev/null; then
    return 0
  fi
  echo "::notice::远端 $TAG 已存在且 commit 正确，但 GitHub 验签尚未 valid（reason=$(jq -r '.verification.reason // "unknown"' <<<"$tag_json")）。" >&2
  return 1
}

wait_for_verified_remote_public_tag() {
  local attempt status
  for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
    set +e
    inspect_remote_public_tag
    status=$?
    set -e
    case "$status" in
      0)
        echo "✓ GitHub 已验真 signed public tag: $TAG -> $BUILDER_COMMIT"
        return 0
        ;;
      2) return 1 ;;
    esac
    [ "$attempt" -eq 12 ] || sleep 5
  done
  echo "::error::远端 $TAG 已进入不可变命名空间，但 GitHub 在 60 秒内未确认 verified/valid。" >&2
  echo "::error::绝不删除、覆盖或重打该 tag；停止发版并按签名/远端 ref 事故处理，人工核对 GitHub tag verification。" >&2
  return 1
}

inspect_local_public_tag() {
  local actual_annotation_sha expected_annotation_sha
  if [ "$(git cat-file -t "$TAG")" != tag ] ||
     [ "$(git rev-parse "${TAG}^{}")" != "$BUILDER_COMMIT" ] ||
     ! git verify-tag "$TAG" >/dev/null; then
    echo "::error::本机同名 tag 不是 exact verified signed builder tag；绝不 push/覆盖。" >&2
    return 1
  fi

  # Parsed subject/body fields and shell command substitution both normalize
  # annotation bytes. Hash the raw annotation stream from the tag object:
  # after its exact standard headers and before its one armored PGP signature.
  # Command substitutions carry fixed-size digests, never message text whose
  # terminating bytes the shell could discard.
  if ! actual_annotation_sha="$(
    git cat-file tag "$TAG" |
      TAG="$TAG" BUILDER_COMMIT="$BUILDER_COMMIT" python3 -c '
import hashlib
import os
import re
import sys

raw = sys.stdin.buffer.read()
header, separator, signed_payload = raw.partition(b"\n\n")
headers = header.split(b"\n")
tag = os.environ["TAG"].encode("ascii")
builder = os.environ["BUILDER_COMMIT"].encode("ascii")
begin = b"-----BEGIN PGP SIGNATURE-----\n"
end = b"-----END PGP SIGNATURE-----\n"

valid_headers = (
    separator == b"\n\n"
    and len(headers) == 4
    and headers[0] == b"object " + builder
    and headers[1] == b"type commit"
    and headers[2] == b"tag " + tag
    and re.fullmatch(rb"tagger .+ <[^\n<>]+> [0-9]+ [+-][0-9]{4}", headers[3])
    is not None
)
valid_signature_envelope = (
    signed_payload.count(begin) == 1
    and signed_payload.count(end) == 1
    and signed_payload.endswith(end)
)
if not valid_headers or not valid_signature_envelope:
    raise SystemExit(1)
annotation, _signature = signed_payload.split(begin, 1)
print(hashlib.sha256(annotation).hexdigest())
'
  )"; then
    echo "::error::本机同名 signed tag 的原始对象/header/PGP signature envelope 非法；绝不 push。" >&2
    return 1
  fi
  expected_annotation_sha="$(
    printf '%s\n' "$PUBLIC_TAG_MESSAGE" |
      python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
  )"
  [[ "$actual_annotation_sha" =~ ^[0-9a-f]{64}$ ]] &&
    [ "$actual_annotation_sha" = "$expected_annotation_sha" ] || {
    echo "::error::本机同名 signed tag annotation 与 exact source/attestation binding 不一致；绝不 push。" >&2
    return 1
  }
}

remote_state="$(remote_public_tag_state)"
if [ "$remote_state" = exists ]; then
  echo "::notice::远端 $TAG 已存在；进入幂等验真，不创建也不覆盖。"
  wait_for_verified_remote_public_tag
else
  if git show-ref --verify --quiet "refs/tags/$TAG"; then
    echo "::notice::复用本机 exact signed tag ${TAG}（此前 push 结果可能不确定）。"
  else
    git tag -s -m "$PUBLIC_TAG_MESSAGE" "$TAG" "$BUILDER_COMMIT"
  fi

  # A prior uncertain push can leave a local signed tag behind. Revalidate the
  # complete annotation bytes as well as type/peel/signature before *every*
  # public push; builder equality alone must not authorize a stale run binding.
  inspect_local_public_tag

  if ! git push origin "refs/tags/$TAG:refs/tags/$TAG"; then
    echo "::warning::public tag push 返回失败；重新读取远端真相，绝不盲目重推或覆盖。" >&2
    remote_state="$(remote_public_tag_state)" || exit 1
    [ "$remote_state" = exists ] || {
      echo "::error::push 失败且远端 tag 不存在；保留本机 signed tag，修复连接后幂等重跑。" >&2
      exit 1
    }
  fi
  wait_for_verified_remote_public_tag
fi

echo "✓ 已确认远端 exact verified signed tag ${TAG}；对应构建已触发或可幂等续跑"
echo "  看进度: gh run watch -R onesyue/yuelink-ci"
if [[ "$TAG" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
  echo "  构建绿仅代表 unsigned candidate 就绪；随后在受保护的私仓/本机签名平面运行:"
  echo "  bash scripts/ci/promote_signed_manifest.sh ${BASH_REMATCH[1]}"
  echo "  (private Actions 可用时也可 workflow_dispatch sign-release-manifest.yml)"
  echo "  promotion 验收后:把精确签名根更新到 tests/fixtures/update-manifest-v1.json"
  echo "  并把旧根保留为 replay regression fixture；不得从 CDN 动态学习地板。"
fi
