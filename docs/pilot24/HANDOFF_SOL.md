# Sol离线材料实现交接

状态：`DESIGN_READY_FOR_MATERIALS`，本阶段设计交付。按[DESIGN.md](DESIGN.md)、[TASKS.md](TASKS.md)和[schedule.json](schedule.json)实施。以下目录与代码文件是**下一阶段拟新增清单**，不是本次已经实现的能力；只有这四份设计文件现在交付。先完成公共/私有材料和可信工程验证，再交父任务复审。当前没有可启动的24槽发送器、最终材料freeze或新候选执行许可。

## 1. 工作边界

- 继续使用当前GIR工程，不写新游戏框架或权限系统。暂不修改`headless_v01/*`、`ark_eval/host.py`、现有worker、隔离profile、v4控制器/发送器及其冻结依赖。
- 保留历史v4的四份候选、31文件原始收集包、74项执行绑定、114项历史基线、已消费scope、审批与最终收据。旧测试与新材料测试分开报告，不重命名旧数据为新题结果。
- 不读取`env.txt`、`.env`、密钥文件、凭据环境值；不收集、填写或传递密钥，不发模型/服务请求，不使用额度重置、不购买、不切按量接口、不push。
- 只执行经审查的手写可信控制与工程测试；不得把旧模型候选复制到新任务运行。原生JS控制仍须复用已核实的容器隔离，不在宿主直接eval；无法复用时标NOTRUN并提交复审，不能放宽隔离。
- 核心语义若与设计不符，报告具体冲突与最小数据/配置修改。任何worker或执行机制变化先返回架构复审，不能悄悄扩大本材料任务。

## 2. 文件清单与依赖顺序

下列`<task>`仅为M1、M2、C1、C2、R1、R2，路径是建议的稳定布局。保留文件的职责边界，命名调整在交接中列清。

| 拟新增位置 | 内容/产出要求 |
|---|---|
| `ark_eval/pilot24/public/common_semantics.md` | 共同事务、缺cell、别名、输入域、精确预算/输出要求。不得含测试表、私有控制或其他答案 |
| `ark_eval/pilot24/public/api_ir.md`、`api_js.md` | 分别基于现有API的最小语法说明；checked加法、EntityID值比较等事实配对；独立Probe例子具同一行为与信息 |
| `ark_eval/pilot24/public/<task>/requirements.md` | 从设计提取完整v0/v1公共契约，逐项标明保留/新增/替代规则及短路优先级；不写case ID或expected表 |
| `ark_eval/pilot24/public/<task>/abi.json` | 不含handler算法的固定Core声明头、唯一入口、字段与事件；两臂完全一致，输入业务域另显式列在公共契约 |
| `ark_eval/pilot24/public/<task>/starter.ir.json`、`starter.js` | 六对正确、清晰、原生v0 starter，共12份；彼此不包装/翻译，禁止TODO/预埋错误 |
| `ark_eval/pilot24/private/<task>/cases.json` | 每题12例，4N+4B+4R；固定初态、逐tick输入与完整expected，按契约独立手工展开，共72个场景定义 |
| `ark_eval/pilot24/private/<task>/v0_cases.json` | v0自己的私有契约检查，包括有效成功、拒绝、终态、缺必需cell、别名/输入分支等适用路径；不进入模型成绩 |
| `ark_eval/pilot24/private/<task>/control_v1.ir.json`、`control_v1.js` | 12份私有手写目标控制，只检验宿主与oracle可实现；不发送给受测模型 |
| `ark_eval/pilot24/private/<task>/mutants/` | 每题三个必需缺陷意图×两种表示，至少36个程序；单一故障、标明由哪一固定场景检出。额外控制单列 |
| `ark_eval/pilot24_materials.py` | 纯离线的材料校验/请求组装薄层；严格公共路径allowlist，不扫描整个仓库，不包含网络、凭据或生成候选执行入口 |
| `ark_eval/pilot24/protocol.draft.json` | 12份公共请求模板及24槽映射、对照/预算/失败规则、源码及运行时依赖版本；状态DRAFT，不能充当执行批准 |
| `ark_eval/tests/test_pilot24_materials.py` | 日程、完整性、长度、信息边界、oracle数据格式、公共材料漂移等纯离线测试 |
| `ark_eval/tests/test_pilot24_trusted_controls.py` | 公共v0、私有v1、成对故障的真实语义验证；沿用已核实隔离，工程输出与模型成绩分离 |
| `results/pilot24-materials-validation-<date>.json` | 实际命令/版本/文件摘要、各门PASS/FAIL/NOTRUN、可信执行证据、0模型调用、限制与待复审项 |
| `ark_eval/pilot24/freeze.json` | **材料与公平性复审通过后才生成**；绑定公共/私有输入、日程、预算、语义、测试与执行依赖，不能提前把DRAFT称为已冻结 |

先手写契约oracle与公共规则；随后独立编写两个starter及两个目标控制；再做故障控制和真实隔离验证；最后构建12份模型消息并检查逐项等信息与预算。两个repetition复用完全相同请求正文，但各有独立审计槽。不要直接从私有目标控制输出生成expected，也不能把私有文件混入公共提示上下文。

## 3. 冻结检查和有意义的测试

| 门 | PASS所需证据；不满足时的处理 |
|---|---|
| 六题覆盖 | 六个不同算法契约；每题12个唯一场景ID、4N/4B/4R；全部输入/expected人工可读。场景定义不是已通过测试数 |
| 完整oracle | 给出所有实体/字段和逐tickstatus/error/events/commands；未变化字段也显式保留；缺cell与false/0区分；同tick全回滚/跨tick提交按设计写明 |
| v0质量 | 两个starter在各自旧版契约集合全过，且R01–R04与v1 oracle相同；不要求v0通过新规则N/B；人工检查两臂可读性和辅助信息对称 |
| v1可实现 | 每题两种可信目标控制全部12例通过，复用同一宿主/oracle，重放一致；JS有真实隔离运行证据。Python模拟通过不能替代JS执行 |
| 故障检出 | 每题三种成对缺陷分别被预定N/B/R中指定场景检出，不能只靠源码匹配；检出原因一致。显示检测FAIL是控制预期结果，不能混成工程失败 |
| 信息隔离 | 构建器只读公共allowlist；私有路径/参考正文/case表/对侧starter无法进入消息；测试对已知泄漏夹具拒绝。哈希未命中不等于证明无泄漏，仍需人工成对审阅 |
| ABI及域 | IR完整声明头与JS宿主头逐字绑定；同一Open payload、字段范围与输出事件；C2 amount=0..4、R2 steps=0..3的业务域公开；域外不纳入主分数 |
| 请求与预算 | 12个不同任务/表示正文，每个复制到两个独立槽；两消息、新会话、指定Plan模型与参数；每槽attempt=1、repair/tool/retry=0；公共估算≤24000，源码≤16384 bytes |
| 4096可行性 | 列出12份starter和12份目标控制的bytes、IR节点数、离线token估计来源/可信度；IR目标优先≤100节点/10000 bytes、估计≤3072 tokens。无兼容tokenizer则明确NOT_VERIFIED；风险过高先停题复审，不调高上限 |
| 运行余量 | 可信控制最长路径的host/wire计数、bootstrap/response帧大小和容器资源实测。已有硬界必须遵守；不以IR节点数等同JS工作量，不把建议余量写成已测结果 |
| 调度平衡 | 24个唯一槽，每题/表示恰好2次；每轮3题IR先、3题JS先；第二轮任务反序、每题臂序反转；没有从结果选择后续槽或额外请求 |
| 质量/环境分类 | 用mock或可信故障样本确认：确定的语法/ABI/空答/截断与基础设施异常分开；不会把usage/identity不明或worker崩溃当普通模型FAIL继续；不改现有旧发送器 |
| 历史不变 | 对旧v4 freeze、收集包、历史基线与执行收据做只读摘要核对；旧scope消费状态保留。不得为了测试新配置重写旧控制或旧freeze |

主case的三类结果须同时保留：实际PASS、实际FAIL、未执行NOTRUN；生成级有自己的PASS/FAIL分类。比如明确语法拒绝为一次生成FAIL，但12个case为NOTRUN；不是12次失败执行。Infra错误停批不是模型能力证据。逐case结果不冒充独立样本。

完整有效批次才计算`p[t,a]`、六题均值与对称差值门；另列24个槽的原始失败/截断与用量。不制作隐藏的“最佳一次”主分数，不为缩小case数量重调指标。固定用例未覆盖所有合法输入，任何全部通过均称“固定场景通过”，完整契约保持NOT_CHECKED。

## 4. 后续复审与交付要求

Sol完成后提供可复核本地diff、实际命令、Python/Node/Podman版本、工程测试PASS/FAIL/NOTRUN、所有材料路径/摘要，以及公平性逐项表。优先跑新增可信测试和既有必要回归；旧319工程检查的通过事实只作历史基线，不能直接继承为本次PASS。当前设计阶段没有运行新语义测试，因此不存在新模型分数。

父任务复审材料、预算风险、成对starter质量和oracle后，才能批准生成材料freeze并准备统一用户本地调用入口。新的薄配置若需要worker/容器装载路径或scope适配，先提交精确diff和安全测试计划；复用现有一次性机制，不另建通用权限系统。本设计不把未来执行适配实现标为已完成。

用户已批准最多24次的规模，不需要再次询问同一个规模问题；这不替代私有终端中的用户凭据动作，也不授权执行尚不存在的新候选。发送收集与候选执行各自保留精确摘要绑定和当前既有批准流程。只在材料可复核后提出真正缺少的具体动作；不提前索取密钥，不提交秘密到聊天、日志、Git或文件。

没有实际模型输出之前，不得声称六题已证明IR优势、Plan当前一定可用、4096已经足够或存在公平Vitric旧基线。该批次的对照是同一宿主上的原生JS callback，而非缺失的Vitric公开实现。
