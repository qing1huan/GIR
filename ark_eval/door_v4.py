"""Offline v4 Modify/Debug contract; no transport or candidate execution path.

Public request construction reads only independent public starter data. Private
fixed expectations are authored from the contract, never from runtime outputs.
Old freezes, answers, oracle and runners are preserved and hash-checked.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from headless_v01.compiler import compile_program, digest
from .adapter import ACCOUNT_MODEL, PLAN_URL, EvaluationError, PlanConfig, bounded_json, prepare
from .contract_v3 import API, FACTS, independent_examples

ROOT=Path(__file__).resolve().parents[1]
DATA=Path(__file__).with_name('door_v4_starters')
FREEZE=Path(__file__).with_name('door_v4.freeze.json')
DIAGNOSTICS=Path(__file__).with_name('door_v4_diagnostics.json')
HISTORY=Path(__file__).with_name('door_v4_history_baseline.json')
TASKS=('P-C02-modify','P-C02-debug')
ARMS=('gameir_text','js_callback')
FILES={('P-C02-modify','gameir_text'):'modify.ir.json',('P-C02-modify','js_callback'):'modify.js',
       ('P-C02-debug','gameir_text'):'debug.ir.json',('P-C02-debug','js_callback'):'debug.js'}
COST={'P-C02-modify':2,'P-C02-debug':1}
REQUIREMENTS={
 'P-C02-modify':'将给定正确 cost=1 开门程序改为至少2把钥匙才可开门且消耗2把；共同契约其余行为不变。starter 中 cost 是唯一门槛/消耗绑定。',
 'P-C02-debug':'修复给定开门程序的比较方向错误，使至少1把钥匙时开门并扣1把，不足时发出 MissingKey；共同契约其余行为不变。两臂各有一个等价比较缺陷。'}
BUSINESS='''v4 新任务契约；开发评测。旧任务、旧候选、旧成绩不能按本版本追改。
确认 player 与 door 都存在；任一目标实体不存在时当前 Open 正常结束，不写状态、不发事件。
双方存在时 door.open 是本次判断的必需 cell；缺失须抛 InvalidEntity，不得当作 false、默认值或正常结束。
door.open 为 true 时正常结束，player.keys 不必存在。允许无害的提前 read，但不得提前 require keys 使这条正常路径报错。
door.open 为 false 时 player.keys 才是必需 cell；缺失须抛 InvalidEntity。
keys >= cost 时扣 cost、打开门、发一次 Opened(door)；否则每次 Open 发一次 MissingKey(player)。成功后同 tick 或后续 tick 的重复 Open 不再扣钥匙、不再发 Opened。
对应本次决策的必需值验证通过后，才能发起业务 write/emit。任一错误回滚整个 tick，包括较早 Open 的效果；已提交状态、已发布事件及命令无残留，失败尝试审计可保留。
read 返回 option；none 本身不是错误，require(none) 抛 InvalidEntity。IR 可用 require(read(...))，JS 可用 ctx.require(ctx.read(...))，两者含义相同。
只固定错误与效果的因果时机；不强制 resolve 左右顺序、冗余安全读取、逐项相同轨迹或调用次数。所有调用仍受既定预算限制。
双方共享 core-v0.1 世界/字段/事件 ABI：keys:int[0,10]、open:bool，authority=IR、policy=exclusive；Open:{player:EntityID,door:EntityID}、Opened:{door:EntityID}、MissingKey:{player:EntityID}、Tick:{}。
输入不要求 player/door 不同；不新增引擎功能、服务、网络、文件、时钟、TS、扩展或工具。
每臂独立会话，尝试1、修复0、工具0；输出4096 tokens、源码16384 UTF-8 bytes。输入24000 byte+message-overhead units 是估计上限，不是精确 token 数，不添加填充。
'''


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):return bounded_json(path.read_text(encoding='utf-8'))


def public_arms():
    """No oracle/reference/candidate reads; each arm receives its own starter."""
    common=BUSINESS+'\n共同宿主语义：\n'+'\n'.join(f'[{c} {t}] {s}' for c,t,s,_ in FACTS)
    result=[]
    for task in TASKS:
        for rep in ARMS:
            path=DATA/FILES[task,rep]
            if path.is_symlink() or not path.is_file() or path.stat().st_size>16384:
                raise EvaluationError('V4_STARTER_PATH_REJECTED')
            starter=path.read_text(encoding='utf-8')
            if rep=='gameir_text':
                compile_program(bounded_json(starter)) # Static trusted starter check only.
                output='只输出完整 core-v0.1 JSON；handler id=open,event=Open,priority=0。player/door 是事件 payload 自动绑定的 EntityID 初始变量。没有 return op；空分支/列表结束表示正常结束。'
            else:
                output='只输出 export function onOpen(ctx, {player, door}) {...}；使用列出的 ctx 方法及普通 JS 局部条件、对象，允许原生 int truthiness。不导入依赖、不捕获宿主错误、不保存可变全局、不创建异步任务、不返回值。'
            lines=[REQUIREMENTS[task],f'目标 cost={COST[task]}。',output,'任务级 API（闭合）：']
            for op,_,_,meaning,ir,js,_ in API:lines.append(f'[{op}] {ir if rep==ARMS[0] else js}；{meaning}')
            lines.append('公共独立 Probe/score 片段，仅说明接口，不是本任务答案：')
            for e in independent_examples():
                fragment=json.dumps(e['IR'],ensure_ascii=False,separators=(',',':')) if rep==ARMS[0] else e['JS']
                lines.append(f'[{e["id"]}] {e["meaning"]}\n{fragment}')
            lines.append('本臂公开初始程序：\n'+starter)
            messages=[{'role':'system','content':common},{'role':'user','content':'\n'.join(lines)}]
            request=prepare(messages,4096,PlanConfig(model=ACCOUNT_MODEL),thinking={'type':'disabled'})
            if request.prompt_units>24000:raise EvaluationError('V4_INPUT_UNITS_EXCEEDED')
            result.append({'task_id':task,'representation':rep,'starter_file':str(path.relative_to(ROOT)).replace('\\','/'),
                'starter_sha256':sha(path),'messages':messages,'request_body':request.body,
                'request_digest':request.request_digest,'prompt_units_estimate':request.prompt_units})
    return result


def diagnostic_document():
    """Hand-authored contract expectations; no reference/runtime imports."""
    p=['world',-1,'genesis',0];d=['world',-1,'genesis',1];bad=['world',-1,'genesis',2]
    absent=['world',-1,'genesis',99];cases=[]
    def event(player,door):return {'name':'Open','payload':{'player':player,'door':door}}
    for task in TASKS:
        cost=COST[task]
        specs=[
          ('D01-missing-open',[{'id':p,'values':{'keys':cost}},{'id':d,'values':{}}],[event(p,d)],True),
          ('D02-missing-player-before-open',[{'id':d,'values':{}}],[event(absent,d)],False),
          ('D03-missing-door-before-keys',[{'id':p,'values':{}}],[event(p,absent)],False),
          ('D04-whole-tick-rollback',[{'id':p,'values':{'keys':cost}},{'id':d,'values':{'open':False}},{'id':bad,'values':{}}],
           [event(p,d),event(p,bad)],True)]
        for label,initial,inputs,failed in specs:
            expected={'entities':copy.deepcopy(initial),'statuses':['ABORTED' if failed else 'COMMITTED'],
                'events':[],'commands':[],'errors':['InvalidEntity' if failed else None]}
            for seed in (1,123):cases.append({'id':f'v4-{task}-{label}-seed{seed}','task_id':task,'seed':seed,
                'initial':copy.deepcopy(initial),'steps':[{'inputs':copy.deepcopy(inputs)}],'expected':copy.deepcopy(expected)})
    return {'schema':'gir-door-v4-diagnostic-oracle@1','holdout':False,'case_count_per_task_arm':8,
        'origin':'HAND_AUTHORED_FROM_V4_CONTRACT_NOT_RUNTIME_REFERENCE_OR_CANDIDATE_OUTPUT',
        'score_scope':'SEPARATE_FROM_ORIGINAL26; NO_OLD_CANDIDATE_RESCORE','cases':cases}


def verify_history():
    document=read_json(HISTORY)
    for name,value in document['sha256'].items():
        path=ROOT/name
        if (not name.startswith(('ark_eval/','headless_v01/','results/')) or '..' in Path(name).parts
                or path.name.lower()=='env.txt' or path.is_symlink()
                or not path.resolve().is_relative_to(ROOT.resolve()) or sha(path)!=value):
            raise EvaluationError('V4_HISTORY_CHANGED')
    return document


def make_protocol():
    history=verify_history();arms=public_arms();diags=read_json(DIAGNOSTICS)
    if diags!=diagnostic_document():raise EvaluationError('V4_DIAGNOSTIC_DRIFT')
    legacy=read_json(ROOT/'ark_eval/final_tests.json')
    selections={t:[c for c in legacy['cases'] if c['task_id']==t] for t in TASKS}
    if any(len(c)!=26 for c in selections.values()):raise EvaluationError('V4_LEGACY_SELECTION_REJECTED')
    old=read_json(ROOT/'results/repair001-isolated-continuation-20261011/approved-both-arms-manifest.json')
    names=set(old['bindings'])|{'ark_eval/door_v4.py','ark_eval/tests/test_door_v4.py',
        'ark_eval/door_v4_diagnostics.json','ark_eval/door_v4_history_baseline.json','docs/ark-door-v4-review.md'}
    names.update(a['starter_file'] for a in arms)
    return {'schema':'gir-door-development-freeze@4','stage':'OFFLINE_REVIEW_READY_NOT_SENT',
        'scope':'MODIFY_DEBUG_POST_OBSERVATION_DEVELOPMENT_NOT_HOLDOUT',
        'base_commit':'fdaedc5a8321b2269ac7c5e3e3d316bbca58caa1',
        'task_ids':list(TASKS),'representations':list(ARMS),'model':ACCOUNT_MODEL,
        'endpoint':PLAN_URL+'/chat/completions','thinking_parameters':{'thinking':{'type':'disabled'}},
        'order':[{'task_id':TASKS[0],'arms':list(ARMS)},{'task_id':TASKS[1],'arms':list(reversed(ARMS))}],
        'independent_sessions':4,'budget_per_arm':{'input_byte_units':24000,'output_tokens':4096,'attempts':1,'repairs':0,'tool_calls':0},
        'maximum_batch_attempts':4,'maximum_batch_output_tokens':16384,'source_bytes_limit':16384,
        'input_units_are_tokens':False,'input_padding_added':False,'feedback_to_model':'NONE',
        'seeds':[1,123],'public_arms':arms,'common_information_sha256':hashlib.sha256(arms[0]['messages'][0]['content'].encode()).hexdigest(),
        'private_oracle':{'legacy_file':'ark_eval/final_tests.json','legacy_sha256':sha(ROOT/'ark_eval/final_tests.json'),
            'legacy26_selection_digests':{t:digest(cs) for t,cs in selections.items()},
            'diagnostic_file':str(DIAGNOSTICS.relative_to(ROOT)).replace('\\','/'),'diagnostic_sha256':sha(DIAGNOSTICS),
            'diagnostic_count_per_task_arm':8,'reporting':'LEGACY26_AND_V4_DIAGNOSTIC8_SEPARATE_NO_OLD_RESCORE'},
        'artifact_sha256':{n:sha(ROOT/n) for n in sorted(names)},'preserved_history_sha256':history['sha256'],
        'runtime':'UNCHANGED_SHARED_HOST_IR_AND_JS; ORIGINAL_ISOLATION_TRANSPORT',
        'limits':{'host_operations_per_tick':128,'JS_requests_per_handler':32,'JS_requests_per_tick':256,
            'wall_seconds_per_invocation':10,'memory_bytes':67108864,'pids':32,'network':'none','rootfs':'readonly','user_mounts':[]},
        'fairness':'SAME_SEMANTIC_INFORMATION_AND_MATCHED_CEILINGS; NOT_EQUAL_SYNTAX_OR_EXACT_TOKENS',
        'starter_policy':'OWN_PUBLIC_DATA_ONLY; MODIFY_SINGLE_COST_BINDING; DEBUG_SINGLE_EQUIVALENT_COMPARE_DEFECT',
        'historical_JS_verdict_note':'Historical static FAIL is preserved; missing-open behavior was ambiguous in prior task wording and is not a proven old-task defect.',
        'full_contract_rules_not_checked':['all_source_paths','player_door_alias','full_effect_authorization','full_publication_repeat_rollback'],
        'send_enabled':False,'candidate_execution_enabled':False,'activation_path':'NONE; FUTURE_SEPARATELY_REVIEWED_USER_LOCAL_ENTRY',
        'authenticated_requests':0,'model_calls':0,'candidate_executions':0,'reset_cards_used':0,'push_performed':False}


def load_freeze(path=FREEZE):
    frozen=read_json(Path(path))
    if digest(frozen)!=digest(make_protocol()):raise EvaluationError('V4_FREEZE_DRIFT')
    return frozen


def send(*args,**kwargs):raise EvaluationError('V4_SEND_DISABLED')


def execute_candidate(*args,**kwargs):raise EvaluationError('V4_CANDIDATE_EXECUTION_DISABLED')


def write_review_bundle(output):
    protocol=load_freeze();output=Path(output)
    if output.exists():raise EvaluationError('V4_FRESH_OUTPUT_REQUIRED')
    output.mkdir(parents=True)
    def save(name,value):
        path=output/name;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
    save('protocol.json',protocol)
    save('diagnostic-oracle-private.json',read_json(DIAGNOSTICS))
    legacy=read_json(ROOT/'ark_eval/final_tests.json')
    save('legacy26-private.json',{t:[c for c in legacy['cases'] if c['task_id']==t] for t in TASKS})
    for arm in protocol['public_arms']:
        prefix=arm['task_id']+'/'+arm['representation']+'/'
        save(prefix+'request.json',arm['request_body'])
        save(prefix+'request-bindings.json',{k:arm[k] for k in ('request_digest','starter_file','starter_sha256','prompt_units_estimate')})
        text='\n\n'.join('['+m['role']+']\n'+m['content'] for m in arm['messages'])
        with (output/(prefix+'complete-prompt.txt')).open('x',encoding='utf-8') as f:f.write(text+'\n')
    save('notrun.json',{'status':'OFFLINE_REVIEW_READY_NOT_SENT','protocol_digest':digest(protocol),
        'planned_sessions':4,'request_attempts':0,'model_calls':0,'authenticated_requests':0,'candidate_executions':0,
        'gameplay':{t:{r:{'legacy26':{'PASS':0,'FAIL':0,'NOTRUN':26},'diagnostic8':{'PASS':0,'FAIL':0,'NOTRUN':8}}
            for r in ARMS} for t in TASKS},'send_enabled':False,'candidate_execution_enabled':False,'reset_cards_used':0,'push_performed':False})
    return {'status':'OFFLINE_REVIEW_READY_NOT_SENT','protocol_digest':digest(protocol),'output':str(output)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv);print(json.dumps(write_review_bundle(args.output),ensure_ascii=False));return 0


if __name__=='__main__':raise SystemExit(main())
