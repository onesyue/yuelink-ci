# ci34b 验收合同

本批公共同步以 yuelink #29 的 e06b73e3 为只读输入，基于 yuelink-ci #17 的
8fc1dd3。同步生成保留私有构建修正，公共依赖缓存键额外绑定 sdk17 已核实的
SDK 四字段和 pubspec.lock 哈希。签名、attestation、secrets 和权限块保持原值。
验收为指定源的 sync-build.sh --check 成功、唯一测试文件
 tests/test_pinned_flutter_action.py 通过，覆盖完整 SHA、缓存身份、信任边界及
apt 137→124 与重试；不运行真实构建。回滚为撤销本批提交。

| 车道 | 范围 | 文件所有权 |
| --- | --- | --- |
| 主会话 | 同步、测试与草稿 PR | sync-build.sh、build.yml、指定测试、本记录 |
| 独立审阅 | 只读审阅最终差异，不运行测试 | 无 |

证据来源：reports/sdk17-evidence.md、根车道记录 ci34/ci34b；两个检出 SHA
已与任务输入核对。首次 GitHub TLS 失败后第二次 pull --ff-only 成功且无更新。

## 本批交接

DONE_WITH_CONCERNS 测试延后：续作时独占 gauntlet 的 PID 为 55986，
命令为 heavy-run.py --exclusive bash scripts/gauntlet.sh；依照禁测指令，
本轮未运行测试、构建、同步校验或语义诊断。续作 pull --ff-only 返回
Already up to date。静态差异确认源检出为 e06b73e3，公共工作流相对该源
仅保留既有镜像补丁并扩展 14 个缓存键/恢复前缀。

独立只读审阅未发现确定的同步或测试逻辑错误。SDK 四字段与 lockfile
约束覆盖公共构建的 9 个依赖缓存；既有 pinned setup-flutter 的 SDK 下载
缓存仍按 OS、架构及版本键控，由现有安装器验证，不在本批改动范围。
新增负例覆盖 mutable action、缺失 SDK 字段/lockfile、宽泛恢复前缀及
恢复 dist 产物；apt 测试执行工作流函数并使用命令替身，不访问网络。

待 gauntlet 结束后，在本 worktree 运行（每条命令前先确认独占进程为空）：

```sh
./sync-build.sh --check ../yuelink-build-caches-20261028
YUELINK_SOURCE=../yuelink-build-caches-20261028 python3 -m pytest -q tests/test_pinned_flutter_action.py
```

草稿 PR 交付不表示验收通过；合并前必须补齐上述证据，且不得把私有 helper
缺失导致的 skip 记作通过。回滚路径为撤销本批提交。本批不请求新增重测试。
