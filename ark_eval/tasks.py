"""Public pilot inputs only. Fixed final tests and reference answers live elsewhere."""
import copy

from .adapter import EvaluationError

PLAYER = ["world", -1, "genesis", 0]
DOOR = ["world", -1, "genesis", 1]
TASKS = {
    "P-C02-create": {"kind": "Create", "cost": 1,
        "requirement": "实现 Open(player,door)：双方存在、门未开且玩家钥匙至少1时扣1把钥匙，打开门并发出一次 Opened(door)。钥匙不足发出 MissingKey(player)。门已开或任一实体不存在时不改变状态，不发出事件。重复 Open 在后续 tick 不重复消耗。"},
    "P-C02-modify": {"kind": "Modify", "cost": 2,
        "requirement": "将既有开门规则改为至少2把钥匙才可开门且消耗2把；其它事件、实体存在性、已开门行为不变。"},
    "P-C02-debug": {"kind": "Debug", "cost": 1,
        "requirement": "修复既有开门规则的钥匙比较方向错误：至少1把钥匙时开门并扣1把，不足时发出 MissingKey，其它行为不变。"},
}

SHARED_API = """共同世界 ABI：core-v0.1；keys:int,range[0,10],IR/exclusive；open:bool,IR/exclusive。
Tick:{}；Open:{player:EntityID,door:EntityID}；Opened:{door:EntityID}；MissingKey:{player:EntityID}。
本 Pilot 无服务、网络、文件、时钟或扩展；整数为 safe-int。
两组都使用同一 Core 操作：literal(type,value),var(name),resolve(entity),is_some(value),
read(entity,field):option,require(value),record(items),and(a,b),not(value),ge(a,b),le(a,b),
sub(a,b),if(condition,then,else),write(entity,field,value),emit(event,payload)。
handler 使用 id,event,priority,body。变量来自事件 payload；每 tick 按现有 Runtime
的快照/own-overlay、原子提交和 microstep 规则处理。不得用 JS host side effects。
返回完整程序；不使用另一组输出；只可使用公开开发反馈。最终测试不参与修复反馈。
"""

IR_TUTORIAL = "输出完整 Core JSON 数据。节点使用上述 op 及参数名；无回调。"
JS_TUTORIAL = "未来 JS/TS 前端输出同一 Core ABI。当前仅数据桥接：输出 export default defineGame(<完整Core JSON>);；TS 可用 export default defineGame(<完整Core JSON>) satisfies CoreProgram;。它不是完整 JS/TS 能力评测。"


def base_program():
    return {"version": "core-v0.1", "fields": {
        "keys": {"type": "int", "authority": "IR", "policy": "exclusive", "range": [0, 10]},
        "open": {"type": "bool", "authority": "IR", "policy": "exclusive"}},
        "events": {"Tick": {"record": {}}, "Open": {"record": {"player": "EntityID", "door": "EntityID"}},
                   "Opened": {"record": {"door": "EntityID"}}, "MissingKey": {"record": {"player": "EntityID"}}},
        "handlers": [], "services": {}, "extensions": {}}


def task(task_id):
    if task_id not in TASKS:
        raise EvaluationError("UNKNOWN_TASK")
    return copy.deepcopy(TASKS[task_id])


def public_input(task_id, representation, starter_core=None):
    """No arbitrary extra prompt keys, oracle imports, final tests or answers."""
    requirement = task(task_id)["requirement"]
    if representation not in {"gameir_text", "wrapper_format"}:
        raise EvaluationError("UNKNOWN_REPRESENTATION")
    if (task_id != "P-C02-create") != (starter_core is not None):
        raise EvaluationError("STARTER_REQUIRED_FOR_MODIFY_DEBUG_ONLY")
    # A starter for Modify/Debug is deliberately visible in BOTH representations.
    from headless_v01.types import canonical_json
    starter = canonical_json(starter_core if starter_core is not None else base_program())
    if representation == "wrapper_format":
        starter = "export default defineGame(" + starter + ");"
    return [{"role": "system", "content": "只完成给定玩法任务。提交一种表示的完整程序；不请求工具或读取文件。\n" + SHARED_API},
            {"role": "user", "content": requirement + "\n" + (IR_TUTORIAL if representation == "gameir_text" else JS_TUTORIAL)
             + "\n起始程序：\n" + starter}]
