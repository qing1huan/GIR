# v4 用户本地四会话入口

本入口绑定协议 `4ddc8d41cac2e8571c2048366c6dd1e5f23881d7e8f00fdaf27d16340e2e9709`，四份原始请求保持不变。按协议 `order` 发送：Modify IR → Modify JS → Debug JS → Debug IR；不按 `public_arms` 存储顺序发送。每次是独立的两条消息会话，不传上一会话答案、完整协议、私有测试、参考解或评测反馈。

同一模型 `deepseek-v4-1-flash-260910`，只用现有 Plan 的 `https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions`。每会话最多一次请求、输出上限 4096 tokens、thinking disabled、temperature 0，整个批次最多四次请求、输出上限合计 16384 tokens。输入也消耗实际 Plan 额度；24000 是输入 UTF-8 字节加消息开销的估算单位上限，**不是 token 上限，也不保证精确相同输入 token 数**。不填充输入。修复、工具、重试、备用路由、付费升级及重置卡均为零。

默认运行只准备文件，不要求密钥，不联网。输出目录必须不存在，任何旧目录均拒绝覆盖；默认用户目录留给用户首次操作。可在 CMD 离线预览到另一个新目录：

```cmd
cd /d D:\GIR
D:\python3.11\python.exe -B -X utf8 -m ark_eval.manual_door_v4 --output "D:\GIR\results\my-v4-preview-001"
```

仅用户在自己的私有交互 CMD 中运行以下命令。`--plan-status-confirmed` 表示用户已确认现有 Plan 允许脚本、额度够用、超额付费关闭。下面密钥路径是示例，替换为用户自己在项目及所有 Git 目录之外创建的私有文本文件路径；不要把密钥发到聊天。代理不得运行发送命令或读取该文件。

```cmd
cd /d D:\GIR
D:\python3.11\python.exe -B -X utf8 -m ark_eval.manual_door_v4 --user-local-send-v4 --plan-status-confirmed --key-file "C:\Users\10036\Ark-private\plan-key.txt" --output "D:\GIR\results\user-local-v4-modify-debug-001"
```

程序先核对协议、请求及入口文件摘要，在显示四请求范围后等待用户输入 `SEND_V4`。取消、缺少状态确认或非交互终端不会读密钥或发请求。密钥读取复用现有安全 helper，拒绝 `env.txt`、`.env`、项目/Git 路径、链接及格式异常文件；不扫描文件夹或读取环境变量。也可省略 `--key-file` 使用隐藏输入；隐藏输入不可用时停止，不能退回可见输入。

HTTP 只发一次，不跟随重定向或自动重试。每次发送前重新核对冻结摘要。任何失败（含配额/权限、超时、未知结果、模型不符、thinking 不符、截断、空答案、超限或摘要漂移）立即停止后续请求，后续会话保留 NOTRUN。未知结果保留预算预约；本入口没有续传或重试功能。进程意外中止时保留发送前预约及初始 NOTRUN 文件，不要自行重跑批次。

输出包括原始固定请求、发送前预约、审计与 hash chain、实际返回 token 用量（若可验证）、候选文本 SHA256 和汇总。不保存密钥、HTTP Authorization、完整响应或服务端错误文本；检测明文/JSON 转义密钥回显后拒绝保存。协议文件含其他会话元数据和私有 oracle 摘要，仅供本地审计，不能作为受测模型输入。

模型文本只保存为 `candidate.txt`，不解析、编译或执行。所有候选执行仍 BLOCKED，136 个 gameplay 条目（四臂各旧 26 + 诊断 8）全部 NOTRUN，contract 为 NOT_CHECKED。返回 200 或完成收集不能算玩法正确、合同通过、公平优势或独立 holdout 结果。后续静态审查和隔离许可由独立评审处理；本入口不打开原有执行门。

需要报告时只提供 `summary.json` 中状态、请求次数、实际用量和失败码，不提供密钥。该入口不使用重置卡、不公开推送、不修改旧答案、oracle 或冻结协议。
