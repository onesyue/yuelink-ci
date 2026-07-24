#!/usr/bin/env bash
# 从私有仓重新同步 build.yml,并自动打回"指向私有源"的 4 处补丁 + 顶部 banner。
# 私有仓 build.yml 改了构建逻辑时跑这个。
#
#   ./sync-build.sh [--check] [私有仓路径]   # 默认 ../yuelink
#
# 锚点找不到时会 **硬失败**(说明私有仓的 checkout 步骤结构变了)→ 手动核对,
# 别让它静默打错补丁。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then
  CHECK_ONLY=1
  shift
fi
if [ "$#" -gt 1 ]; then
  echo "用法: ./sync-build.sh [--check] [私有仓路径]" >&2
  exit 2
fi

SRC_REPO="${1:-../yuelink}"
SRC="$SRC_REPO/.github/workflows/build.yml"
DST=".github/workflows/build.yml"
GENERATED="$(mktemp)"
trap 'rm -f "$GENERATED"' EXIT

[ -f "$SRC" ] || { echo "找不到 $SRC"; exit 1; }
cp "$SRC" "$GENERATED"

python3 - "$GENERATED" <<'PY'
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

# 2) preflight job checkout
must_replace(
"""    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          fetch-depth: 0
          submodules: recursive
          persist-credentials: false

      - uses: subosito/flutter-action@1a449444c387b1966244ae4d4f8c696479add0b2 # v2.23.0
""",
"""    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          repository: onesyue/yuelink
          ref: ${{ github.ref_name }}
          ssh-key: ${{ secrets.SRC_DEPLOY_KEY }}
          fetch-depth: 0
          submodules: recursive
          persist-credentials: false

      - uses: subosito/flutter-action@1a449444c387b1966244ae4d4f8c696479add0b2 # v2.23.0
""",
"preflight-checkout")

# 3) build job checkout
must_replace(
"""      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          submodules: recursive
          persist-credentials: false
""",
"""      # CI 镜像仓：checkout 私有 onesyue/yuelink 同名 tag 源码到工作区根。
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          repository: onesyue/yuelink
          ref: ${{ github.ref_name }}
          ssh-key: ${{ secrets.SRC_DEPLOY_KEY }}
          submodules: recursive
          persist-credentials: false
""",
"build-checkout")

# 4) release job checkout
must_replace(
"""    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          fetch-depth: 0
          persist-credentials: false

      - name: Set up Flutter for release-candidate verification
""",
"""    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          repository: onesyue/yuelink
          ref: ${{ github.ref_name }}
          ssh-key: ${{ secrets.SRC_DEPLOY_KEY }}
          fetch-depth: 0
          fetch-tags: true
          persist-credentials: false

      - name: Set up Flutter for release-candidate verification
""",
"release-checkout")

open(p, "w", encoding="utf-8").write(s)
print("✓ build.yml 镜像内容已生成(4 处锁点全命中)")
PY

if [ "$CHECK_ONLY" -eq 1 ]; then
  if ! cmp -s "$GENERATED" "$DST"; then
    echo "::error::公开 CI build.yml 与私有仓发布工作流不同步。"
    diff -u "$DST" "$GENERATED" || true
    exit 1
  fi
  echo "✓ 公开 CI build.yml 与私有仓发布工作流同步"
  exit 0
fi

cp "$GENERATED" "$DST"
echo "✓ build.yml 已同步。现在 git diff 核对一遍,然后 commit + push。"
