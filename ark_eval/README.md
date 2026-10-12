# 方舟离线评测工程

当前主路线是**同一可信游戏宿主上的 GameIR vs JS 回调**，不是完整源码开发或 Vitric 替代品。
已实现阶段1任务契约、阶段2共享 ctx，以及阶段3固定审核目录中的真实隔离 JS 回调。
任意候选源码、真实模型 transport 和 TS 均未启用；工程参考回调不属于受测 AI 结果。
Codex 编写工程；方舟仅作为受测模型。Codex认证请求为0；用户001观测到HTTP401、生成次数未知，002报告1次模型调用且输出截断，003已离线核验HTTP200/精确模型/stop/OK及usage47/25/72。三次合计生成数未知，报告下限2。受测模型代码执行和重置卡使用均为0。

## 本地复现

在 `D:\GIR`，使用 `D:\python3.11\python.exe` 3.11.6；全部依赖为标准库。

```powershell
& 'D:\python3.11\python.exe' -X utf8 -m unittest discover -s headless_v01/tests -t . -q
& 'D:\python3.11\python.exe' -X utf8 -m unittest discover -s ark_eval/tests -t . -q
& 'D:\python3.11\python.exe' -X utf8 -m headless_v01.verify --output results\headless-callback-phase12-NEW
& 'D:\python3.11\python.exe' -X utf8 -m ark_eval.callback_verify --output results\callback-phase12-NEW
& 'D:\python3.11\python.exe' -X utf8 -m ark_eval.isolation_probe
```

阶段3实际执行须使用已安装的 Ubuntu 默认非 root 用户。运行器依赖本机 Podman 4.9.3、
cgroup v2/systemd 和已缓存的官方 Node 镜像；不自动安装、更改配置或转用 Windows 本机 Node。

```powershell
wsl -d Ubuntu-24.04 --cd /mnt/d/GIR --exec env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C python3 -m ark_eval.js_verify --output results/js-phase3-NEW
```

必须使用新输出目录。镜像的官方 repository digest 和实测版本见 `node_image_lock.json`；
派生镜像只加入固定 worker/supervisor 文件，构建上下文不含整个仓库。
初始化检查失败、资源限制偏移或清理失败都会关闭执行，不降级到 root 或宽松配置。

使用新的输出目录，保留已有证据。回调 verifier 的输出目录已存在时拒绝覆盖；原 verifier 接口保持不变。
回调 verifier 失败条件包括正向控制不通过、协议摘要漂移或已知缺陷未被独立 oracle 检出。
离线单元测试130项（既有100项 + Create配对29项 + 引号/反斜杠回显1项）；原运行时63项，含12个端到端 fixture，另有7个CLI检查。

`callback_verify` 输出：

- `protocol.json`：回调对照专用契约、预算、固定文件摘要和阶段状态。
- `public_pairs.json`：三个任务的两个自然表示、合法 starter、独立可见示例；不含最终测试内容或 Create 参考解。
- `controls.json`：78个固定 scenario/seed case，在两个表示控制上共156项检查，含事务结果、成本分项、消息和回放。
- `known_bug_controls.json`：52项缺陷控制，完整保留 PASS/FAIL；缺陷未在所有输入显现是预期现象。
- `model_runs.json`：6个真实受测臂均 NOT_RUN，tokens 和 AI 成功率为 null。
- `static_source_checks.json`：仅文本预检查，不是语法解析、沙箱证明或运行授权。
- `audits.json/public_requests.json`：复用离线适配器与Ledger的6次固定响应模拟；usage均为模拟值，真实调用0。

阶段报告见 [CALLBACK_PHASE12_REPORT.md](CALLBACK_PHASE12_REPORT.md)，契约见 [PROTOCOL.md](PROTOCOL.md)。
首阶段的 [ENGINEERING_REPORT.md](ENGINEERING_REPORT.md) 保留为历史记录，其数量对应旧提交。

## 共用宿主与边界

`headless_v01/runtime.py` 提取宿主 resolve/read/int_sub/write/add/emit/schedule/draw 原语。
GameIR AST 和 `ark_eval.host.TrustedContext` 复用这些原语以及原事务合并、冲突、事件、RNG 和回滚。
IR 编译静态权限检查仍保留，ctx 对传入参数独立检查；不允许传入任意 Python callable 或执行器。
唯一新增执行器为固定 `IsolatedJSRunner`，仅接受 `reviewed_js.py` 中的审核目录名及固定故障夹具。
回调消息有限、严格闭合、按 invocation/sequence 去重；故障锁存，不能吞错后发送 done 来恢复提交。
异常、超时、断连和退出**信号模拟**会回滚整个 tick；这不证明操作系统能限制或终止恶意进程。

成本分别记录公共宿主操作、IR AST 次数、旧 Runtime units 和回调线流量。
JS 计算单位仍为 null；阶段3另外记录真实 wall 时间与内核资源证据，不把它等同于 IR AST 单位。
公共宿主上限128/tick，不将 AST 次数当作 JS 的预算。
阶段2使用未优化 Core；有常量折叠的 Plan 被拒绝，避免跨表示漏计公共纯算术操作。

旧 `defineGame(Core JSON)` 入口改名为 `wrapper_format`，保留命令：

```powershell
& 'D:\python3.11\python.exe' -X utf8 -m ark_eval --output results\wrapper-format-NEW
```

它仅做包装格式控制，不执行 JS/TS，不加入回调模型评测分母，也不能作为公平 JS/Vitric 基线。

## Plan、预算与凭证

非秘密目标固定为 `https://ark.cn-beijing.volces.com/api/plan/v3`、`deepseek-v4.1-flash`。
2026-10-11用户确认：个人Plan、超额付费关闭、额度够、旧密钥撤销、允许自定义评测用途，
控制台精确标识为 `deepseek-v4-1-flash-260910`。这些是用户自报；公开条款独立核实仍UNKNOWN。
现有配对协议保留旧请求标识作为离线历史，不称其已被服务端接受；手动连通性入口只请求上述精确标识。
不购买、不升级、不用重置卡。已有适配器保持离线：`OfflinePlanAdapter.send()` 永久拒绝真实发送。
本轮未读取 `env.txt`，未提取、设置或传输任何密钥，未发送认证请求。

每任务每臂：prompt units 24000、输出 tokens总额4096、每次输出上限1024、尝试3、修复2、评测工具3。
prompt units 是保守 UTF-8/消息模板估算，**尚未验证真实 tokenizer token 上限**。
Ledger 预留调用后记账；失败/超时/缺失usage耗用尝试和预留额度，超额、配额/认证失败、模型漂移立即停止。
审计保存固定字段、UTC、usage和摘要，不保存密钥、headers或原始模型/提供商正文。
匹配的修复反馈接口尚未连接模型；最终 oracle 不提供给模型修复。

真实调用前的用户本地入口说明见 [USER_LOCAL_FIRST_CALL.md](USER_LOCAL_FIRST_CALL.md)。
首次HTTP401的证据边界与诊断修订见 [AUTH_DIAGNOSTIC_REPORT.md](AUTH_DIAGNOSTIC_REPORT.md)。
第二次HTTP200/空输出及离线诊断见 [PROBE002_DIAGNOSTIC_REPORT.md](PROBE002_DIAGNOSTIC_REPORT.md)。离线诊断CLI退出0只代表诊断完成，原模型判定仍为FAIL，不加入公平对照。
当前手动入口默认128输出tokens，参数范围1..256，v3分别报告HTTP/认证状态及应用判定；length记TRUNCATED。v1/v2按原16-token请求校验，原证据保留。实现与003用户命令见 [PROBE128_REPORT.md](PROBE128_REPORT.md)。
003已成功核验，不需要继续连通性重试。首次HTTP401原因未确认；旧延迟问答不视为配置更正。用户v1/pair001实际两臂HTTP200、正文为空、各4096 completion tokens、length/TRUNCATED，原失败保留。新v2入口及冻结公共输入见 [USER_LOCAL_CREATE_PAIR.md](USER_LOCAL_CREATE_PAIR.md) 与 [create_pair.freeze.json](create_pair.freeze.json)：同一精确模型、原公共提示与starter、IR/JS各一次、每臂4096输出tokens、0修复/工具，两臂统一显式thinking disabled；Plan实际参数兼容仍待用户pair002验证。候选存为有界candidate.txt，执行门禁关闭，不加入可信目录。旧v1冻结文件保留并只用于历史导入，没有恢复旧默认thinking的发送选项。
`python -m ark_eval.manual_probe --output NEW_DIRECTORY` 默认仅准备；独立的 `--user-local-send`
仅由人类在私有本地交互终端使用隐藏输入，单次Plan请求，无重试、重定向或按量兜底。
Codex只运行mock与离线导入，不能运行该发送模式；`OfflinePlanAdapter.send()`仍禁用。
现有模板仅为非秘密、阻断状态的形状示例，不能作为可执行公平请求。
Plan UNKNOWN 不阻止离线工程；固定参考 JS 已能隔离执行，任意模型候选源码仍永久被当前入口阻断。
A09/A12 仍 NOT_RUN，IR 未冻结。阶段3范围和证据说明见 [JS_PHASE3_REPORT.md](JS_PHASE3_REPORT.md)。
