# 回调对照开发契约 @1

父级已选定：**同一可信游戏宿主上的 GameIR vs JS callback**。
本轮实施阶段1/2，阶段3真实源码执行关闭，TS暂缓。旧 Vitric 包未恢复，不声称复现或替代其基线。
本协议仍为开发协议；不是预注册冻结，也不是 holdout。原始研究需求位于
`docs/AI_GameIR_Cloud_Dot_Experiment_Plan.docx`。

## 三任务与固定输入矩阵

|任务|共同目标|两个自然 starter 的变化范围|
|---|---|---|
|P-C02-create|安全开门、消耗1把钥匙、正确事件和持久状态|IR空handlers / JS空onOpen；均提供同一ABI|
|P-C02-modify|把开门cost从1改成2|两个starter均为正确cost=1程序；单一cost绑定|
|P-C02-debug|修复倒置的钥匙比较|两个starter均只有keys<=cost这一缺陷|

这只是开门切片，不替代原计划全部 Create/Modify/Debug 序列或通用游戏开发。
每任务13个 scenario，各用seed 1/123，共78个固定case；重复seed不代表独立模型重复实验。

|输入类型|固定语义|
|---|---|
|keys=0/1/2/10且门关闭|按cost判断；成功一次Opened，失败MissingKey|
|keys=0/10且门已开|不读keys、不写状态、不发事件|
|无效player / 无效door / 两者均无效|顺序解析并返回；无副作用|
|同tick两次Open及后续tick重复Open|只成功一次，不能重复消费|
|无效player同时门已开|实体检查先于读取门字段|
|keys缺失但门已开|提前返回，不触发缺失cell错误|
|keys缺失且门关闭|InvalidEntity，整个tick回滚|

操作顺序以 `callback_contract.ORDERING` 为共同信息：resolve(player)，存在才resolve(door)，
读门open且require，已开返回，读keys且require，比较后依次写keys、写open、emit。
同一事件内，读自己的overlay；其他handler写入在事件合并前不可见。冲突/错误回滚根tick。
输入不要求player/door不同；当前矩阵不声称穷尽所有别名或任意自定义schema。

可见示例只实现独立Example事件写score=7；IR和JS得到相同示例ABI。
Create没有参考解；Modify/Debug仅提供题目必需的相同初始语义，不给最终测试或oracle。
`public_pairs` 中哈希仅用于固定版本，不是模型消息；模型messages不含测试case/期望值。

## 共享事务宿主和有限协议

共同宿主为原 `headless_v01.Runtime/Transaction/Job`；只提取原语和可信dispatch/audit钩子。
IR仍经原compiler验证，ctx重新验证类型、字段权限、合并policy、值不变量及事件schema。
同一宿主完成snapshot/own-write读取、效果ordinal、冲突检测、事件排序、RNG和提交。
当前callback不能访问backend、服务、文件、网络、时钟或提供自定义Python callable。

消息版本 `trusted-ctx@1`。格式为
`{v,invocation,seq,kind:'call',op,args}`，或done/fault终止消息。
字段严格闭合，seq是非bool整数且连续，invocation绑定tick/event/handler。
精确重传返回缓存响应，不重复执行宿主效果；同seq不同内容、错序、跨invocation均失败。
重传也占用消息预算。错误锁存，之后done不能挽回事务。
ctx.exists只映射wire resolve的option，ctx.sub映射wire int_sub；实际JS worker尚未实现。

|约束|开发协议上限|
|---|---|
|公共宿主操作|128/tick；两组相同|
|请求流量|32/invocation（含done/fault/重传）；256/tick|
|每请求/响应UTF-8|8192bytes|
|静态源码文本|16384bytes；不是执行许可|
|旧Runtime units|20000；兼容既有防护，单独报告|
|模型预算|prompt units24000，output tokens4096，单次output1024，attempt3，repair2，tool3|
|未来worker wall time|10秒；尚未实现/验证OS强制终止|

公共宿主操作与IR AST访问、JS计算成本分开；JS计算值当前null。
require、分支和局部变量不计公共宿主操作，回调require仍占线流量。
当前只支持未优化Core；拒绝带folds的Plan，避免折叠后的操作漏计。
当前数据不能比较执行性能、计算预算公平性或模型token效率。

固定Python消息模拟器 `DoorMockRunner`/`ScriptedMockRunner` 不加载JS源码。
JS_EXCEPTION/JS_TIMEOUT/JS_DISCONNECTED/JS_EXITED四种终止信号均触发根tick回滚，
包括先前microstep状态、RNG、ID/cursor、future/outbox以及尚未发布事件。
原Runtime结果保留尝试诊断；sidecar `published_events/commands` 在ABORTED时为空。
消息回放重新走同一ctx、验证响应和最终结果；篡改响应、额外segment/记录均拒绝。
不可解析或超长请求不记录原文，其request=null，明确ReplayUnsupported；不宣称能回放所有坏字节流。

## 独立测试、反馈和模型分母

`build_corpus.py` 仅从自然语言契约人工编写期望，不调用Runtime或参考解生成期望。
`final_tests.json` 固定于代码版本，测试中不重建；hash改变视为协议改变。
`callback_protocol()` 固定宿主、oracle、任务、桥接及预算文件摘要；run前后验证不漂移。
两组共享同一需求、允许信息、预算和反馈粒度；表示语法及自然starter允许不同。
每任务每臂独立模型session，按任务交替臂顺序；修复只看匹配的开发反馈。
最终case/oracle不作为模型工具返回；反馈接口和真实repair链尚未连接，不能声称已验证真实公平预算。
prompt估算不等于真实tokenizer；provider revision、计费口径、cache/reasoning usage需后续确认。

正向控制156检查；缺陷控制52检查，均为工程验证。
受测臂6个，全部NOT_RUN，AI成功率/tokens为null；失败、预算耗尽及未运行不得删去分母。
原wrapper格式记录单列，不加入回调模型分母。A12仍NOT_RUN；工程通过率不能成为AI优势证据。

## 后续门槛

只读能力检查不证明隔离。真实JS源执行需要先具备可销毁环境，再证明文件/凭证隔离、禁网络、
环境清理、进程/原生模块限制、CPU/内存/进程限制以及超时杀整个进程树。
可信evaluator/oracle在外，仅传有限JSON IPC；普通subprocess、Node vm和文本扫描均不满足此门槛。
本机当前缺少已证实可用环境，不能执行任何生成源码。

Plan自动评测许可保持UNKNOWN，账号/模型适用性和无超额付费配置未确认。
不得换普通按量路由，不购买/升级、不用重置卡。密钥操作仅能由用户本地进行；本工程不操作凭证。
Plan UNKNOWN不阻止离线阶段1/2；真实调用还需用户确认许可/账号/配额，真实源码执行另需隔离证据。
