# Pilot24 同一 runner 的零预约失败续接

逻辑批次 key不变，只含原82个未预约ID。原193确定结果、未知C2/N02、12语法NOTRUN、原1156证据，以及失败恢复scope/receipt/claim/23份封存证据均保持。当前实现@2另绑源码/测试hash，引用原@1失败scope和Git版本，不把历史批准更新为新批准。

新的显式零预约续接入口要求旁置一次permit。先核实原失败终态、全部证据及准入/主预约/执行/未知计数严格为零；旧批准、普通布尔、改变预算或范围均拒绝。launch intent与单次claim按稳定逻辑key原子领取，跨输出目录只有一个赢家。共享case账先独占写入并fsync，再写输出预约，再执行。任何预约/部分持久化/未知不自动重跑，许可消耗后不释放，累计仍82。

执行复用原隔离、parser、HostRuntime、IR与oracle函数。原直接WSL上下文须rootless/v2/systemd及cpu/memory/pids全部可用；同一非systemd cgroup。独立child仅使用新session和清洁环境，不启用systemd服务、Delegate或更改机器设置。安全预检错误仅记录固定码/类型，不保存任意异常文本。SIGINT/SIGTERM停止调度并持久化。只读status以PID+启动时间识别child，并核对逐项预约/结果；未核实的执行不是0。

默认入口仅准备，旧执行入口已拒绝。新的permit不存在，本轮不写批准或执行候选。工程交付后的准确scope、命令和可信控制证据见results交付目录；实际使用只允许父级依据用户授权记录匹配的新permit后一次启动。

可信直接生命周期控制只使用固定代码：正常父进程退出、仅自有父helper的SIGHUP、仅自有child的SIGKILL。不操作用户连接或其他进程。实际exec-server清理整个进程树、WSL/主机关停未测试且不能保证存活；遇到这些情况只读证据，保留未知，不重启。

不API/凭证、购买、升级、转按量、重置卡、push、ABC建设或改变运行语义。
