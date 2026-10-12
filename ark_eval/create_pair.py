"""One frozen Create development pair. Prompts use public inputs only."""
from dataclasses import asdict
import json
from pathlib import Path

from headless_v01.compiler import digest
from headless_v01.types import canonical_json
from .adapter import ACCOUNT_MODEL,EvaluationError,PLAN_URL,PlanConfig,bounded_json,prepare
from .budget import Budget
from .callback_contract import (ORDERING,IR_API,JS_API,EXAMPLE_ABI,EXAMPLE_IR,EXAMPLE_JS,
                                HOST_LIMIT,js_starter)
from .tasks import base_program,task

ROOT=Path(__file__).resolve().parents[1]
FREEZE=Path(__file__).with_name('create_pair.freeze.json')
LEGACY_FREEZE=Path(__file__).with_name('create_pair.v1.freeze.json')
LEGACY_DIGEST='b139ae8f59fed7e1dc1ce9bfb812bb43c204a4c5265fe505d3637da6de10d89c'
THINKING={'type':'disabled'}
TASK='P-C02-create'
ARMS=('gameir_text','js_callback')
OUTPUT_TOKENS=4096
SOURCE_BYTES=16384
PROMPT_UNITS=24000
FROZEN_FILES=(
    'headless_v01/compiler.py','headless_v01/runtime.py','headless_v01/types.py',
    'headless_v01/services.py','headless_v01/plan_checks.py',
    'ark_eval/adapter.py','ark_eval/budget.py','ark_eval/tasks.py','ark_eval/callback_contract.py',
    'ark_eval/host.py','ark_eval/oracle.py','ark_eval/final_tests.json','ark_eval/build_corpus.py',
    'ark_eval/container_profile.py','ark_eval/node_worker.mjs','ark_eval/node_supervisor.sh',
    'ark_eval/node_backend.py','ark_eval/js_runner.py','ark_eval/node_image_lock.json',
    'ark_eval/manual_probe.py','ark_eval/create_pair.py','ark_eval/manual_pair.py',
    'ark_eval/create_pair.v1.freeze.json')

IR_SHAPES='''Core节点参数：literal:{type,value}；var:{name}；resolve/read:{entity[,field]}；
is_some/require/not:{value}；and/ge/le/sub:{a,b}；record:{items:{字段:表达式}}；
let语句:{name,value}；if语句:{condition,then:[语句],else:[语句]}；
write语句:{entity,field,value}；emit语句:{event,payload:record表达式}；每节点必须有op。
let绑定仅在当前handler/分支作用域可见，player与door为事件payload中的初始变量。
只输出完整Core JSON，不加Markdown、解释、参考测试或自定义服务/扩展。
'''
JS_SHAPES='''只输出export function onOpen(ctx, {player, door}) {...}，不加Markdown或解释。
ctx接口同步；只使用给定宿主API和普通局部变量/条件。不得导入依赖、创建工具或访问环境。
'''


def arm_budget():
    return Budget(prompt_units=PROMPT_UNITS,output_tokens=OUTPUT_TOKENS,attempts=1,repairs=0,tool_calls=0)


def public_arms():
    ordering=ORDERING.replace('cost 为1；Modify将单一cost绑定改为2；Debug修复单一比较符号。','cost 为1。')
    ordering=ordering.replace('模型尝试3、修复2、评测工具3；修复只见同粒度开发反馈，最终测试不反馈。',
                              '本小试每臂模型尝试1、修复0、工具0；最终测试不反馈。')
    common=(ordering+'\n共同宿主空manifest（两组相同，不含实现）：\n'+canonical_json(base_program())+
            '\n输出预算每臂4096 tokens，源码UTF-8上限16384bytes；不调用工具、不请求修复。\n')
    requirement=task(TASK)['requirement']
    starters={'gameir_text':canonical_json(base_program()),'js_callback':js_starter(TASK)}
    arms=[]
    for rep in ARMS:
        api=IR_API+IR_SHAPES if rep=='gameir_text' else JS_API+JS_SHAPES
        example=canonical_json(EXAMPLE_IR) if rep=='gameir_text' else EXAMPLE_JS
        messages=[{'role':'system','content':common},
                  {'role':'user','content':requirement+'\n'+api+'\n'+EXAMPLE_ABI+'\n'+example+
                   '\n起始程序：\n'+starters[rep]}]
        request=prepare(messages,OUTPUT_TOKENS,PlanConfig(model=ACCOUNT_MODEL),thinking=THINKING)
        if request.prompt_units>PROMPT_UNITS:raise EvaluationError('PAIR_PROMPT_BUDGET_EXCEEDED')
        arms.append({'representation':rep,'messages':messages,'starter':starters[rep],
                     'request_body':request.body,'request_digest':request.request_digest,
                     'prompt_units_estimate':request.prompt_units})
    return arms


def current_protocol():
    arms=public_arms()
    return {'schema':'gir-create-pair-freeze@2','scope':'ONE_DEVELOPMENT_CREATE_PAIR_NOT_HOLDOUT_NOT_AI_ADVANTAGE',
            'previous_protocol_digest':LEGACY_DIGEST,
            'task_id':TASK,'model':ACCOUNT_MODEL,'endpoint':PLAN_URL+'/chat/completions',
            'order':list(ARMS),'independent_sessions':True,'budget_per_arm':asdict(arm_budget()),
            'maximum_pair_output_tokens':2*OUTPUT_TOKENS,'maximum_pair_attempts':2,
            'HTTPS_socket_timeout_seconds_per_arm':120,
            'source_bytes_limit':SOURCE_BYTES,'host_operations_per_tick':HOST_LIMIT,
            'oracle_case_count_per_arm':26,'oracle':'FIXED_EVALUATOR_OWNED_NOT_SENT_TO_MODEL',
            'oracle_holdout':False,'seeds':[1,123],
            'artifact_hashes':{name:digest((ROOT/name).read_text(encoding='utf-8')) for name in FROZEN_FILES},
            'request_digests':{a['representation']:a['request_digest'] for a in arms},
            'starter_digests':{a['representation']:digest(a['starter']) for a in arms},
            'common_information_digest':digest(arms[0]['messages'][0]['content']),
            'public_arms':arms,'repair_budget':0,'feedback_to_model':'NONE',
            'input_token_accounting':'MATCHED_24000_PROMPT_UNITS_CEILING_NOT_VERIFIED_TOKENIZER',
            'provider_usage':'REPORTED_TOTAL_COMPLETION_TOKENS_NO_INFERRED_REASONING_SPLIT',
            'thinking_parameters':{'thinking':THINKING},
            'thinking_support':{'official_source':'https://www.volcengine.com/docs/ark/deep-thinking',
                'evidence':'OFFICIAL_INDEXED_MODEL_SPECIFIC_EXCERPT_ENABLED_DEFAULT_AND_DISABLED',
                'Plan_compatibility':'UNVERIFIED_PENDING_USER_LOCAL_REQUEST',
                'nonempty_reasoning_response':'HALT_THINKING_MISMATCH_NO_DEFAULT_FALLBACK'},
            'model_revision_claim':'EXACT_REPORTED_IDENTIFIER_ONLY_NOT_WEIGHTS_ATTESTATION',
            'candidate_execution':'BLOCKED_PENDING_SOURCE_REVIEW_AND_FRESH_OS_ISOLATION_CHECK',
            'runtime_semantics':'EXISTING_SHARED_HOST_IR_AND_JS_FIXED_ORACLE',
            'ir_architecture_frozen':False,'TS':'DEFERRED','Vitric':'ORIGINAL_BASELINE_ABSENT'}


def load_freeze(path=FREEZE):
    path=Path(path)
    if not path.is_file() or path.stat().st_size>1048576:raise EvaluationError('PAIR_FREEZE_MISSING_OR_OVERSIZED')
    contract=bounded_json(path.read_text(encoding='utf-8'))
    if contract!=current_protocol():raise EvaluationError('PAIR_CONTRACT_DRIFT')
    return contract


def load_legacy_freeze():
    """Pinned historical data for import only; never a transport selection."""
    if not LEGACY_FREEZE.is_file() or LEGACY_FREEZE.stat().st_size>1048576:
        raise EvaluationError('PAIR_LEGACY_FREEZE_MISSING')
    contract=bounded_json(LEGACY_FREEZE.read_text(encoding='utf-8'))
    if digest(contract)!=LEGACY_DIGEST:raise EvaluationError('PAIR_LEGACY_CONTRACT_DRIFT')
    return contract


def import_contract(contract):
    if contract==load_legacy_freeze():return contract
    if contract!=load_freeze():raise EvaluationError('PAIR_CONTRACT_DRIFT')
    return contract


def prepared_requests(contract):
    import_contract(contract)
    setting=None if contract['schema']=='gir-create-pair-freeze@1' else THINKING
    return [(a['representation'],prepare(a['messages'],OUTPUT_TOKENS,PlanConfig(model=ACCOUNT_MODEL),thinking=setting))
            for a in contract['public_arms']]
