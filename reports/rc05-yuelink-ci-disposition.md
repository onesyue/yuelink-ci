# PLAN 9.4 RC05：yuelink-ci 普通注释处置

基准 SHA：`ba7986ebbe4c07c322cc265b7c3eced287ef41c4`。当前 PR 分支已执行 `git pull --ff-only`，结果为 Already up to date。

本批验收：只删除第一方源码的历史叙述注释，保留可执行 token、API 文档、工具指令及所有约束说明。最多 15 个源码文件；本批实际修改 1 个。测试交由 box 调度，master 冻结期间不合并，不进入本次 release。

| 文件 | 处置 | 理由 |
|---|---|---|
| `tests/test_source_attestation_contract.py` | 删除基准第 237 行；其余保留 | 删除“2026-08-31 某次修复”的历史标题。其后的安装器 pin、缓存、失败关闭和精确计数约束全部原样保留。该行处于函数体普通注释位置，不是字符串、工具指令或可执行代码。 |
| `scripts/count-flutter-machine-tests.py` | 保留 | 注释解释 Flutter 协议前导输出、JSON 数组及失败关闭边界。 |
| `scripts/gh-read-retry.py` | 保留 | 注释解释签名拒绝不能重试、进程组清理及部分下载隔离等约束。 |
| `scripts/run-flutter-coverage.py` | 保留 | 注释解释执行模式不可变、容量证据边界、cgroup 根识别和空 LCOV 判据。 |
| `scripts/flutter-coverage-pilot.py` | 保留 | 普通整行注释扫描只命中 shebang，保留工具入口。 |
| `tests/test_flutter_machine_count.py` | 保留 | 普通整行注释扫描没有可删项。 |
| `sync-build.sh` | 保留 | 调用说明和锚点失败约束有用；heredoc 中的模板文本属于执行输入，不按注释删除。 |

本表是有限候选批次，不宣称全仓 RC05 或 RC07 完成。docs、vendor、生成工作流、迁移、备份恢复及生产均未修改。待议（Parked）：无新增调查项。

检查证据：逐行阅读源码及差异；本机未运行测试、构建、lint、语义分析或词法自动检查。删除一条独立 Python 普通注释不改变非注释 token；自动验证仍为 deferred，不计作测试通过。

box 预检命令（仓库根目录）：

```sh
python3 -m unittest discover -s tests -p 'test_source_attestation_contract.py'
git diff --check ba7986ebbe4c07c322cc265b7c3eced287ef41c4 HEAD
```

无需新增重测试，本批不增加代码或测试。冻结解除、独立审阅及 box 检查完成后由合并线决定集成；回滚可还原基准版本的该注释。
