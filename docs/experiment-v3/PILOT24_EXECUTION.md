# Pilot24 最小隔离执行入口（待审查、待批准）

本轮只做工程与人工控制验证。24 份外部候选执行为 0、API 请求为 0；不写人类批准。新入口不接受旧 v4、旧发送批准，也不实现 A/B/C 工具。

## 精确范围

`ark_eval/pilot24_execution.freeze.json` 绑定此前静态清单、172 个采集文件、24 槽/14 source hash、72 个冻结场景及本次新适配器/测试/文档。静态清单本身不能赋予执行权限；新增 freeze 的状态仍为 `SEALED_PENDING_REVIEW_AND_APPROVAL`。

计划评分为 6 任务 × 每任务4候选 × 12场景 = **288 行**。`R1-r1-gameir_text` 的原始 JSON 语法错误保持不变，生成级记 `FAIL_SYNTAX`，对应12行 NOTRUN；不为它启动 worker。其余 **23槽**只是可能可运行，须逐槽隔离准入。它们最多产生 **276 个主场景运行**：IR 11槽×12=132，JS 12槽×12=144。任何后续编译/ABI/导入/导出拒绝也是一次生成失败，其12场景 NOTRUN，不能伪记12次执行失败。原 R1 逻辑问题按原 oracle 观察，不修候选或测试。

每个生成槽有独立准入、预约和结果；同源码只共享内容寻址镜像缓存，不去重执行/评分，也不将重复源码或288用例当独立实验。研究单位仍为6任务簇，数据仍是零反馈两表示探索。

## 复用与新增边界

- JS 原生 callback、`NodeBackend`、`CandidateParserBackend`、`HostRuntime`、IPC、Profile/故障夹具和清理全部沿用。新 Host 只装配每题固定 ABI。
- IR 镜像复用 `image_payload`、原 supervisor/shim 和 `NodeBackend`。新增可信 Python worker 只绑定精确 source SHA、任务 ABI、用例输入 digest；在已有隔离中编译/解释，并用共享 `HostRuntime/replay_host` 产生固定观察。参考 expected 不送入 worker。
- `pilot24_public_runtime.freeze.json` 另绑定701个公开镜像包文件（Python 3.12.3 二进制/标准库/共享库及运行源码）。本freeze纳入它的字节和canonical摘要，打包前逐文件核验集合与内容；宿主包升级后相同批准不能接纳不同IR运行包。
- 新批次使用独立人类 receipt schema、独立 freeze 和 claim 根目录。`batch_key` 由原始采集/槽/场景确定，**不随新代码 scope hash 变化**；跨进程、跨输出目录只有一次原子目录领取，失败、Ctrl+C、写盘错误都不释放、不恢复、不重跑。静态验证及输出路径检查先于领取，领取先于 backend。
- 新记录用独占创建、flush/fsync，场景预约先持久化，结果先落盘再记完成。未确认运行保留未知下/上界，不写成已执行0次。旧原始包、发送 claim、v4 claim 均不修改。

## 额外工作（单列，不算主评分）

基础预检按每个实际进入的槽执行：JS 最多12槽×(1 profile+12故障)=156个可信 worker；IR 最多11槽×(1 profile+3故障)=44个可信 worker。鲜活证据超过550秒则刷新，刷新次数单记；已知静态拒绝不预检。

JS 隔离仅解析最多12次，IR 编译准入最多11次（不解释 handler）；IR 主运行各有一次同源码回放，最多132次；JS 仅转录回放，最多144次，不再启动候选回调。主场景内部可能含多个事件，因此 JS 物理 callback 次数单独记，不能等同144。IR 镜像最多6个不同source/ABI组合。所有 worker 清理需通过，最终只读检查容器库存，绝不删除他人容器。

语法/编译/ABI拒绝与主场景业务FAIL分别记；worker/IPC/清理/准入协议问题标 BLOCKED 并全批停止，其余 NOTRUN。完整契约仍 `NOT_CHECKED`。

回调前准入、bootstrap与清理失败由新runner锁存，不能被旧Host转成业务异常后误算模型FAIL。清理阶段所有普通异常均锁存，审计在独立finally中保留；锁存后入口阻止再次启动。CLI若在领取后遇到传播的写盘/协议异常，返回 `EXECUTION_INCOMPLETE_DO_NOT_RETRY`、null未知执行数和保留claim路径；不能回报0。已确认与未知主运行分别给下/上界；存在未知重放时总数为null，另列已确认数和未知记录。

## 入口与测试

安全默认入口只生成 scope 和288行 NOTRUN，不读取批准、不领取、不启动 backend，也不写批准模板：

```powershell
& D:\python3.11\python.exe -m ark_eval.pilot24_execution_eval --output results\pilot24-execution-preview-001
& D:\python3.11\python.exe -m unittest ark_eval.tests.test_pilot24_execution
```

只运行仓库人工控制的新适配器验证入口（Linux已有rootless环境）：

```text
python3 -m ark_eval.pilot24_execution_controls --output results/pilot24-adapter-trusted-controls-20261011-001
```

实际执行另需父级依据聚焦复审和本 freeze 的精确 scope 摘要，一次请求人类许可；只有固定非秘密 `results/pilot24-execution-human-approval.json` 的全字段匹配 receipt 加显式 CLI 开关才可进入执行。该 receipt 当前不存在，本轮不创建。批准仅针对本范围，不继承收集授权，不允许模型调用、修复、追加场景或新接口实验。
