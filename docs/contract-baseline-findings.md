# w06ci：可信契约基准

验收合同：与 onesyue/yuelink#28 配对，公开 source-attestation 必须从本次
`github.sha` 对应的 yuelink-ci 已审阅签名 update floor 选择源码 SHA，取回对象，
验证为候选的不同树祖先，并传入实际 Flutter worker。缺失、验签失败、自身、
同树和非祖先均拒绝。保留客户端原有重绑检测，不新增跳过路径。
轻测试限定为 `tests/test_contract_baseline.py`；独占 gauntlet 期间延期，完整
Flutter 与跨仓门禁交由合并线。

来源：`README.md` 的 manifest watchdog 约定及
`de02e5d8f50d618f07f0cc0bde3bf34dc3ba58f0` 纳入的
`tests/fixtures/update-manifest-v1.json`。记录为已签名 v1.3.157，源码
`cf6e8d1216d02944fec45da527921fd4963cc9df`。使用现有历史清单验证器校验签名及
完整策略；不从候选父提交、浮动标签或在线 CDN 学习基准。每次运行的基准由
精确 builder 提交冻结；后续 floor 前滚必须继续走原有审阅流程。

工作流通过现有只读 checkout 凭据获取该私有对象，再从本地 checkout 导入源码
对象库。完整 runner 的 detached worker 共享对象库，实际 Flutter 子进程显式
继承环境，拆组重试沿用同一路径。若候选正好等于 floor，必须失败；需要重验
已发布版本时应另行审阅更早的可信基准，不能自动回退。

待议（Parked）：无。

车道账本：主车道负责上述工作流、基准脚本、runner 传参和单文件测试，独占源码写入；
独立审阅车道仅静态读取本批差异，不运行测试、不写源码。

本轮续作执行 `pgrep -f 'heavy-run.*--exclusive'` 返回 PID 55986，未运行
测试、构建或语义诊断。上一轮记录称 9 项测试通过，但此文件未附运行回执，
本轮不将其作为当前验收通过证据。待 gauntlet 结束后运行：
`python3 -m unittest discover -s tests -p test_contract_baseline.py -v`。
测试源码包含真实签名验证及 Git/worker 子进程边界；运行结果仍待合并线补齐。

独立审阅：`review_w06ci` 静态审阅未发现阻断，确认来源、取回、失败检查及
worker 传参链路；`git diff --check` 无输出。结论为
`DONE_WITH_CONCERNS TESTS=deferred`，不代表真实 source-attestation 已通过。
