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
cleanup() {
  if [[ "$SOURCE_SNAPSHOT" == */yuelink-ci-source-tag.* ]] &&
     [ -d "$SOURCE_SNAPSHOT" ]; then
    rm -rf -- "$SOURCE_SNAPSHOT"
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
fi
