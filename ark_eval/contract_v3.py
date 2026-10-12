"""Review-only post-observation contract revision. No transport or activation path.

Does not replace create_pair.py or its v1/v2 frozen/imported contracts. Public
examples are evaluator-authored, independent score/Probe fragments, never door
reference code. Runtime and compiler stay unchanged.
"""
import ast
import copy
import json
from pathlib import Path
import re

from headless_v01.compiler import digest
from headless_v01.types import canonical_json
from .adapter import EvaluationError, PlanConfig, prepare
from .create_pair import ROOT, ARMS, TASK, OUTPUT_TOKENS, PROMPT_UNITS, THINKING, load_freeze
from .tasks import task
from .contract_assessment import assessment_policy, make_assessment

FREEZE = Path(__file__).with_name('create_pair.v3_1.review.freeze.json')
PREVIOUS_REVIEW = Path(__file__).with_name('create_pair.v3.review.freeze.json')
PREVIOUS_REVIEW_DIGEST = '248cdee54ea4f0e753b08d0c73ab14808d36c46da1e5a710541355b335d1ad0c'

# Task ABI subset, not a claim that the engine exposes ONLY these operations.
# Public IR text and JS text derive from the same meaning/type row.
API = (
    ('literal', 'expr', ('type','value'), '指定 bool/int/EntityID 值；int 是 safe-int，bool 不是 int。',
     'literal:{type,value} -> type', '类型正确的普通字面值；EntityID 使用给定事件参数',
     ('headless_v01/compiler.py:210','headless_v01/types.py:81')),
    ('var', 'expr', ('name',), '读取已绑定的局部变量；未声明变量无效。',
     'var:{name} -> 绑定类型', '读取已声明的普通局部变量',
     ('headless_v01/compiler.py:212',)),
    ('resolve', 'expr', ('entity',), 'EntityID -> option<EntityID>；存在是 some，缺失是 none；不改变状态。',
     'resolve:{entity} -> option<EntityID>', 'ctx.resolve(entity) -> {some:EntityID}|{none:true}',
     ('headless_v01/compiler.py:215','headless_v01/runtime.py:366','ark_eval/node_worker.mjs:29')),
    ('is_some', 'expr', ('value',), '检查 option 是否存在，结果为 bool；检查不等于解包。',
     'is_some:{value:option<T>} -> bool', "'some' in option -> bool；ctx.exists(entity) 是 is_some(resolve(entity)) 的便利调用",
     ('headless_v01/compiler.py:223','headless_v01/runtime.py:445','ark_eval/node_worker.mjs:30')),
    ('read', 'expr', ('entity','field'), 'EntityID × 已声明字段 -> option<字段类型>；实体或该 cell 缺失返回 none。',
     'read:{entity:EntityID,field} -> option<T>', 'ctx.read(entity,field) -> {some:T}|{none:true}',
     ('headless_v01/compiler.py:215','headless_v01/runtime.py:371','ark_eval/host.py:35')),
    ('require', 'expr', ('value',), 'option<T> -> T；some 解包，none 抛 InvalidEntity；与真假判断无关。',
     'require:{value:option<T>} -> T', 'ctx.require(option) -> T；none 抛 InvalidEntity',
     ('headless_v01/compiler.py:223','headless_v01/runtime.py:441','ark_eval/host.py:51')),
    ('not', 'expr', ('value',), '逻辑否定：IR 输入须为 bool；JS 原生 truthiness 可得到 bool。option 对象真假不能表示 some/none。',
     'not:{value:bool} -> bool', '!value；保留原生 JS 语义，允许 int truthiness',
     ('headless_v01/compiler.py:229','headless_v01/runtime.py:449')),
    ('and', 'expr', ('a','b'), '从左到右短路：IR bool×bool->bool，左侧 false 时跳过右侧；JS && 按 truthiness 短路并返回操作数。',
     'and:{a:bool,b:bool} -> bool', 'a && b；保留原生 JS truthiness，不强制两个操作数为 bool',
     ('headless_v01/compiler.py:247','headless_v01/runtime.py:451')),
    ('ge', 'expr', ('a','b'), '本任务 int × int -> bool，比较 a >= b。',
     'ge:{a:int,b:int} -> bool', 'a >= b；两侧为 int',
     ('headless_v01/compiler.py:236',)),
    ('le', 'expr', ('a','b'), '本任务 int × int -> bool，比较 a <= b。',
     'le:{a:int,b:int} -> bool', 'a <= b；两侧为 int',
     ('headless_v01/compiler.py:236',)),
    ('sub', 'expr', ('a','b'), '本任务 int × int -> int；safe-int 减法，越界 NumericOverflow。',
     'sub:{a:int,b:int} -> int', 'ctx.sub(a,b) -> int；使用宿主 checked 减法而不是普通 JS 减法',
     ('headless_v01/compiler.py:236','headless_v01/runtime.py:381','headless_v01/types.py:91','ark_eval/node_worker.mjs:33')),
    ('record', 'expr', ('items',), '构造字段到表达式值的 record；事件 payload 必须匹配声明的字段与类型。',
     'record:{items:{字段:表达式}} -> record<字段类型>', '普通对象字面量 {字段:值} -> 事件 payload record',
     ('headless_v01/compiler.py:271','headless_v01/runtime.py:134','ark_eval/host.py:61')),
    ('let', 'stmt', ('name','value'), '绑定类型明确的局部变量；新分支内的名字不在分支外可见。',
     'let:{name,value}；同名重新绑定须保持类型', '普通 const/let 局部绑定；不得保留可变全局状态',
     ('headless_v01/compiler.py:315','headless_v01/compiler.py:327','headless_v01/runtime.py:594')),
    ('if', 'stmt', ('condition','then','else'), '只执行选中分支，语句按源代码顺序执行；IR condition 须为 bool；JS 条件保留原生 truthiness。',
     'if:{condition:bool,then:[语句],else:[语句]}；else 可省略，等同 []', '普通 if/else；允许原生条件语义，包括 int truthiness',
     ('headless_v01/compiler.py:325','headless_v01/runtime.py:543')),
    ('write', 'stmt', ('entity','field','value'), 'EntityID × 已声明 IR 字段 × 该字段类型；写自己的 overlay 并记录 write 意图。',
     'write:{entity:EntityID,field,value:T}', 'ctx.write(entity,field,value)',
     ('headless_v01/compiler.py:319','headless_v01/runtime.py:385','ark_eval/host.py:37')),
    ('emit', 'stmt', ('event','payload'), '已声明且非保留事件 × 匹配类型的 record；不能主动发出 Tick 或 FieldChanged: 前缀事件。记录意图，成功事件合并后才进入队列。',
     'emit:{event,payload:record表达式}', 'ctx.emit(event,payload)',
     ('headless_v01/compiler.py:343','headless_v01/runtime.py:403','headless_v01/runtime.py:325','ark_eval/host.py:61')),
)

# Common semantic statements are sent verbatim to BOTH arms. Source pointers
# remain evaluator-only metadata, not copied source code in model prompts.
FACTS = (
    ('C01', '范围', '本次任务级 API 是下面列出的闭合集；引擎其它操作不在本试验范围。不创建服务、扩展、计时器、网络、文件、导入或工具；不得修改共同字段/事件 ABI。',
     ('ark_eval/create_pair.py:50','ark_eval/tasks.py:17','ark_eval/tasks.py:32')),
    ('C02', '类型和 option', 'EntityID 是 [world_epoch,tick,job,ordinal]。option<T> 表示 some(T) 或 none；option 不是 T。存在性检查不会解包；read/write 必须拿 EntityID，数值操作必须拿 int；some(false) 和 some(0) 都是存在值。',
     ('headless_v01/compiler.py:215','headless_v01/compiler.py:223','headless_v01/types.py:81','headless_v01/runtime.py:366')),
    ('C03', '控制流', '业务说明的“返回”表示结束当前 handler、没有后续调用或事件。IR 不存在 return op：将剩余处理放进满足条件的 if 分支，空分支无操作；handler 语句列表结束即正常结束。JS 允许不带值的 return 或自然到达函数末尾；不得返回值或 Promise。两种结束均不撤销已执行的 write/emit 意图，错误才触发回滚。',
     ('headless_v01/compiler.py:309','headless_v01/runtime.py:543','ark_eval/node_worker.mjs:51','headless_v01/runtime.py:152')),
    ('C04', '顺序和求值', 'and/&& 从左到右短路，if 只执行选中分支；IR 条件须为 bool，JS 保留原生 truthiness。硬要求是错误/效果的因果时机：不得因提前 require 或业务写入，让本应正常结束的缺实体/已开门路径抛错或产生效果。预算内冗余但无害的 resolve/read 仅记录成本，不因调用形式或次数不同判玩法失败；推荐调用顺序不是逐项相同轨迹的硬要求。',
     ('headless_v01/runtime.py:366','headless_v01/runtime.py:371','headless_v01/runtime.py:441','headless_v01/runtime.py:451','headless_v01/runtime.py:543','ark_eval/node_worker.mjs:28','ark_eval/oracle.py:19')),
    ('C05', '错误', 'resolve/read 的 none 本身不是错误；require(none) 当场抛 InvalidEntity，不表示 false/0。write 不存在的实体抛 InvalidEntity；字段值类型错误为 TypeError，safe-int 越界为 NumericOverflow，超范围写为 InvariantViolation。无效的语法/类型可在 IR 编译时拒绝；JS 语法或普通 JS 异常由回调故障处理，不宣称两组拒绝阶段相同。不得捕获或吞掉宿主错误。',
     ('headless_v01/runtime.py:366','headless_v01/runtime.py:385','headless_v01/runtime.py:441','headless_v01/runtime.py:63','headless_v01/types.py:91','ark_eval/host.py:51','ark_eval/node_worker.mjs:55')),
    ('C06', '状态和副作用', '每事件各 handler 从同一快照读取并叠加自身 write；其它 handler 的写入在事件合并前不可见。write 当场检查值与范围、更新自身 overlay 并记录意图；emit 当场校验 payload 并记录意图。事件完成后合并意图，再调度发出的事件；exclusive 字段由不同 handler 写入发生冲突。任何错误/冲突回滚整个 tick 的状态及已排队意图，错误尝试仍记录审计。',
     ('headless_v01/runtime.py:256','headless_v01/runtime.py:385','headless_v01/runtime.py:403','headless_v01/runtime.py:293','headless_v01/runtime.py:152','ark_eval/host.py:61')),
    ('C07', '预算和计量', '共同宿主上限 128 次/tick；resolve、read、checked sub、write、emit 计入该上限，require/存在性判断不是额外宿主操作。JS 的 ctx 调用另有 32 请求/handler 与 256 请求/tick 的协议限制（包含 done；exists 调用一次 resolve）；IR AST 成本与 JS 计算/协议成本分别报告，不宣称两者完全相等。',
     ('headless_v01/runtime.py:360','headless_v01/runtime.py:381','ark_eval/host.py:80','ark_eval/callback_contract.py:12','ark_eval/node_worker.mjs:30')),
    ('C08', '输入绑定与保留事件', 'IR handler 启动时，事件 payload 字段自动成为同名初始变量；Open 的 player、door 均为 EntityID，可用 var 直接引用，无须先 let。JS 的同名输入来自 onOpen(ctx,{player,door}) 参数。Probe 示例的 target 也来自示例 payload。两组都不能主动 emit Tick 或 FieldChanged: 前缀保留事件。',
     ('headless_v01/compiler.py:365','headless_v01/runtime.py:140','headless_v01/runtime.py:134','ark_eval/node_worker.mjs:52')),
)

def node(op, **values):
    return dict(op=op, **values)

def independent_examples():
    target=node('var',name='target')
    cell=node('var',name='cell')
    read=node('read',entity=target,field='score')
    return [
        {'id':'P01','kind':'POSITIVE_FRAGMENT','meaning':'Probe:{target:EntityID}，score:int/IR/exclusive。仅查看可选 cell；不存在时空分支，存在时得到 int；不写状态、不发事件。',
         'IR':[node('let',name='cell',value=read),node('if',condition=node('is_some',value=cell),then=[node('let',name='value',value=node('require',value=cell))],**{'else':[]})],
         'JS':"const cell = ctx.read(target, 'score');\nif ('some' in cell) { const value = ctx.require(cell); }",
         'expectation':'Existing cell -> int local value; missing cell -> normal finish without require.'},
        {'id':'P02','kind':'POSITIVE_FRAGMENT','meaning':'明确需要实体时，先将 resolve 的 option 解包成 EntityID；不存在时必须抛 InvalidEntity，而不是跳过。',
         'IR':node('require',value=node('resolve',entity=target)),
         'JS':'ctx.require(ctx.resolve(target))', 'expectation':'EntityID or InvalidEntity; no writes.'},
        {'id':'P03','kind':'POSITIVE_FRAGMENT','meaning':'false 左侧使右侧 read 完全不发生。',
         'IR':node('and',a=node('literal',type='bool',value=False),b=node('is_some',value=read)),
         'JS':"false && ('some' in ctx.read(target, 'score'))",'expectation':'false; zero read operations.'},
        {'id':'N01','kind':'NEGATIVE_FRAGMENT','meaning':'把 resolve 的 option 直接当 EntityID 传给 read 无效；须先检查/解包或使用类型正确的原 EntityID。',
         'IR':node('read',entity=node('resolve',entity=target),field='score'),
         'JS':"ctx.read(ctx.resolve(target), 'score')", 'expectation':'IR TypeError at compile; JS TypeError at ctx entity validation.'},
        {'id':'N02','kind':'NEGATIVE_FRAGMENT','meaning':'option 对象不能表示是否存在：JS 的 some/none 对象均为 truthy，用对象真假判断存在性会产生逻辑错误；这不是 JS 语法禁令，int truthiness 仍允许。IR option 不能作为 bool 条件。',
         'IR':node('if',condition=read,then=[],**{'else':[]}),
         'JS':"if (ctx.read(target, 'score')) { /* wrong option truthiness */ }",
         'expectation':'IR TypeError at compile; JS syntactically valid but logically wrong, not a promised runtime TypeError.'},
        {'id':'N03','kind':'NEGATIVE_FRAGMENT','meaning':'缺失 cell 时 require(read(...)) 抛 InvalidEntity；不是返回默认 0，也不是正常结束。',
         'IR':node('require',value=read), 'JS':"ctx.require(ctx.read(target, 'score'))",
         'expectation':'Missing score -> InvalidEntity; whole tick rollback.'},
        {'id':'N04','kind':'NEGATIVE_FRAGMENT','meaning':'IR 没有 return 语句；JS 回调不允许返回非 undefined 值。正常提前结束方式见 C03，两种语法并不相同。',
         'IR':node('return'), 'JS':'return 7;',
         'expectation':'IR unsupported op -> parse rejection; JS non-undefined return -> callback JS_EXCEPTION.'},
    ]

def source_manifest():
    points={p for row in API for p in row[6]}|{p for row in FACTS for p in row[3]}
    result={}
    for point in sorted(points):
        name,line=point.rsplit(':',1)
        text=(ROOT/name).read_text(encoding='utf-8').splitlines()
        number=int(line)
        if not 1<=number<=len(text):raise EvaluationError('V3_SOURCE_ANCHOR_INVALID')
        result[point]={'file':name,'line':number,'source_line':text[number-1],
                       'file_digest':digest((ROOT/name).read_text(encoding='utf-8'))}
    return result

def compiler_operation_shapes():
    """Static inspect actual closed compiler tables, without executing AST code."""
    tree=ast.parse((ROOT/'headless_v01/compiler.py').read_text(encoding='utf-8'))
    result={}
    for fun in ast.walk(tree):
        if isinstance(fun,ast.FunctionDef) and fun.name in ('expr','statements'):
            phase='expr' if fun.name=='expr' else 'stmt'
            shapes={}
            for statement in fun.body:
                # statements() wraps its closed table in the body iteration.
                children=[statement]+(statement.body if isinstance(statement,ast.For) else [])
                for child in children:
                    if isinstance(child,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='keys' for t in child.targets):
                        shapes.update(ast.literal_eval(child.value))
                    if isinstance(child,ast.For) and isinstance(child.iter,ast.List):
                        for inside in child.body:
                            if (isinstance(inside,ast.Assign) and isinstance(inside.targets[0],ast.Subscript)
                                    and isinstance(inside.targets[0].value,ast.Name) and inside.targets[0].value.id=='keys'):
                                for op in ast.literal_eval(child.iter):shapes[op]=ast.literal_eval(inside.value)
            result[phase]=shapes
    return result

def check_consistency():
    from .host import ARGS
    shapes=compiler_operation_shapes()
    for op,phase,args,*_ in API:
        if shapes.get(phase,{}).get(op)!=set(args):raise EvaluationError('V3_COMPILER_SHAPE_MISMATCH_'+op)
    worker=(ROOT/'ark_eval/node_worker.mjs').read_text(encoding='utf-8')
    mappings={
        'resolve':('resolve',{'entity'},"resolve:entity => request('call', {op:'resolve', args:{entity}})"),
        'exists':('resolve',{'entity'},"exists:entity => 'some' in request('call', {op:'resolve', args:{entity}})"),
        'read':('read',{'entity','field'},"read:(entity,field) => request('call', {op:'read', args:{entity,field}})"),
        'require':('require',{'option'},"require:option => request('call', {op:'require', args:{option}})"),
        'sub':('int_sub',{'a','b'},"sub:(a,b) => request('call', {op:'int_sub', args:{a,b}})"),
        'write':('write',{'entity','field','value'},"write:(entity,field,value) => request('call', {op:'write', args:{entity,field,value}})"),
        'emit':('emit',{'event','payload'},"emit:(event,payload) => request('call', {op:'emit', args:{event,payload}})"),
    }
    for public,(host,args,fragment) in mappings.items():
        if ARGS.get(host)!=args or fragment not in worker:raise EvaluationError('V3_CTX_SHAPE_MISMATCH_'+public)
    documented={method for row in API for method in re.findall(r'ctx\.(\w+)',row[5])}
    if documented!=set(mappings):raise EvaluationError('V3_CTX_DOCUMENTED_METHOD_MISMATCH')
    signatures={'resolve':'ctx.resolve(entity)','read':'ctx.read(entity,field)',
                'require':'ctx.require(option)','sub':'ctx.sub(a,b)',
                'write':'ctx.write(entity,field,value)','emit':'ctx.emit(event,payload)'}
    descriptions={row[0]:row[5] for row in API}
    for op,signature in signatures.items():
        if signature not in descriptions[op]:raise EvaluationError('V3_CTX_DOCUMENTED_SIGNATURE_MISMATCH_'+op)
    if 'return' in shapes['stmt']:raise EvaluationError('V3_RETURN_SEMANTICS_CHANGED')
    return {'IR_documented_operations':len(API),'JS_documented_ctx_methods':len(mappings),
            'IR_compiler_shape_check':'PASS','JS_ctx_wire_shape_check':'PASS',
            'semantic_checks':'SEE_EVALUATOR_AUTHORED_UNIT_TESTS_NOT_MODEL_EXECUTION'}

def public_arms():
    old=load_freeze()
    common=old['public_arms'][0]['messages'][0]['content']
    common=common.replace('按顺序：resolve(player)，仅当存在才 resolve(door)；任一不存在则返回且无事件。',
        '确认 player 与 door 都存在；任一不存在则正常结束且无事件。resolve 查询本身不会因实体缺失抛错。')
    common=common.replace('双方存在后 require(read(door,open))；已开门则返回，不读取玩家 keys。',
        '双方存在后取得 door.open；已开门则正常结束。不得提前 require 玩家 keys，让已开门但缺 keys 的正常路径抛错。')
    common=common.replace('keys >= cost 时按顺序 write(player,keys,sub(keys,cost))、write(door,open,true)、emit Opened(door)。',
        'keys >= cost 时扣 cost、打开门并发出一次 Opened(door)。推荐依次 write(player,keys,sub(keys,cost))、write(door,open,true)、emit Opened(door)；不要求逐项相同的调用轨迹，最终状态、事件与错误语义必须满足要求。')
    common+='\n契约澄清（两组逐字相同，以下类型/时机适用于上述任务）：\n'
    common+='\n'.join(f'[{code} {title}] {text}' for code,title,text,_ in FACTS)
    examples=independent_examples()
    starters={a['representation']:a['starter'] for a in old['public_arms']}
    arms=[]
    for rep in ARMS:
        is_ir=rep=='gameir_text'
        lines=[task(TASK)['requirement'], '\n本试验任务级 API（闭合；每行给出类型与行为）：']
        for op,phase,args,meaning,ir,js,_ in API:
            lines.append(f'[{op}] {ir if is_ir else js}；{meaning}')
        if is_ir:
            lines.append('只输出完整 core-v0.1 JSON；每 AST 节点有 op，参数只能是该 op 列出的键。添加 handler id=open,event=Open,priority=0,body=[语句]；保留共同字段/事件 ABI。player 与 door 是事件 payload 自动绑定的初始变量。不使用 return 或未列出的 op。')
        else:
            lines.append('只输出 export function onOpen(ctx, {player, door}) {...}。只用上述 ctx.resolve/exists/read/require/sub/write/emit 与普通局部变量、对象、原生 JS 条件/比较；允许 int truthiness。不导入依赖、catch 宿主错误、保存可变全局变量或返回值；接口同步，不创建异步任务。')
        lines.append('两组都只输出本表示，不加 Markdown、解释、测试、自定义服务/扩展。\n独立最小正反例（Probe/score 与本任务无关；仅为片段，不能作为开门实现）：')
        for example in examples:
            fragment=canonical_json(example['IR']) if is_ir else example['JS']
            lines.append(f'[{example["id"]} {example["kind"]}] {example["meaning"]}\n{fragment}')
        lines.append('起始程序（与 v2 完全相同；保持各自原有输出格式，无业务实现）：\n'+starters[rep])
        messages=[{'role':'system','content':common},{'role':'user','content':'\n'.join(lines)}]
        request=prepare(messages,OUTPUT_TOKENS,PlanConfig(model=old['model']),thinking=THINKING)
        if request.prompt_units>PROMPT_UNITS:raise EvaluationError('V3_PROMPT_BUDGET_EXCEEDED')
        arms.append({'representation':rep,'messages':messages,'starter':starters[rep],
                     'request_body':request.body,'request_digest':request.request_digest,
                     'prompt_units_estimate':request.prompt_units})
    return arms

def current_review():
    load_previous_review()
    old=load_freeze();arms=public_arms();contract=copy.deepcopy(old)
    contract.update(schema='gir-create-pair-review-freeze@3.1',
        scope='POST_OBSERVATION_DEVELOPMENT_CREATE_CONTRACT_REVIEW_NOT_HOLDOUT',
        previous_protocol_digest=digest(old),supersedes_review_digest=PREVIOUS_REVIEW_DIGEST,
        review_status='AWAITING_PARENT_FAIRNESS_REVIEW',
        send_enabled=False,activation_path='NONE_EXISTING_MANUAL_PAIR_REJECTS_V3',
        revision_provenance={'observed_development_pair':'pair002',
            'reason':'Clarify contract after one unsupported-return submission; not independent holdout evidence.',
            'original_candidates_changed':False,'original_scores_changed':False,
            'compiler_or_runtime_changed':False,'new_model_calls':0},
        public_arms=arms,request_digests={a['representation']:a['request_digest'] for a in arms},
        starter_digests={a['representation']:digest(a['starter']) for a in arms},
        common_information_digest=digest(arms[0]['messages'][0]['content']),
        api_rows=[{'op':r[0],'phase':r[1],'args':list(r[2]),'meaning':r[3],'IR':r[4],'JS':r[5],'sources':list(r[6])} for r in API],
        semantic_facts=[{'id':r[0],'title':r[1],'text_both_arms':r[2],'sources':list(r[3])} for r in FACTS],
        independent_examples=independent_examples(),source_manifest=source_manifest(),
        consistency=check_consistency(),fairness_status='STRUCTURAL_PARITY_CHECKED_CAUSAL_FAIRNESS_PENDING_PARENT_REVIEW',
        generated_candidates='NONE_V3_1_NOT_SENT', assessment_policy=assessment_policy(),
        starter_policy='EXACT_V2_STARTERS_AND_OUTPUT_FORMATS',
        thinking_support=copy.deepcopy(old['thinking_support']))
    contract['artifact_hashes']['ark_eval/contract_v3.py']=digest(Path(__file__).read_text(encoding='utf-8'))
    contract['artifact_hashes']['ark_eval/tests/test_contract_v3.py']=digest((ROOT/'ark_eval/tests/test_contract_v3.py').read_text(encoding='utf-8'))
    for name in ('ark_eval/contract_assessment.py','ark_eval/tests/test_contract_assessment.py','ark_eval/create_pair.v3.review.freeze.json'):
        contract['artifact_hashes'][name]=digest((ROOT/name).read_text(encoding='utf-8'))
    contract['artifact_hashes']['ark_eval/create_pair.freeze.json']=digest((ROOT/'ark_eval/create_pair.freeze.json').read_text(encoding='utf-8'))
    return contract

def load_review(path=FREEZE):
    path=Path(path)
    if not path.is_file() or path.stat().st_size>1048576:raise EvaluationError('V3_REVIEW_FREEZE_MISSING')
    value=json.loads(path.read_text(encoding='utf-8'))
    if value!=current_review():raise EvaluationError('V3_REVIEW_CONTRACT_DRIFT')
    return value

def load_previous_review():
    value=json.loads(PREVIOUS_REVIEW.read_text(encoding='utf-8'))
    if digest(value)!=PREVIOUS_REVIEW_DIGEST:raise EvaluationError('V3_HISTORICAL_REVIEW_DRIFT')
    return value

def review_diff(contract):
    old=load_freeze()
    unchanged=('task_id','model','endpoint','order','independent_sessions','budget_per_arm',
               'maximum_pair_output_tokens','maximum_pair_attempts','HTTPS_socket_timeout_seconds_per_arm',
               'source_bytes_limit','host_operations_per_tick','oracle_case_count_per_arm','oracle',
               'oracle_holdout','seeds','repair_budget','feedback_to_model','thinking_parameters','runtime_semantics')
    if any(contract[k]!=old[k] for k in unchanged):raise EvaluationError('V3_NON_DOCUMENTARY_CHANGE')
    return {'kind':'POST_OBSERVATION_CONTRACT_CLARIFICATION_V2_STARTERS_RESTORED',
            'parent_v2_digest':digest(old),'review_v3_digest':digest(contract),'unchanged_fields':list(unchanged),
            'changed':['Explicit closed task subset and typed operation signatures',
                       'Symmetric option, normal termination, ordering, fault and transaction explanations',
                       'Seven equivalent independent positive/negative fragments per arm',
                       'Payload initial bindings and reserved emit restrictions restored/clarified',
                       'Exact v2 starters restored; native JS int truthiness retained',
                       'Separate gameplay/contract reporting schema, no checker or transport activation'],
            'superseded_review_digest':PREVIOUS_REVIEW_DIGEST,
            'starter_equality_to_v2':{a['representation']:a['starter']==b['starter'] for a,b in zip(contract['public_arms'],old['public_arms'])},
            'policy_clarification':'IR closed task API is explicit; JS native conditions remain allowed. Only causal error/effect ordering is hard; redundant safe queries are cost diagnostics.',
            'fairness_limits':['Post-observation revision; no independent holdout claim',
                'Matched ceilings, not exact tokenizer or equal syntax burden',
                'IR static rejection and JS callback rejection occur at different stages',
                'IR AST and JS protocol/compute costs remain distinct',
                'No new runtime support, no correction or re-scoring of original candidates',
                'Parent fairness review remains pending; API send entry point stays disabled']}

def write_review_bundle(output):
    contract=load_review();output=Path(output);output.mkdir(parents=True,exist_ok=False)
    def save(name,value):
        (output/name).write_bytes((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    save('protocol.json',contract);save('diff.json',review_diff(contract))
    save('source-evidence.json',contract['source_manifest'])
    save('assessment-policy.json',contract['assessment_policy'])
    for arm in contract['public_arms']:
        directory=output/arm['representation'];directory.mkdir()
        text='\n\n'.join(f'[{m["role"]}]\n{m["content"]}' for m in arm['messages'])
        (directory/'complete-prompt.txt').write_text(text+'\n',encoding='utf-8')
        save(Path(arm['representation'])/'request.json',arm['request_body'])
        save(Path(arm['representation'])/'contract-not-checked.json',
             make_assessment(representation=arm['representation'],protocol_digest=digest(contract)))
    save('notrun.json',{'status':'REVIEW_ONLY_NOT_SENT','planned_future_arms':2,'request_attempts':0,
                       'reported_model_calls':0,'authenticated_requests':0,'candidate_executions':0,
                       'repairs':0,'reset_cards_used':0,'send_enabled':False,'push_performed':False})
    return {'status':'REVIEW_ONLY_NOT_SENT','protocol_digest':digest(contract),'output':str(output)}
