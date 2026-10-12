# Pilot24 采集封账与最小隔离评测静态交接

日期：2026-10-11。状态：`STATIC_PREPARATION_NOT_EXECUTION_AUTHORIZATION`。本文件和 [清单](pilot24-static-manifest.json) 只绑定已存在的数据，不构成执行批准。当前 **24 个生成槽、14 个唯一源码哈希、72 个固定场景、288 个计划主评分观察**；主评分仍为 **0 PASS / 0 FAIL / 288 NOTRUN**，完整契约 `NOT_CHECKED`。

## 1. 原始材料与可复验绑定

采集包：`D:\GIR\results\pilot24-user-local-001`，172 文件；24 个请求/预约/回执/审计/候选，以及逐槽 checkpoint 和顶层材料。全部 HTTP 200、finish_reason=stop，模型调用 **24..24**，修复/重试/工具调用 0。采集完成不代表代码正确。

| 绑定 | 值 |
|---|---|
| 维护基线 commit | `a8cd9753aef6e82be3b627bee25cd33497134913` |
| 材料 canonical digest | `5db1fbc7855755e0cae1c033294bee8a46c43192c669419da781350d4e2aefa1` |
| material freeze 文件 SHA256 | `5c824595221dd2fd68d7d50d5d7a4fa7bc56474c514132c6b603723fc34eeab1` |
| 采集 scope（已消耗，不可重用） | `44f5882a92ecabb7f22f7d6296d049c4332fab358ad08646548159a6b0bc45c5` |
| 原 summary SHA256 | `8bf83eef8020508673c36186fbcb2722b9d6105a305b59134e89ce932a37b115` |
| 172 文件 map 摘要，路径用 `/` | `65a9e3671e8bee7b61f947dad64ef39baabb73804422aaace11c81f23f02a701` |
| 历史 map 摘要，Windows 路径用 `\` | `753b3c808a647252f7d5f9ac2bc6ea8078c04350def3f33be49760e2f62c2e33` |

两种 map 摘要不同仅因为键的路径分隔符不同。清单保存全部逐文件 SHA256、两种摘要、106 材料绑定、74 运行绑定、13 材料验证证据和发送器绑定。摘要算法为 UTF-8 紧凑 JSON、key 排序、ensure_ascii=false、allow_nan=false 后 SHA256。不能把其中一个摘要误称为另一种格式的校验结果。

采集 start/completion claim 位于 `results/pilot24-send-claims/<scope>/`，completion 绑定 summary。逐槽机制是追加 checkpoint 与 response receipt，并核验已完成前缀；不是逐槽密码学哈希链。当前 terminal state 为 `FIXED_SCHEDULE_COLLECTED_UNREVIEWED`。不释放、不覆盖、不恢复或重用发送 claim。

仓库根目录只读复验：

```powershell
& D:\python3.11\python.exe docs\experiment-v3\verify_static_handoff.py
```

脚本只 hash 明确文件和解析 inert JSON，不导入 runtime 或候选。原始 results 在本地被 Git 忽略；清单只有摘要/计数/路径，不含候选原文。异机复验需要另行完整保留对应非秘密证据包；缺文件必须报 FAIL/BLOCKED，不能重造或回填。

## 2. 实际用量与重复

| 任务 | IR 输入/输出/总 tokens | JS 输入/输出/总 tokens |
|---|---|---|
| M1 | 5582 / 2716 / 8298 | 3434 / 386 / 3820 |
| M2 | 5034 / 2206 / 7240 | 3328 / 454 / 3782 |
| C1 | 5586 / 2546 / 8132 | 3434 / 342 / 3776 |
| C2 | 5384 / 3170 / 8554 | 3246 / 396 / 3642 |
| R1 | 6048 / 3827 / 9875 | 3594 / 538 / 4132 |
| R2 | 5100 / 2214 / 7314 | 3164 / 296 / 3460 |
| 合计 | 32734 / 16679 / 49413 | 20200 / 2412 / 22612 |

总用量 **52934 输入 + 19091 输出 = 72025 tokens**。实际输入/总成本不相等。相同 4096 输出上限并不等于严格总 token 预算匹配；输入 byte 估计不是已验证 tokenizer。10/12 组内重复对字节相同，仍是不同生成槽，不能选择去重后更好看的分母；重复、回放、同源码多用例也不是新的独立任务样本。

## 3. 静态发现及原评分规则

| 槽 | 固定原件 | 发现 | 后续处理 |
|---|---|---|---|
| R1-r1-gameir_text | SHA256 `778e89544fc240d3a3fb8590772e0548ca858f8c2f7cf5242b30e7aa0f1eea4f` | JSON 在 line 161 / column 18 / offset 2862 缺分隔符；完整返回 stop、1895 输出 tokens、7787 bytes | 保留原文；按原规则记 1 次生成 FAIL_SYNTAX，其 12 场景 NOTRUN。不得自动补括号、修复或重采集 |
| R1-r2-gameir_text | SHA256 `43ccdbd71d2c79419a20893bb62ae62ea0ce7e3266234f5d8a37e5c91effe38c` | JSON 可解析；未预约 cancel 分支发 CancelUnavailable，违反原规格的无效果返回，与固定 N02/B03 矛盾 | 静态逻辑发现，未执行；保留候选和 oracle，之后按原用例观察，不预填 gameplay FAIL/PASS |

其余 11 份 IR 的 JSON 语法可解析，不等于编译准入或业务正确。12 份 JS 本轮没有运行解析器或求值，语法准入仍 NOTRUN；文本检查不能替代安全证明。原 summary/audit 保留 COLLECTED_UNREVIEWED，不为静态判断改写采集证据。

后续终局报告必须区分三层：采集成功、生成级最终 PASS/FAIL/ERROR/NOTRUN、实际场景 PASS/FAIL/NOTRUN。已知语法失败使最多 23×12=276 个主场景可进入执行，前提是其余全部通过准入；计划分母始终 24 生成与 288 主观察。未执行的12项不能记为12次执行失败，更不能因没有跑出错误而记成功。回放、准入、压力/故障预检另记次数与开销，不混入288主观察。

## 4. 之后最小适配器的边界

本轮未实现任何新执行器、worker 或执行 claim，清单没有其 hash，因此不能直接拿清单开跑。下一工程阶段先提交以下薄层方案与测试，完成后集中提出精确执行审批：

1. **一次性 scope**：绑定本清单、24 槽顺序、14 原始源码 hash、每槽 task ABI/12 用例、运行时、oracle、新薄适配器/worker/image hash 和资源配置；新执行 claim 与已消耗的发送 claim、历史 v4 执行 claim 完全分开。不按路径泛化为“本目录所有候选可执行”。
2. **JS 准入与承载**：复用 `candidate_parser.mjs` / `CandidateParserBackend`（只解析不求值）、`NodeBackend`、`node_worker.mjs` 的原生 callback 路径，以及现有 `HostRuntime` / IPC /清理流程。它们的 callback payload 传递本身不是只能处理 player/door；旧 scope/runner 的固定候选和任务绑定才不能直接套给新24槽。新薄层仅按 frozen task ABI 配置 host 并绑定候选。不得用人工 factory 代执行模型源码。
3. **IR 准入与承载**：复用 rootless 容器传输、冻结 compiler/runtime、事务和回放。现有 `pair003_ir_worker.py` 固定旧 source SHA，v4 也有旧 scope；保持原件，新增最小任务绑定入口，在隔离中解析/编译本槽原始 IR 并核对固定 ABI。不要移除旧 gate、接受任意源码路径或在宿主直接 `HostRuntime(candidate)`。
4. **统一观察**：复用 Pilot24 固定每 tick 世界/状态/错误/有序业务事件/commands/replay oracle。独立控制夹具先验证 task ABI 和观察投影；不把 Door 专用投影硬套给六个任务，也不改 oracle 迎合候选。`pilot24_trusted.py` 只允许人工目录精确匹配，不改成候选后门。
5. **隔离与审计**：复用无网络、无用户挂载、清空环境、只读镜像、64 MiB、0.25 CPU、32 PID、10 秒、128 host ops/tick、32 JS wire/handler、256 wire/tick、8192 字节常规帧、32768 字节 bootstrap。用人工故障夹具证明资源/清理/断线/未知结果处理，绑定实际 backend 的鲜活证明；旧日志不能代替新 adapter 的隔离证明。
6. **失败处理**：语法/ABI/业务错误记模型构造失败；准入基础设施、worker/IPC、清理异常和未知结果记 BLOCKED/NOTRUN 并停机，不伪装模型 FAIL。逐槽预约、持久化观察和保留部分结果；不能因中断换目录重新执行。不得改变已收集候选、修 R1 或产生新请求。

新执行审批必须明确24槽/14hash/288计划观察，已知语法失败处置、最大276主执行、回放及人工预检额外工作、确切新实现hash和不可重用claim。没有这些具体绑定之前保持 BLOCKED。这不是要求重新批准已经用完的24次采集，而是待实现的新候选执行范围。

## 5. 工程证据与剩余事项

前次工程证据：`results/pilot24-p1-engineering-regression-20261011-001/tests.json`（Ark309），`results/pilot24-p1-headless-regression-20261011-001/summary.json`（Headless63、E2E12、CLI7、验收10/0/2），`results/pilot24-p1-focused-rereview-20261011-001/final.json`（发送器复核），以及已冻结的 trusted controls（240正向观察、36负向检出；工程证据不计模型成绩）。本清单再次核对这些文件摘要；此轮不重跑工程或候选测试。

下一步先开发并复核最小隔离适配器，然后提出具体执行申请；当前无需用户再次提供 API Key 或重新运行采集入口。A/B/C 的新调用、产品开发和公开推送均不在本轮范围。静态计划继续见 [PLAN.md](PLAN.md)。
