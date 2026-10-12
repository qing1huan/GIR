# v4 四候选精确执行入口

一次性执行资格：完成批准、scope、源码、oracle 和输出路径的只读验证后，在任何 backend 启动前，以原子独占创建目录领取 `results/v4-batch001-execution-evidence/<scope_digest>/`。同一 scope 更换输出目录、再次启动或并发启动均只能有一个领取者。该目录只存非秘密领取及阶段记录；即使写记录失败、预检失败或执行中断也不释放，禁止删除领取记录来重试。

领取本身不代表执行完成。预检逐臂记为 NOTRUN、STARTED、PASS 或 INCOMPLETE；候选逐例区分 NOT_STARTED、RESERVED_EXECUTION_UNCONFIRMED 和 COMPLETED。预约前失败仍计零次候选启动，预约后无完整结果则记录未知而不补跑；只有完整136例和清理验证完成，才写 COMPLETED。事件使用独占文件，保留领取后写入失败的阻断状态。默认准备及 mock 测试不创建真实执行资格目录或真实批准文件。

本入口独立于 repair001，绑定 v4 协议、提案 digest `7b017a647137f3deb3adb6f5ee5f07cde9b58eba199cebfb69f256eb48bad360`、原批次31文件及四份完整候选 SHA256。旧授权不能启用本入口。新代码只添加文件，旧运行时、oracle、worker、沙箱及清理代码不改动。

默认 `python -B -m ark_eval.v4_eval --output results/v4-execution-preview-001` 只生成 scope、未批准模板与136条 NOTRUN；无容器或候选执行。源码只用于摘要，不在控制器编译或执行。既有公开 starter 的可信工程校验仍可运行。

当前未收到本批次执行批准，固定授权文件 `D:\GIR\results\v4-batch001-execution-approval.json` 不存在，执行门关闭。父级只读复审通过且用户明确同意后，可信控制器才可按模板创建该非秘密文件：decision=`APPROVE_EXACT_V4_136`，human_explicit_approval=true，human_statement 为用户原话；其余字段须精确匹配本次 scope、协议、提案及四摘要。候选、旧授权、仅命令行开关或未批准模板均不能作为批准。

未来批准后的命令为：

```cmd
C:\Windows\System32\wsl.exe -d Ubuntu-24.04 --cd /mnt/d/GIR --exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C /usr/bin/python3 -B -m ark_eval.v4_eval --run-approved-v4 --output results/v4-batch001-isolated-evaluation-001
```

此命令当前不得运行。本轮只做 mock 与可信回归。受测模型调用、密钥或 env 读取、重置卡、push 均为零。

固定顺序 Modify IR、Modify JS、Debug JS、Debug IR。各臂原例26＋边界8，共136；种子1/123已在预声明例中，不额外翻倍。Modify 采用现有 cost=2 oracle，Debug cost=1。保留原例/边界分类、seed、期望和观察、重放、工作进程及清理证据，不按候选改 oracle。IR每例含既有worker的一次重放；JS重放已有宿主记录，不再调用候选。

复用 NodeBackend/WorkerProcess 的 rootless Podman、network=none、只读根、无个人挂载、无宿主环境、固定digest镜像、0.25CPU、64MiB、swap0、32PID、10秒墙钟及进程树清理。IR适配仅替换可信worker的允许摘要配置，沿用内容摘要缓存和公开Python打包。JS使用相同宿主事务/故障协议；不放宽旧gate或其类型检查。

每臂先运行既有profile检查和可信故障预检：JS12类、IR3类；只接受同一活跃backend/image且600秒内的全PASS证据。超过550秒在下一例前刷新预检。两份JS在隔离parser中检查真实语法及唯一onOpen导出，无顶层或回调执行。每次候选准入重验全部代码、原始bundle与oracle摘要。每例发送前写不可覆盖预约；任何隔离/准入/清理失败停止后续并保留NOTRUN；普通oracle失败如实记FAIL，不修复、不重试。

输出目录必须不存在，证据用独占创建。最终另做只读容器库存检查，出现残留或未知即阻断，不广泛删除容器。完整合同仍独立为NOT_CHECKED，136例通过不能自动补足别名或所有源路径证据，也不构成AI优势。
