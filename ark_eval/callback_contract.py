"""Phase 1: natural callback starters and sealed independent development contract."""
import copy
import re
from dataclasses import asdict

from headless_v01.compiler import digest
from headless_v01.types import canonical_json
from .adapter import EvaluationError
from .budget import Budget
from .tasks import TASKS, base_program, task

HOST_LIMIT = 128
REQUEST_LIMIT = 32
MESSAGE_BYTES = 8192
ORDERING = """同一可信游戏宿主上的 GameIR 与 JS 回调对照；无服务、网络、文件或时钟。
按顺序：resolve(player)，仅当存在才 resolve(door)；任一不存在则返回且无事件。
双方存在后 require(read(door,open))；已开门则返回，不读取玩家 keys。
门未开时 require(read(player,keys))；缺失 cell 为 InvalidEntity，整个 tick 回滚。
keys >= cost 时按顺序 write(player,keys,sub(keys,cost))、write(door,open,true)、emit Opened(door)。
否则 emit MissingKey(player)。cost 为1；Modify将单一cost绑定改为2；Debug修复单一比较符号。
每次成功只发一次 Opened；同tick重复Open和后续tick重复Open均不再扣钥匙。
输入实体不要求 player/door 不同；字段缺失遵循上述 require 规则。
每事件读取快照加自身写入；其它 handler 的写入在该事件合并前不可见。冲突/错误回滚整个tick。
keys:int[0,10]；open:bool；两字段authority=IR,policy=exclusive；EntityID为[world_epoch,tick,job,ordinal]。
Open:{player:EntityID,door:EntityID}；Opened:{door:EntityID}；MissingKey:{player:EntityID}；Tick:{}。
宿主操作上限128/tick；模型尝试3、修复2、评测工具3；修复只见同粒度开发反馈，最终测试不反馈。
"""
JS_API = """ctx.resolve(entity)->{some:EntityID}|{none:true}; ctx.exists(entity)->bool(同resolve)；
ctx.read(entity,field)->option(value)；ctx.require(option)->value或InvalidEntity；
ctx.sub(a,b)->checked safe-int；ctx.write(entity,field,value)；ctx.emit(event,payload)。
它们是宿主调用，不是Core构造函数。普通JS if/return/局部变量只控制当前事件；不得保留可变全局状态。
提交 export function onOpen(ctx, {player, door}) {...}；不导入依赖。TS本轮暂缓。
"""
IR_API = """提交完整Core JSON：literal、var、let、resolve/is_some、read/require、not/and、ge/le、
sub、if、write、emit；handler id=open,event=Open,priority=0；其它ABI同共同说明。
上述操作分别调用同一宿主；普通AST访问成本与JS计算成本分别报告。
"""
EXAMPLE_JS = "export function onExample(ctx, {target}) { ctx.write(target, 'score', 7); }"
EXAMPLE_IR = {"id": "example", "event": "Example", "priority": 0, "body": [
    {"op": "write", "entity": {"op": "var", "name": "target"}, "field": "score",
     "value": {"op": "literal", "type": "int", "value": 7}}]}
EXAMPLE_ABI = "独立可见示例：Example:{target:EntityID}，score:int/IR/exclusive。两组示例只写score=7，非开门答案。"


def js_starter(task_id):
    task(task_id)
    if task_id == "P-C02-create":
        return "export function onOpen(ctx, {player, door}) {\n  // TODO\n}\n"
    comparison = "<=" if task_id == "P-C02-debug" else ">="
    return """export function onOpen(ctx, {player, door}) {
  if (!ctx.exists(player) || !ctx.exists(door)) return;
  if (ctx.require(ctx.read(door, 'open'))) return;
  const cost = 1;
  const keys = ctx.require(ctx.read(player, 'keys'));
  if (keys COMPARISON cost) {
    ctx.write(player, 'keys', ctx.sub(keys, cost));
    ctx.write(door, 'open', true);
    ctx.emit('Opened', {door});
  } else {
    ctx.emit('MissingKey', {player});
  }
}
""".replace("COMPARISON", comparison)


def callback_protocol():
    from .protocol import protocol
    p = protocol()
    return {"schema": "ark-callback-pilot@1", "stage": "DEVELOPMENT_NOT_FROZEN",
            "scope": "SAME_TRUSTED_GAME_HOST_GAMEIR_VS_JS_CALLBACK",
            "representations": ["gameir_text", "js_callback"], "task_ids": list(TASKS),
            "requested_model": p["requested_model"], "model_revision": None, "base_url": p["base_url"],
            "budget": asdict(Budget()), "max_output_tokens": 1024, "seeds": p["seeds"],
            "artifact_hashes": p["artifact_hashes"], "host_operations": HOST_LIMIT,
            "requests_per_invocation": REQUEST_LIMIT, "requests_per_tick": 256,
            "message_bytes": MESSAGE_BYTES, "response_bytes": MESSAGE_BYTES,
            "timeout_seconds": 10, "feedback": "DEVELOPMENT_GENERIC_CODES_ONLY",
            "model_session": "INDEPENDENT_PER_TASK_ARM", "arm_order": "ALTERNATE_BY_TASK_INDEX",
            "legacy_runtime_units": 20000, "computation_metrics": "IR_AST_AND_JS_COMPUTE_REPORTED_SEPARATELY",
            "token_accounting": "OFFLINE_ESTIMATE_ONLY_PROVIDER_TOKENIZER_CEILINGS_UNVERIFIED",
            "holdout": False, "vitric_equivalence": "NOT_ESTABLISHED",
            "source_executor": "UNREVIEWED_SOURCE_EXECUTION_DISABLED",
            "reviewed_source_executor": "ISOLATED_NODE_CATALOG_ONLY", "live_transport": "DISABLED",
            "ts": "DEFERRED", "plan_permission": "UNKNOWN", "ir_frozen": False}


def callback_pair(task_id):
    from .reference import starter
    p = callback_protocol()
    shared = {"task_id": task_id, "requirement": task(task_id)["requirement"],
              "ordering": ORDERING, "model": p["requested_model"], "model_revision": None,
              "budget": asdict(Budget()), "host_operations": HOST_LIMIT,
              "seeds": p["seeds"], "hashes": p["artifact_hashes"],
              "max_output_tokens": 1024, "feedback": "DEVELOPMENT_GENERIC_CODES_ONLY",
              "timeout_seconds": 10, "example_abi": EXAMPLE_ABI,
              "scope": "SAME_TRUSTED_GAME_HOST_GAMEIR_VS_JS_CALLBACK"}
    ir = starter(task_id)
    sources = {"gameir_text": canonical_json(ir if ir is not None else base_program()),
               "js_callback": js_starter(task_id)}
    return [{"representation": rep, "shared": copy.deepcopy(shared), "starter": sources[rep],
             "messages": [{"role": "system", "content": ORDERING},
                          {"role": "user", "content": shared["requirement"] + "\n" +
                           (IR_API if rep == "gameir_text" else JS_API) + "\n" + EXAMPLE_ABI + "\n" +
                           (canonical_json(EXAMPLE_IR) if rep == "gameir_text" else EXAMPLE_JS) +
                           "\n起始程序：\n" + sources[rep]}]}
            for rep in ("gameir_text", "js_callback")]


def validate_callback_pair(arms):
    try:
        tid = arms[0]["shared"]["task_id"]
        if arms != callback_pair(tid): raise EvaluationError("CALLBACK_PROTOCOL_DRIFT")
    except (IndexError, KeyError, TypeError):
        raise EvaluationError("INVALID_CALLBACK_PAIR") from None
    return True


def static_js_preflight(source):
    """Conservative text checks, NOT a JS parser, capability sandbox or approval."""
    if not isinstance(source, str): raise EvaluationError("INVALID_SOURCE")
    try:
        if len(source.encode("utf-8")) > 16384: raise EvaluationError("SOURCE_SIZE")
    except UnicodeError: raise EvaluationError("INVALID_UNICODE") from None
    if not re.match(r"\s*export function onOpen\(ctx,\s*\{player,\s*door\}\)\s*\{", source):
        raise EvaluationError("CALLBACK_SIGNATURE")
    if re.search(r"\b(import|process|globalThis|fetch|eval|Function|Date|Math|WebAssembly|Worker)\b|(?<![.\w])require\s*\(", source):
        raise EvaluationError("FORBIDDEN_SOURCE_TOKEN")
    return {"source_digest": digest(source), "status": "STATIC_TEXT_ONLY", "syntax_validated": False,
            "source_executed": False, "execution_allowed": False}


def model_records():
    return [{"task_id": tid, "representation": rep, "status": "NOT_RUN", "attempts": 0,
             "repairs": 0, "tool_calls": 0, "tokens_in": None, "tokens_out": None,
             "pass_at_1": None, "final_pass": None, "real_model_calls": 0,
             "blockers": ["UNREVIEWED_SOURCE_EXECUTION_DISABLED", "PLAN_PERMISSION_UNKNOWN",
                          "ACCOUNT_REVISION_AND_QUOTA_UNKNOWN", "TOKENIZER_CEILINGS_UNVERIFIED"]}
            for tid in TASKS for rep in ("gameir_text", "js_callback")]
