#!/usr/bin/env bash
# 在本仓(yuelink-ci)打同名 tag 并推送 → 触发公开仓构建。
# 前提:私有仓 onesyue/yuelink 已经打好并推送了同名 tag。
#
#   ./release.sh vX.Y.Z      # 正式版
#   ./release.sh pre         # 预发布(强制覆盖)
set -euo pipefail

TAG="${1:-}"
if [ -z "$TAG" ]; then
  echo "用法: ./release.sh <vX.Y.Z|pre>"; exit 1
fi

# 校验私有仓确实有这个 tag(避免本仓 tag 了、私有仓没推 → checkout 失败)。
if command -v gh >/dev/null 2>&1; then
  if ! gh api "repos/onesyue/yuelink/git/refs/tags/$TAG" >/dev/null 2>&1; then
    echo "::error:: 私有仓 onesyue/yuelink 没有 tag '$TAG' —— 先在私有仓打 tag 并推送。"
    exit 1
  fi
  echo "✓ 私有仓已有 tag $TAG"
fi

if [ "$TAG" = "pre" ]; then
  # 预发布:floating tag,强制覆盖
  git tag -f pre
  git push -f origin pre
else
  git tag "$TAG"
  git push origin "$TAG"
fi

echo "✓ 已推送 tag $TAG 到 yuelink-ci → 构建已触发"
echo "  看进度: gh run watch -R onesyue/yuelink-ci"
