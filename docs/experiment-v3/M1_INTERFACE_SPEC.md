# M1：单任务语义接口可行性规格

状态：`STATIC_SPEC_ONLY`。本轮不实现工具框架、不运行模型候选、不调用模型。后续离线实现的最大范围是一个 handler 上的查询与一次事务编辑；不能把它扩成完整编辑器。M1 是已观察开发夹具，不是新任务样本。

输入固定为 `ark_eval/pilot24/public/M1/{requirements.md,abi.json,starter.js,starter.ir.json}` 和公共语义/API 文档。其 SHA256 在 [静态清单](pilot24-static-manifest.json) 的 `material_bindings` 中。A/B 读取同一 starter.js；C 读取语义等价 starter.ir.json。私有控制与 oracle 只留在独立测试端，不能进入 Agent 工具上下文。

M1 IR 是 `handlers` Core JSON，现有 SemanticEditor 使用 `nodes/connections` 语义文档。此规格复用查询/事务设计，不假设类可以直接接收 Core。下一薄适配仅定位 Core handler 内的路径并保持原序列化/语义；不自动转换24槽候选，不增加新表示层。

## 操作边界

接口仅对公开工作副本生效。所有请求含 `document_id`、`expected_revision_sha256`、操作和参数；回复含 `observed_revision_sha256`、`status`、带来源位置的事实、精度、工具成本。无任意路径参数，无 shell、网络、动态代码执行或包安装。节点选择器只对当前修订有效；节点存在歧义返回拒绝，不“猜”目标。

| 功能 | A 的标准接口 | B 的语义包装 | C 的对应接口 |
|---|---|---|---|
| 读取/定位 | read_range、symbols、definition/references、AST | query(kind/predicate)、inspect(selector) | query_nodes、inspect(node/path) |
| 影响 | 同一 AST 分析后端提供读/写/事件及 conservative/unknown 摘要 | 相同事实按语义选择器呈现 | 复用依赖查询并补相同读/写/事件分类，不能隐藏未知 |
| 局部更改 | revision guarded 文本补丁或语言服务重构 | replace_expression / replace_block，传入普通 JS 表达式/语句 | 对应 IR 表达式/块替换；不自动生成算法 |
| 提交 | preview、公共诊断、commit、undo | 同一事务流程 + 选择器/前置条件 | draft / validate_patch / commit_patch / rollback 的匹配封装 |

所有组都享有原子提交、修订冲突拒绝和撤销；不能故意让 A 部分写入而 B/C 自动回滚。B 的差异在语义定位和组合编辑。`query_reads_writes` 不证明路径可达性，返回 `conservative`；未知动态字段返回 `unknown`，不输出伪精确依赖。

## 下一工程阶段的固定夹具序列

1. 三组分别查询入口、coins/tokens/sold 的读写点、事件发射点与直接引用；按公共源代码人工登记同一事实表。A/B 的底层事实集合必须一致，C 的差异必须能用语义等价解释。
2. 选择一个数值常量位置，进行人为给定的普通表达式替换；这是编辑器机械夹具，不是 M1 的目标解。A 文本范围、B AST 选择器、C 节点路径均绑定原修订。预览显示相同意图且不改变 ABI。
3. 用旧修订再次提交，三组都拒绝；重复/歧义选择器拒绝。拒绝后工作副本哈希不变。
4. 以错误类型的表达式构造一次无效编辑。记录各语言真实诊断，按共同错误类别呈现；不得让 C 获得人工解释而 A/B 只看失败码。未通过准入不能激活。
5. 成功编辑仅激活工作副本；撤销后字节哈希恢复。公共测试运行只能使用已验证隔离端；本阶段不能运行从24槽复制的任何代码。
6. 拦截越界 document、私有测试/参考解路径、跨修订选择器、输出过长和工具预算耗尽。工具日志保留操作和成本，不接收凭证字段。

验收是上述六类工程检查全部 PASS、模型请求 0、生成候选执行 0；任何一项失败停止接入。不能用“工具返回了 JSON”代替事实一致和事务不变性的验证。文件/节点差异、诊断差异、耗时和实现工时均报告；此结果不能解释为模型效果。

现有 `SemanticEditor` 的构造/验证会编译或运行程序，因此未来 C 的不可信输入必须放进已有隔离边界；不能直接在宿主实例化它处理模型输出。A/B 的语言服务只在受控公开虚拟文件集合中解析，不加载项目外文件。下一工程阶段若无法做到这些边界，报告 BLOCKED，不扩大框架。
