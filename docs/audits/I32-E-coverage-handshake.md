# I32-E（C13）R9/R13 验收与交接

依据：根仓 reports/INDEX.md 的 I32（E 节 R9、R13 与补丁建议 1、4）。I33 将计时与阈值问题归入 I32，本线不重复架构审计。

验收合同：R9 使用测试专用就绪管道，观察器确认首个事件落盘后触发 GNU timeout 的 SIGALRM；TERM handler 等待观察器确认信号公告后输出后置事件。断言真实退出码 124、两个事件各一次、顺序与原字节哈希。缺事件不得通过；partial 字节保留后触发外层 watchdog，并检查 SIGKILL、回收与回执。R13 在首轮 124 后检测 error、失败 testDone、done=false，阻止分半覆盖失败；保留首轮原件和 124 回执，分半回执独立。无失败的超时仍允许分半成功，二次失败仍拒绝。300/30/350 秒产品预算不变。

车道账本：ylr9 为唯一源码写者，拥有 scripts/run-flutter-coverage.py、tests/test_flutter_coverage_pilot.py、tests/test_flutter_coverage_runner.py；独立审阅车道仅只读检查这三个文件，不运行测试、不写源码。

当前证据：工作树基于 origin/master，同步时无差异。代码与负控已编写；Mac 未运行测试、构建或语义诊断。测试结论为 deferred，不能据此认定门禁通过。Serena 诊断按本轮只读/改代码限制交由后续验证，未将缺失诊断视作通过。

独立只读审阅：R13 未发现阻塞缺陷；R9 指出 GNU timeout 可能向进程及进程组重复发送 TERM，使等待确认的 handler 重入。已将 handler 首条操作设为忽略后续 TERM，保留真实首次终止与各一次事件断言。运行验证仍等待 box。

box 预检（Linux，GNU timeout 与 /usr/bin/time）：

```sh
python3 -m pytest -q tests/test_flutter_coverage_pilot.py tests/test_flutter_coverage_runner.py
```

合并线负责对本 PR 精确提交执行以上两个直接相关文件，独立审阅并通过后按冻结规则处理。本线不合并、不部署、不签发版。R10/R11/R12 不在范围内。

待议（Parked）：无新增范围外调查。
