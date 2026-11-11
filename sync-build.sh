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

# Public Build never runs implicit private candidates on a pull request.
# PR code validation stays in Policy CI; archive/sign only follows tag admission.
must_replace("on:\n  pull_request:\n", "on:\n", "public-release-triggers")
must_replace(
    "#   • pull_request runs only the dial gate; release jobs stay on signed tags",
    "#   • pull_request changes run Policy CI, never this release builder",
    "public-pr-workflow-comment")

# Preserve the public archive boundary label consumed by existing retry tests.
# The private131 template uses this equivalent reviewed comment; no commands
# or retry behavior are changed by this normalization.
private_archive_comment = (
    "      # Signing/immutable archival does not advance the public update root.\n"
    "      # Root/cohort promotion belongs to the separately authorized workflow.\n"
)
public_archive_comment = (
    "      # Build/sign and client exposure are separate. Mode comes FIRST so an\n"
    "      # older private script rejects it as an invalid version before any write.\n"
)
if private_archive_comment in s:
    must_replace(private_archive_comment, public_archive_comment, "public-archive-boundary-comment")

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

# Accept only the two reviewed private candidate-reference profiles. Never
# restore a hardcoded branch when the template binds its exact PR head.
dial_refs = ["${{ github.event.inputs.dialgate_candidate_ref || (github.event_name == 'pull_request' && 'feat/real-dial-gate-20261111') || needs.source_binding.outputs.source_commit }}", "${{ (github.event_name == 'pull_request' && github.event.pull_request.head.sha) || needs.source_binding.outputs.source_commit }}"]
present_dial_refs = [ref for ref in dial_refs if f"          ref: {ref}\n          path: candidate\n" in s]
if len(present_dial_refs) != 1:
    sys.exit("::error:: unknown or ambiguous dial candidate source binding")
dial_ref = present_dial_refs[0]
# 5) dial-gate candidate checkout. Unique because of path: candidate.
must_replace(
"""      - name: Checkout dial-gate candidate
        uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          ref: ${{ github.event.inputs.dialgate_candidate_ref || (github.event_name == 'pull_request' && 'feat/real-dial-gate-20261111') || needs.source_binding.outputs.source_commit }}
          path: candidate
          fetch-depth: 1
          submodules: recursive
          persist-credentials: false
""".replace("${{ github.event.inputs.dialgate_candidate_ref || (github.event_name == 'pull_request' && 'feat/real-dial-gate-20261111') || needs.source_binding.outputs.source_commit }}", dial_ref),
"""      - name: Checkout dial-gate candidate
        uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          repository: onesyue/yuelink
          ref: ${{ github.event.inputs.dialgate_candidate_ref || (github.event_name == 'pull_request' && 'feat/real-dial-gate-20261111') || needs.source_binding.outputs.source_commit }}
          ssh-key: ${{ secrets.SRC_DEPLOY_KEY }}
          path: candidate
          fetch-depth: 1
          submodules: recursive
          persist-credentials: false
""".replace("${{ github.event.inputs.dialgate_candidate_ref || (github.event_name == 'pull_request' && 'feat/real-dial-gate-20261111') || needs.source_binding.outputs.source_commit }}", dial_ref),
"dial-gate-candidate-checkout")

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

# Never let a template refresh restore automatic root promotion.
# Exact caller/step anchors admit old and migrated private templates only.
python3 "$SCRIPT_DIR/scripts/apply-archive-only-overlay.py" "$GENERATED"

# Rewrite only the known updater-candidate force block. A full template
# refresh must still fail closed on an unknown or partial force anchor.
python3 "$SCRIPT_DIR/scripts/apply-update-force-overlay.py" "$GENERATED"

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
