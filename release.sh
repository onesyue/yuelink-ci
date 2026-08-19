#!/usr/bin/env bash
# 在本仓(yuelink-ci)打同名 tag 并推送 → 触发公开仓构建。
# 前提:私有仓 onesyue/yuelink 已经打好并推送了同名 tag。
#
#   ./release.sh vX.Y.Z
#   ./release.sh vX.Y.Z-pre.N
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

SOURCE_SNAPSHOT=""
ATTESTATION_SNAPSHOT=""
cleanup() {
  if [[ "$SOURCE_SNAPSHOT" == */yuelink-ci-source-tag.* ]] &&
     [ -d "$SOURCE_SNAPSHOT" ]; then
    rm -rf -- "$SOURCE_SNAPSHOT"
  fi
  if [[ "$ATTESTATION_SNAPSHOT" == */yuelink-ci-source-attestation.* ]] &&
     [ -d "$ATTESTATION_SNAPSHOT" ]; then
    rm -rf -- "$ATTESTATION_SNAPSHOT"
  fi
}
trap cleanup EXIT

TAG="${1:-}"
if [ -z "$TAG" ]; then
  echo "用法: ./release.sh <vX.Y.Z|vX.Y.Z-pre.N>"; exit 1
fi
if [[ ! "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-pre(\.[0-9]+)?)?$ ]]; then
  echo "::error::非法 release tag: $TAG"
  exit 1
fi

command -v gh >/dev/null 2>&1 || {
  echo "::error::缺少 gh，不能校验私有源码 tag；拒绝 fail-open 发版。"
  exit 1
}

# GitHub does not expose secret values, but it does expose configured names.
# Require only identities that are structurally mandatory for a sideload
# release: the source checkout, immutable R2 publisher, and (for stable tags)
# Android's upgrade-compatible APK key. Apple Developer ID/notarization and
# Windows Authenticode are optional publisher identities; each platform records
# its actual mode and an unavailable platform never blocks unrelated artifacts.
required_secrets=(SRC_DEPLOY_KEY R2_KEY_ID R2_APP_KEY)
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

git fetch --prune origin master --tags
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/master)" ] || {
  echo "::error::本地 master 不是 origin/master；先拉取并确认最新发布门禁。"
  exit 1
}

# 私有 tag 必须先存在，公开 tag 必须尚不存在。这样公开 CI checkout 的源码
# 对象和触发对象是一一对应的，不会因重打 tag 产生不可复现构建。
if ! gh api "repos/onesyue/yuelink/git/ref/tags/$TAG" >/dev/null 2>&1; then
  echo "::error::私有仓 onesyue/yuelink 没有 tag '$TAG' —— 先在私有仓打 tag 并推送。"
  exit 1
fi
if git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1; then
  echo "::error::公开 CI tag '$TAG' 已存在；release tag 不允许覆盖。"
  exit 1
fi

# Private-repository Actions can be prevented from starting by account billing,
# producing a red check with no steps or logs. A release must still have a
# machine-verifiable source gate; a maintainer's local test claim is not an
# acceptable substitute. Require the successful public source-attestation
# workflow produced by this exact yuelink-ci revision for the exact private
# commit behind the immutable source tag, then authenticate its proof artifact.
SOURCE_COMMIT="$(
  gh api "repos/onesyue/yuelink/commits/$TAG" --jq '.sha'
)"
if [[ ! "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  echo "::error::无法把私仓 tag $TAG 解析为完整 40 位 source commit。"
  exit 1
fi
BUILDER_COMMIT="$(git rev-parse HEAD)"
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

ATTESTATION_SNAPSHOT="$(mktemp -d "${TMPDIR:-/tmp}/yuelink-ci-source-attestation.XXXXXX")"
ATTESTATION_NAME="yuelink-source-attestation-$SOURCE_COMMIT"
gh run download "$ATTESTATION_RUN_ID" \
  -R onesyue/yuelink-ci \
  --name "$ATTESTATION_NAME" \
  --dir "$ATTESTATION_SNAPSHOT"
ATTESTATION_PROOF="$ATTESTATION_SNAPSHOT/source-attestation.json"
[ -s "$ATTESTATION_PROOF" ] || {
  echo "::error::source attestation run $ATTESTATION_RUN_ID 缺少 $ATTESTATION_NAME/source-attestation.json。"
  exit 1
}
jq -e \
  --arg source "$SOURCE_COMMIT" \
  --arg release "$TAG" \
  --arg builder "$BUILDER_COMMIT" \
  --arg run "$ATTESTATION_RUN_ID" \
  '.schemaVersion == 1 and
   .sourceRepository == "onesyue/yuelink" and
   .sourceSha == $source and
   .releaseTag == $release and
   .builderRepository == "onesyue/yuelink-ci" and
   .workflowSha == $builder and
   .runId == $run and
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
   ] | sort)' "$ATTESTATION_PROOF" >/dev/null || {
  echo "::error::source attestation proof 与 tag/source/builder/run 或完整 gate 集合不一致。"
  exit 1
}
gh attestation verify "$ATTESTATION_PROOF" \
  --repo onesyue/yuelink-ci \
  --signer-workflow onesyue/yuelink-ci/.github/workflows/source-attestation.yml \
  --source-digest "$BUILDER_COMMIT" >/dev/null || {
  echo "::error::source attestation provenance 验证失败。"
  exit 1
}
echo "✓ source attestation 已验真: run $ATTESTATION_RUN_ID, source $SOURCE_COMMIT"

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

git tag "$TAG"
git push origin "$TAG"

echo "✓ 已推送 tag $TAG 到 yuelink-ci → 构建已触发"
echo "  看进度: gh run watch -R onesyue/yuelink-ci"
if [[ "$TAG" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
  echo "  构建绿仅代表 unsigned candidate 就绪；随后在受保护的私仓/本机签名平面运行:"
  echo "  bash scripts/ci/promote_signed_manifest.sh ${BASH_REMATCH[1]}"
  echo "  (private Actions 可用时也可 workflow_dispatch sign-release-manifest.yml)"
  echo "  promotion 验收后:把精确签名根更新到 tests/fixtures/update-manifest-v1.json"
  echo "  并把旧根保留为 replay regression fixture；不得从 CDN 动态学习地板。"
fi
