# sdk17：发布证据与冻结 SDK

最新公开发布为 v1.3.157。签名源码证明与五个平台构建日志均为 Flutter
3.47.8，Dart 3.13.7；不存在应回退到 3.47.7 的发布证据。

## 复核命令与输出

以下命令在当前 yuelink-ci worktree 执行；证据目录为 `/tmp/sdk17-evidence`。

```sh
gh release list -R onesyue/yuelink-ci --limit 5
gh release view v1.3.157 -R onesyue/yuelink-ci --json assets,body,targetCommitish
gh api repos/onesyue/yuelink-ci/attestations/sha256:9fb3084bbe98e1f2b567706fd6c644caede6c291f9cff449d9e38bcfdbaae2a5
gh api repos/onesyue/yuelink-ci/actions/runs/40464318467/artifacts
gh run download 40464318467 -R onesyue/yuelink-ci -n yuelink-source-attestation-cf6e8d1216d02944fec45da527921fd4963cc9df -D /tmp/sdk17-evidence/source
gh attestation verify /tmp/sdk17-evidence/source/source-attestation.json -R onesyue/yuelink-ci --signer-workflow onesyue/yuelink-ci/.github/workflows/source-attestation.yml --cert-oidc-issuer https://token.actions.githubusercontent.com --format json > /tmp/sdk17-evidence/verification-workflow.json
gh run view 40474308178 -R onesyue/yuelink-ci --log > /tmp/sdk17-evidence/build-40474308178.log
shasum -a 256 /tmp/sdk17-evidence/source/source-attestation.json /tmp/sdk17-evidence/build-40474308178.log
```

- release list 首行：`YueLink v1.3.157 native sources Latest v1.3.157 2026-10-27T09:01:50Z`。
- Release body：`ATTESTED_SOURCE_COMMIT=cf6e8d1216d02944fec45da527921fd4963cc9df`。
- Release 仅列 native sources 及 checksum；native archive attestation API 返回 HTTP 404，故转取发布对应源码证明与构建日志，未把 native sources 当作 SDK 证明。
- 源码证明 artifact id `12719248909`，文件 `source-attestation.json`；`gh attestation verify` 限定 source-attestation workflow 与 GitHub OIDC issuer 后退出 0。
- 证明 SHA256：`27db0fa14a05a19dcdc68d4497f95d9b4055150efb26c3aa2c8e1fcd4f6cc432`。
- 字段 `releaseTag=v1.3.157`，`sourceSha=cf6e8d1216d02944fec45da527921fd4963cc9df`，`runId=40464318467`，`workflowSha=09e350668e26589d3ca49ef980de7a51e3697d1d`。
- `reuse.flutter.toolchain.frameworkVersion=3.47.8`。
- `reuse.flutter.toolchain.dartSdkVersion=3.13.7`。
- `reuse.flutter.toolchain.frameworkRevision=737ba8f885dc774b53b442759999a312c9461e4e`。
- `reuse.flutter.toolchain.engineRevision=cf636b1dfb818dd6a3ff16e390e93b1a036969c8`。
- 构建 run `40474308178` 成功，headBranch `v1.3.157`，headSha 同上 workflowSha。
- 日志 SHA256：`ee26ce1b3745deeace58bc3e8af60c4fab16fa63757d44aaa74acd90fffeb0c6`。
- 日志行 2440（Android）、7596（Windows）、10334（Linux）、15265（iOS）、17371（macOS）均含 `Flutter 3.47.8 verified at`；Linux 行时间 `2026-10-27T08:44:51.8114756Z`。

## 变更与签名

既有计划以 JSON、runner 内的 SHA256 和 Git 签名提交绑定，没有独立的计划签名旁文件。
新增 `flutter-coverage-groups-20261028.json` 保留旧计划所有字段，新增 `live_sdk`
及证据引用。旧 `sdk` 仍用于精确历史快照，活树只取 `live_sdk`；避免把 3.47.7
历史覆盖率宣称为 3.47.8 的实测基线。新计划 SHA256 为
`818d2d37d03aa6666595fb4e8af16881c971d4d8865702e0543ab95a77fdd584`。

依赖检查定位 runner 常量、测试夹具路径及 README；历史 pilot 和历史 runner
保留原文件。安装器及其 checksum、source-attestation workflow 无需修改。
本批以默认 GPG `git commit -S` 签名，不签发版、不合并。

## 验证边界

只运行 `tests/test_flutter_coverage_runner.py`，不执行 Flutter、全量测试或构建。
首轮为 35 passed、65 subtests passed、1 failed；失败根因为历史字节恢复测试
通过当前计划指针读到了新增 live_sdk 字段。修复为明确读原历史 JSON，保留旧
摘要断言；新回归继续要求新计划全部历史字段与原文件一致。
独立只读审阅不重复执行测试。修改区段 Serena 诊断为空。

DONE：修复后 `~/.venvs/yueto/bin/python -m pytest -q tests/test_flutter_coverage_runner.py`：36 passed、65 subtests passed，174.50 秒；`git diff --check` 通过，独立审阅及修复复核通过。日志：`/tmp/sdk17-evidence/test-final.log`。
