#!/usr/bin/env bash
# 从私有仓重新同步 build.yml,并自动打回"指向私有源"的 2 处补丁 + 顶部 banner。
# 私有仓 build.yml 改了构建逻辑时跑这个。
#
#   ./sync-build.sh [私有仓路径]   # 默认 ../yuelink
#
# 锚点找不到时会 **硬失败**(说明私有仓的 checkout 步骤结构变了)→ 手动核对,
# 别让它静默打错补丁。
set -euo pipefail

SRC_REPO="${1:-../yuelink}"
SRC="$SRC_REPO/.github/workflows/build.yml"
DST=".github/workflows/build.yml"

[ -f "$SRC" ] || { echo "找不到 $SRC"; exit 1; }
cp "$SRC" "$DST"

python3 - "$DST" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()

def must_replace(old, new, label):
    global s
    if old not in s:
        sys.exit(f"::error:: 锚点未找到({label})—— 私有仓 build.yml 结构已变,手动同步本处。")
    if s.count(old) != 1:
        sys.exit(f"::error:: 锚点不唯一({label}, {s.count(old)} 处)—— 手动同步。")
    s = s.replace(old, new, 1)

# 1) 顶部 banner
must_replace(
"""name: Build YueLink

# Release builds.""",
"""name: Build YueLink

# ┌───────────────────────────────────────────────────────────────────────┐
# │ PUBLIC CI MIRROR — 本仓不含源码。源码在私有 onesyue/yuelink。见 README。         │
# │ 触发：在本仓打同名 tag 推送 → checkout 私有仓同名 tag。产物全进 R2。       │
# └───────────────────────────────────────────────────────────────────────┘
#
# Release builds.""",
"top-banner")

# 2) build job checkout
must_replace(
"""      - uses: actions/checkout@v6
        with:
          submodules: recursive
""",
"""      # CI 镜像仓：checkout 私有 onesyue/yuelink 同名 tag 源码到工作区根。
      - uses: actions/checkout@v6
        with:
          repository: onesyue/yuelink
          ref: ${{ github.ref_name }}
          ssh-key: ${{ secrets.SRC_DEPLOY_KEY }}
          submodules: recursive
""",
"build-checkout")

# 3) release job checkout(release job 的 steps: 紧跟一个裸 checkout)
must_replace(
"""    steps:
      - uses: actions/checkout@v6

""",
"""    steps:
      - uses: actions/checkout@v6
        with:
          repository: onesyue/yuelink
          ref: ${{ github.ref_name }}
          ssh-key: ${{ secrets.SRC_DEPLOY_KEY }}
          fetch-depth: 0
          fetch-tags: true

""",
"release-checkout")

open(p, "w", encoding="utf-8").write(s)
print("✓ build.yml 已同步 + 打补丁(3 处锁点全命中)")
PY

echo "现在 git diff 核对一遍,然后 commit + push。"
