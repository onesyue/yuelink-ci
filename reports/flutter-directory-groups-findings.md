# Child 31 — FLUTTER-GROUP 验收合同与车道

本批仅修改 yuelink-ci 的 Flutter coverage runner 及其单个测试文件。冻结快照命中时保留原分组；活树未命中时按父目录确定性分组，每组最多 8 个文件，ISOLATED 单独运行，macOS 专属文件继续排除。SDK 使用冻结计划中的完整对象；漏分文件以 PLAN_INCOMPLETE 失败，不切换执行模式。仅在 isolated 文件前及非零退出后清除编译缓存。300 秒上限、2044 测试下限和覆盖率并集保持不变；超时分组只二分重试一次，第二次失败即失败。

验收执行 `~/.venvs/yueto/bin/python -m pytest -q tests/test_flutter_coverage_runner.py`，使用测试替身，不运行 Flutter。下一次自然运行记录步骤秒数，不为取样触发运行。性能改善须由自然运行证据确认。本批回滚为撤销未发布差异。

| 车道 | 负责范围 | 文件所有权 |
| --- | --- | --- |
| 主会话 | 实现、定向测试、签名提交及 PR | scripts/run-flutter-coverage.py；tests/test_flutter_coverage_runner.py；本记录 |
| 独立审阅 | child 31 规格与差异的只读核对 | 无写权限，不重复执行测试 |

已有记录：根计划的 2026-10-25 CI stage time 节记载早期目录分组批次；当前基线 ba7986e 已含该实现，但 singleton 优先入口仍可绕过它。本批修复该入口并复用分组和一次拆分逻辑。

待议（Parked）：Serena 在修改前已报告 runner 和测试文件的动态模块加载类型诊断；本批不扩展类型整改。

## 本批验收结果

DONE：`~/.venvs/yueto/bin/python -m pytest -q tests/test_flutter_coverage_runner.py` 完整运行通过：35 passed、65 subtests passed，220.62 秒。测试使用模拟 Flutter/LCOV 可执行程序；未运行 Flutter 或全量测试。`git diff --check` 通过，Serena 对保存后修改区段未返回诊断。

DONE：独立审阅提出的负例辅助函数提前抛错、超时拆分第一半报告可能被第二半掩盖两项问题已修复并复核。拆分后每半均验证事件完整性和 LCOV 文件存在，任一半缺失即失败。

DONE：singleton 选择入口被移除；相关常量在 scripts/tests 的 Python 调用方检索无剩余引用，历史计划 JSON 保留。本批采用 `[skip ci]` 提交标记，避免 push/PR 自动运行；不调用 workflow dispatch。
