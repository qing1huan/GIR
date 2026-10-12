# Pilot24 接手核实与进度报告 — 2026-10-12

这是截至 **2026-10-12 23:54:30 UTC+8** 的未完成阶段快照。保留 PLAN v3.0 和 Architecture v0.1；不预设 GIR 胜出。数据是六类玩法 × 两轮生成 × 两种表示的探索实验：24份原答案、14个不同源码哈希、288个固定场景。288不是模型生成次数，也不是独立统计样本；这批不能重标为正式 A/B/C 或多轮维护对照。

## CURRENT_STATE

本地运行代码 HEAD 为 `73f05ab479a17781295016e94446fa32dbd1ca5a`，分支 `ark-js-phase3-20261011`。实际执行以 `ark_eval/pilot24_recovery.v5_1.freeze.json` 的30项绑定为准，冻结 SHA256 `fc22a6b6b71ed2ea5d15db24922be12092a84440e617e9a95004baa03cc3d0f9`；scope `63d2c9432483f0a9843eda05436a077a838ccd09493556e7ffcf99de68506628`。本次完整核查30/30匹配，未改变冻结、隔离、预算、路径或任何候选。

历史交接在12:59 UTC仍是启动检查；后续文件已证明精确51项控制器获得启动回执、一次性 claim 已消费并产生结果。这里的事实来自文件，不构成当前进程存活证明。尚无 `summary.json` 或终局记录，绝不再次启动、清理 claim、重复预约或重跑候选。

2026-10-12 21:02及21:04（北京时间）的原执行请求及只读核查被安全系统以 `Potentially unintended activity` 拦截，未提供进一步原因。随后 WSL 两次返回 `E_ACCESSDENIED`，CIM 查询也被拒绝；没有确认这些访问恢复，不通过替代路线查询。现阶段仅文件核查继续可用；当前进程状态保持 UNKNOWN。

近期环境曾切为 read-only：B01在内存核验为PASS，但无法保存快照/游标，因此聊天自动接续暂停。文件写入范围后来恢复，本次已补记该已汇报结果，并完整核验新B02；B01没有重复计数。没有通过权限变化推定WSL/CIM或执行权限恢复。

此前 GitHub 草稿快照为 `97328d4807192414766beacd35ad05b3fc19417f`。20个已发布的续接工程/测试/冻结/进度文件与当前本地文件逐字匹配，因而本次主要发布五个独立核查工具及脱敏进度材料；本地39个中间开发提交与原工作树保持原样。

## PILOT24_SCORE

| 原288个场景 | Game IR | JS callback | 合计 |
|---|---:|---:|---:|
| PASS | 112 | 118 | **230** |
| FAIL | 2 | 0 | **2** |
| NOTRUN | 30 | 24 | **54** |
| HARNESS_ERROR / 历史未知 | 0 | 2 | **2** |
| 总数 | 144 | 144 | **288** |

与接手基线221 PASS相比，精确51项续跑现已有9个新的持久化PASS：

- `C1-r2-gameir_text`：R02、R03、R04。该槽固定12项全部通过。
- `M2-r2-gameir_text`：N01–N04、B01、B02。该槽现有6项通过。

当前预约—结果为9/9，无预约后待确认项，42项未预约。54个NOTRUN包含12项原IR语法错误和42项尚未预约；不要把它们填成通过。预约后无结果的项目在运行期间仅标待确认，不能自动改为HARNESS_ERROR。

每个新结果均独立检查原源码/冻结场景绑定、原人工期望、主执行次数、IR/JS对应回放、清理、rootless/无网络/只读文件系统、CPU/内存/pids/墙钟约束及原结果指纹。工程测试、预检、准入、启动回执及可信控制不算评分行。按唯一 `slot_id + case_id` 合并；重读旧行不增加成绩。14份派生快照的9个唯一新结果指纹核验通过。

| 完整证据核查 | 匹配数 | 结论 |
|---|---:|---|
| 原始答案包 | 172/172 | 原字节未变 |
| 封存历史文件 | 1509/1509 | 指纹未变 |
| 前次生产文件 | 127/127 | 指纹未变 |
| 当前 v5.1 运行绑定 | 30/30 | 精确匹配 |
| 旧静态运行绑定 | 70/74 | 4个历史差异仍保留，不宣称全绿 |

旧静态差异为 `candidate_eval.py`、`js_runner.py`、`js_verify.py`、`node_backend.py`。它们与当前冻结匹配，不代表旧冻结也通过；原门槛未被修改。历史27项针对性测试、6组真实隔离控制和更早验收均是历史工程证据，本轮没有将它们计入评分或冒充重新全回归。

本轮实际命令（在 `D:\GIR`，均禁用字节码写入）为：

```powershell
& D:\python3.11\python.exe -B D:\GIR\results\pilot24-takeover-readonly-audit-20261012-001\poll-score-files.py
& D:\python3.11\python.exe -B D:\GIR\results\pilot24-takeover-readonly-audit-20261012-001\verify-observed-results.py
& D:\python3.11\python.exe -B D:\GIR\results\pilot24-takeover-readonly-audit-20261012-001\observe-forward51.py --output results/pilot24-forward51-file-observation-20261012-001
& D:\python3.11\python.exe -B D:\GIR\results\pilot24-takeover-readonly-audit-20261012-001\audit.py
& D:\python3.11\python.exe -B D:\GIR\results\pilot24-takeover-readonly-audit-20261012-001\prepare-score-material.py
```

五份已完成脚本的原字节副本与核验SHA见 [只读核查工具](readonly_audit/README.md)。[脱敏JSON汇总](pilot24-takeover-public-summary-20261012.json)含槽位/表示计数、唯一结果指纹、实际冻结版本及本地证据定位，但不含原始答案、执行轨迹、完整管理账本或运行包。GitHub快照不能替代本地实验现场。

本轮本地证据路径（SHA256见脱敏JSON）：

- `results/pilot24-forward51-file-observation-20261012-001/snapshot-20261012T155430-908038.json`
- `results/pilot24-takeover-readonly-audit-20261012-001/report-publication-20261012T155607-187732.json`
- `results/pilot24-takeover-readonly-audit-20261012-001/score-material-230-001.json`
- 生产目录 `results/pilot24-recovery-score-forward51-executed-20261012-001`。

## FAILURE_ANALYSIS

**候选语法错误**：`R1-r1-gameir_text` 原JSON解析在第161行、第18列报告 `Expecting comma delimiter`，12项为原规则下的STATIC_REJECTED/NOTRUN。原答案保留，无修补、剔除或重生成。

**候选玩法错误**：`R1-r2-gameir_text/N02`、`B03` 多发 `CancelUnavailable`。N02重复取消在第一次释放后仍发额外事件；B03未预约票据应静默返回却发该事件。独立期望不匹配，而回放及清理核验通过，所以是行为FAIL。原IR保持原样。

**历史未知**：`C2-r2-js_callback/N02`、`C1-r2-js_callback/R04` 已预约，但没有足以确认主执行结局的证据，保持两项HARNESS_ERROR/UNKNOWN；永久禁止重跑或改判通过。不能把它们归因于模型错误，也不能据现有证据认定某种具体设施故障。

**未运行**：42项从未预约属于评分进度未完成。预检成功不等于这些场景已执行。权限/游标写入问题是本次核查工作流阻断，不是新增候选或设施评分失败。

**性能与因果边界**：曾观察到WSL p9等待、反复读取 `/mnt/d` 运行包与历史证据，但尚未证明某个文件是具体瓶颈。没有为加速迁移路径或放宽哈希/隔离条件。当前分臂计数未覆盖相同完整运行范围，不作可靠性优势、纯表示因果、运行速度或经济收益结论。

原24次采集已知token为IR 49,413（输入32,734、输出16,679），JS 22,612（输入20,200、输出2,412），合计72,025。此为采集记录的已知usage，思考/缓存额外字段、货币成本及完整工具/墙钟成本尚无可核实值；续接核查新增模型调用0。不同表示的材料长度、仅六个任务及跨轮重复源码均限制外推，不将场景行当独立样本做显著性结论。

## NEXT_GATE

沿用同一已消费控制器，仅观察后续结果；不建立新执行许可、不重跑或补题。截止快照时精确未预约余项：

| 槽位 | 场景 | 数量 |
|---|---|---:|
| M2-r2-gameir_text | B03、B04、R01–R04 | 6 |
| M2-r2-js_callback | N01–N04、B01–B04、R01–R04 | 12 |
| M1-r2-js_callback | N01–N04、B01–B04、R01–R04 | 12 |
| M1-r2-gameir_text | N01–N04、B01–B04、R01–R04 | 12 |
| 合计 | 尚未预约，不是新授权 | **42** |

终局出现后完整核对原288项和51项预约—结果，复核原172/1509/127文件及当前绑定，再生成独立收尾材料。STOPPED_ON_BLOCK、证据矛盾/指纹变化或允许文件访问被拒绝时保留现场并暂停自动接续，不自行启动新评分；仅时间戳不变或PID不可见不能证明进程死亡。

评分收尾前不实现A/B/C接口、不接引擎、不改架构、不扩产品。本批收尾后才报告A/B/C最小接口缺口、预计成本及所需授权；新增模型请求、正式三组实验和平台扩展另批。PLAN v3.0与冻结Architecture v0.1继续有效，架构未完成验收项不能称已关闭。
