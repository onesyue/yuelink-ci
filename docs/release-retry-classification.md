# I32-E R11/R12 验收与交接

范围为当前 yuelink-ci PR 分支的发布重试分类。验收要求：明确可重试结果后成功；内容、身份或签名错误首轮失败且无 sleep；耗尽预算时最后一轮无 sleep。候选最多 8 轮、间隔 15 秒；tag 最多 12 轮、间隔 5 秒。原有签名、版本、摘要、exact builder 和 verified/valid 断言保留。

R11 使用私有源码校验器现有 `--json` 输出，仅退出码 1 且全部失败项为 binary HTTP 404 时重试。校验器把 sidecar 缺失和摘要不符合并为同一结果，因此这类结果立即失败；manifest 缺失、网络错误和未知结果也不推断为传播延迟。原校验器文件与全部断言不变。

R12 先核对内容，再仅允许 verified=false 且 reason 为 gpgverify_unavailable/gpgverify_error 时等待。release.sh 的 API 读取失败仅明确 404 可等待。其余状态立即拒绝。最终成功仍必须 exact builder + verified=true + reason=valid。

状态定义依据：https://docs.github.com/en/rest/git/tags 。两个 gpgverify 状态表示验签服务不可用/通信错误，不代表签名已通过。

工作流变更由 scripts/public-release-retry.patch 作为公共构建器策略维护；sync-build.sh 在原有私源镜像转换后以零 fuzz 应用，模板冲突必须显式解决。未修改其他仓库。后续私源吸收相同策略时需同步移除此 overlay，避免重复应用。

新增 tests/test_release_retry_classification.py 执行真实 shell 循环，替换外部 API、Dart 与 sleep；现有 test_release_remote_tag_behavior.py 更新无效签名的拒绝状态断言。本机按调度要求未运行测试或构建，验收状态为 deferred，由 box 执行：

```sh
python3 -m pytest -q tests/test_release_retry_classification.py tests/test_release_remote_tag_behavior.py
```

合并线另在具备私源检出的环境验证同步契约：`bash sync-build.sh --check <yuelink源码检出绝对路径>`。本轮不触发工作流、不发布；回滚方式为在 PR 分支追加 revert 提交。
