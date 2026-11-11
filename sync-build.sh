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
import json
import re
from pathlib import Path
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

# Public master PR50 pins Go 1.27.4 for GO-2026-6859. Frozen private
# candidates may retain 1.27.3; never let a mirror refresh downgrade the builder.
go_versions = re.findall(r"^  GO_VERSION: '([^']+)'$", s, re.M)
if len(re.findall(r"^\s*GO_VERSION:", s, re.M)) != 1 or go_versions not in (["1.27.3"], ["1.27.4"]):
    sys.exit("::error:: unreviewed or ambiguous Go toolchain in private template")
if go_versions == ["1.27.3"]:
    must_replace("  GO_VERSION: '1.27.3'", "  # 2026-11-11: Go 1.27.4 addresses GO-2026-6859 (net/http MaxBytesReader race).\n  GO_VERSION: '1.27.4'", "public-Go-security-overlay")

# 1) 顶部 banner
must_replace(
"""name: Build YueLink

# Release builds.""",
"""name: Build YueLink

# ┌───────────────────────────────────────────────────────────────────────┐
# │ PUBLIC CI MIRROR — 本仓不含源码。源码在私有 onesyue/yuelink。见 README。         │
# │ 触发：signed tag 绑定 source SHA → checkout 私有仓 exact commit。产物进 R2。 │
# └───────────────────────────────────────────────────────────────────────┘
#
# Release builds.""",
"top-banner")

# 2) preflight job checkout
must_replace(
"""      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          ref: ${{ needs.source_binding.outputs.source_commit }}
          fetch-depth: 0
          submodules: recursive
          persist-credentials: false
""",
"""      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          repository: onesyue/yuelink
          ref: ${{ needs.source_binding.outputs.source_commit }}
          ssh-key: ${{ secrets.SRC_DEPLOY_KEY }}
          fetch-depth: 0
          submodules: recursive
          persist-credentials: false
""",
"preflight-checkout")

# 3) build job checkout
must_replace(
"""      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          ref: ${{ needs.source_binding.outputs.source_commit }}
          submodules: recursive
          persist-credentials: false
""",
"""      # CI 镜像仓：checkout public signed tag 绑定的 exact source commit。
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          repository: onesyue/yuelink
          ref: ${{ needs.source_binding.outputs.source_commit }}
          ssh-key: ${{ secrets.SRC_DEPLOY_KEY }}
          submodules: recursive
          persist-credentials: false
""",
"build-checkout")

# 4) release job checkout
must_replace(
"""      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          ref: ${{ needs.source_binding.outputs.source_commit }}
          fetch-depth: 0
          persist-credentials: false
""",
"""      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          repository: onesyue/yuelink
          ref: ${{ needs.source_binding.outputs.source_commit }}
          ssh-key: ${{ secrets.SRC_DEPLOY_KEY }}
          fetch-depth: 0
          fetch-tags: true
          persist-credentials: false
""",
"release-checkout")

# 5) dial-gate candidate checkout. Unique because of path: candidate.
must_replace(
"""      - name: Checkout dial-gate candidate
        uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          ref: ${{ (github.event_name == 'pull_request' && github.event.pull_request.head.sha) || needs.source_binding.outputs.source_commit }}
          path: candidate
          fetch-depth: 1
          submodules: recursive
          persist-credentials: false
""",
"""      - name: Checkout dial-gate candidate
        uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          repository: onesyue/yuelink
          ref: ${{ needs.source_binding.outputs.source_commit }}
          ssh-key: ${{ secrets.SRC_DEPLOY_KEY }}
          path: candidate
          fetch-depth: 1
          submodules: recursive
          persist-credentials: false
""",
"dial-gate-candidate-checkout")

# Public PR verification uses a distinct workflow name so Build listeners
# cannot reach release archive auditing from a verification run.
must_replace("on:\n  pull_request:\n", "on:\n", "public-release-triggers")
must_replace(
    "#   • pull_request runs only the dial gate; release jobs stay on signed tags",
    "#   • pull_request verification uses the separate YueLink dial verification workflow",
    "public-pr-workflow-comment")
must_replace(
    "      EXPECTED_CANDIDATE_REVISION: ${{ (github.event_name == 'pull_request' && github.event.pull_request.head.sha) || needs.source_binding.outputs.source_commit }}\n",
    "      EXPECTED_CANDIDATE_REVISION: ${{ needs.source_binding.outputs.source_commit }}\n",
    "public-dial-source-identity")

# 6) dial-gate baseline checkout.
must_replace(
"""      - name: Checkout dial-gate baseline v1.3.174
        uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          ref: v1.3.174
          path: baseline
          fetch-depth: 1
          submodules: recursive
          persist-credentials: false
""",
"""      - name: Checkout dial-gate baseline v1.3.174
        uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          repository: onesyue/yuelink
          ref: v1.3.174
          ssh-key: ${{ secrets.SRC_DEPLOY_KEY }}
          path: baseline
          fetch-depth: 1
          submodules: recursive
          persist-credentials: false
""",
"dial-gate-baseline-checkout")

# Public coverage timeout evidence is restricted to these explicit patterns.
must_replace(
    "      - name: Verify live immutable version-prefix bucket lock\n",
    """      # Preserve timeout attempts, half reports and classifications for diagnosis.
      # Upload only the listed test-event/LCOV evidence patterns, never other files.
      - name: Retain timed-out coverage group evidence
        if: failure()
        uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2
        with:
          name: coverage-timeout-evidence-${{ github.run_id }}-${{ github.run_attempt }}
          path: |
            coverage/source-gate/*.initial.jsonl
            coverage/source-gate/*.initial.info
            coverage/source-gate/*.half*.jsonl
            coverage/source-gate/*.timeout.json
          if-no-files-found: ignore
          retention-days: 3

      - name: Verify live immutable version-prefix bucket lock
""",
    "coverage-timeout-evidence")

# Public dependency caches bind the reviewed SDK identity and lockfile.
# Fail closed on an SDK bump until the evidence-backed plan is updated.
plan = json.loads(Path("tests/fixtures/flutter-coverage-groups-20261028.json").read_text())
fields = ("frameworkVersion", "frameworkRevision", "engineRevision", "dartSdkVersion")
sdk = plan["live_sdk"]
if set(sdk) != set(fields) or any(not re.fullmatch(r"[A-Za-z0-9.+-]+", sdk[f]) for f in fields):
    sys.exit("::error:: invalid reviewed SDK identity")
if f"  FLUTTER_VERSION: '{sdk['frameworkVersion']}'" not in s:
    sys.exit("::error:: workflow SDK differs from reviewed cache identity")
suffix = "-sdk-" + "-".join(sdk[f] for f in fields)
suffix += "-${{ hashFiles('pubspec.lock') }}"
# Include restore prefixes: a fallback must not cross SDK/lockfile identities.
s, count = re.subn(r"(?m)^( +(?:key: )?)(yue-(?:xcode-cc|gomod|gocache|pub|gradle)-v1-[^\n]+)$",
                   lambda m: m[1] + "yue" + suffix + "-" + m[2], s)
if count != 14:
    sys.exit(f"::error:: expected 14 cache keys/prefixes, found {count}")

open(p, "w", encoding="utf-8").write(s)
print("✓ build.yml 镜像内容已生成(6 处锁点全命中)")
PY

# Public builder retry policy is maintained in this repository (I32-E R11/R12).
# Apply with exact context; a source-template change must be reconciled explicitly.
patch --batch --fuzz=0 --forward "$GENERATED" < "$SCRIPT_DIR/scripts/public-release-retry.patch"

# Public release admission requires complete five-platform protocol evidence.
patch --batch --fuzz=0 --forward "$GENERATED" < "$SCRIPT_DIR/scripts/public-dial-summary.patch"

if [ "$CHECK_ONLY" -eq 1 ]; then
  if ! cmp -s "$GENERATED" "$DST"; then
    echo "::error::公开 CI build.yml 与私有仓发布工作流不同步。"
    diff -u "$DST" "$GENERATED" || true
    exit 1
  fi
  python3 "$SCRIPT_DIR/scripts/generate-dialgate-verify.py" --build "$GENERATED" \
    --output "$SCRIPT_DIR/.github/workflows/dialgate-verify.yml" --check
  echo "✓ 公开 CI build.yml 与私有仓发布工作流同步"
  exit 0
fi

cp "$GENERATED" "$DST"
python3 "$SCRIPT_DIR/scripts/generate-dialgate-verify.py" --build "$SCRIPT_DIR/$DST" \
  --output "$SCRIPT_DIR/.github/workflows/dialgate-verify.yml"
echo "✓ build.yml 已同步。现在 git diff 核对一遍,然后 commit + push。"
