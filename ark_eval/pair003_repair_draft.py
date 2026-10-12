"""Offline proposal only: one equal-budget repair opportunity per pair003 arm.

No transport, credential, execution, reservation or automatic send entry exists.
The original protocol and candidates stay immutable. This is not a live freeze.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from headless_v01.compiler import digest
from .adapter import EvaluationError,PlanConfig,prepare,bounded_json
from .manual_pair_v3_1 import load_review_requests,ROOT,PROTOCOL_DIGEST
from .pair003_eval import load_candidates,HASHES

RESULTS=ROOT/'results/pair003-isolated-evaluation-20261011'
SYSTEM_REPLACEMENTS=(
    ('宿主操作上限128/tick；本小试每臂模型尝试1、修复0、工具0；最终测试不反馈。',
     '宿主操作上限128/tick；原生成轮每臂尝试1、修复0、工具0；本独立修复轮每臂追加一次修复请求、无工具/重试/后续修复；仅反馈已观察的有限错误类别，不反馈具体测试输入、期望或参考解。'),
    ('输出预算每臂4096 tokens，源码UTF-8上限16384bytes；不调用工具、不请求修复。',
     '输出预算每臂4096 tokens，源码UTF-8上限16384bytes；本修复轮不调用工具、不请求更多修复。'),
)
REPAIR_STAGE_SYSTEM=(
    '[本轮阶段说明] 本请求是原生成后的独立单轮修复：每臂仅追加一次请求、输出上限4096 tokens，'
    '无工具、无重试、无进一步修复。允许后续消息提供两类已观测错误的有限诊断。'
    '原生成轮记录保持尝试1/修复0；本轮追加修复1。原业务需求、运行语义、ABI和源码格式要求不变。'
    '不提供参考解、具体测试输入/期望、对侧候选或修补代码。'
)
COMMON_REPAIR=(
    '这是独立记录的一轮修复机会：本轮只允许一次输出，无工具、无重试、无额外修复。'
    '原生成轮的尝试1/修复0保持原账；仅本轮追加修复1。除这一预算说明外，原需求、信息、'
    '接口、输出格式和运行语义不变。请返回修复后的完整原生源文本，不要解释、Markdown或围栏。'
    '下面仅给两类诊断；NOT_CHECKED/NOTRUN不代表通过。只使用原公开需求核查分支；'
    '不会提供固定测试输入、期望状态、参考解、另一方源代码或修复代码。'
)
DIAGNOSTICS={
    'gameir_text':[
        {'category':'format_and_source_abi','status':'PASS','message':'原完整JSON通过静态编译与源接口审查。'},
        {'category':'branch_error_semantics','status':'FAIL','message':'原固定测试中，缺失实体分支出现InvalidEntity并使后续步骤RuntimeHalted；公开需求要求缺失实体正常结束。'}],
    'js_callback':[
        {'category':'format_and_source_abi','status':'FAIL','message':'原文解析后的导出列表为空，结果EXPORTS_REJECTED；原文含Markdown围栏，不符合完整原生模块输出格式。'},
        {'category':'branch_error_semantics','status':'NOT_CHECKED','message':'原JS未求值、未调用；玩法NOTRUN。分支语义尚未完整验证，请根据原公开需求核查。'}],
}


def repair_messages(original,sources,rep,feedback):
    messages=copy.deepcopy(original.body['messages'])
    if not messages or messages[0]['role']!='system':raise EvaluationError('REPAIR_SYSTEM_CONTEXT_REJECTED')
    system=messages[0]['content']
    for old,new in SYSTEM_REPLACEMENTS:
        if system.count(old)!=1:raise EvaluationError('REPAIR_ORIGINAL_STAGE_CLAUSE_DRIFT')
        system=system.replace(old,new)
    messages[0]['content']=system+'\n\n'+REPAIR_STAGE_SYSTEM
    messages.extend([{'role':'assistant','content':sources[rep]},{'role':'user','content':feedback}])
    return messages


def build_draft():
    contract,requests=load_review_requests();sources=load_candidates()
    summary=bounded_json((RESULTS/'summary.json').read_text(encoding='utf-8'))
    if (summary['protocol_digest']!=PROTOCOL_DIGEST or summary['candidate_sha256']!=HASHES
            or summary['IR_counts']!={'PASS':18,'FAIL':8,'NOTRUN':0}
            or summary['JS_parser']['status']!='EXPORTS_REJECTED'
            or summary['JS_candidate_evaluations']!=0 or summary['blocked']
            or not summary['source_bindings_unchanged'] or not summary['all_IR_cleanup']):
        raise EvaluationError('ORIGINAL_PAIR003_RESULT_BINDING_REJECTED')
    arms=[]
    for rep,original in requests:
        feedback={'common_instruction':COMMON_REPAIR,'diagnostics':copy.deepcopy(DIAGNOSTICS[rep])}
        # Same schema/categories/maximum feedback size. Different observed error
        # values are evidence, not a handcrafted fix or artificial equal outcome.
        encoded=json.dumps(feedback,ensure_ascii=False,separators=(',',':'))
        if len(encoded.encode('utf-8'))>2048 or any(len(d['message'].encode('utf-8'))>512 for d in feedback['diagnostics']):
            raise EvaluationError('REPAIR_FEEDBACK_QUOTA_EXCEEDED')
        messages=repair_messages(original,sources,rep,encoded)
        request=prepare(messages,4096,PlanConfig(model=contract['model']),thinking={'type':'disabled'})
        if request.prompt_units>24000:raise EvaluationError('REPAIR_PROMPT_UNIT_CEILING_EXCEEDED')
        arms.append({'representation':rep,'original_source_sha256':HASHES[rep],
            'request_digest':request.request_digest,'request_body':request.body,
            'prompt_byte_units':request.prompt_units,'feedback_utf8_bytes':len(encoded.encode('utf-8')),
            'feedback':feedback,'status':'DRAFT_NOT_SENT'})
    proposal={'schema':'gir-pair003-repair-proposal@2','status':'FIXED_OFFLINE_REQUESTS_USER_LOCAL_SEND_ONLY',
        'trial':'ONE_POST_OBSERVATION_CREATE_PAIR_NOT_HOLDOUT_OR_AI_ADVANTAGE',
        'parent_generation_protocol_digest':PROTOCOL_DIGEST,'original_candidate_sha256':HASHES,
        'original_results_sha256':hashlib.sha256((RESULTS/'summary.json').read_bytes()).hexdigest(),
        'model':contract['model'],'endpoint':contract['endpoint'],'thinking':{'type':'disabled'},
        'sessions':'Continue each own original context independently; no cross-arm information.',
        'system_stage_instruction':REPAIR_STAGE_SYSTEM,'system_stage_replacements':[list(pair) for pair in SYSTEM_REPLACEMENTS],
        'original_archived_messages_modified':False,
        'incremental_budget_per_arm':{'prompt_byte_units_ceiling':24000,'max_output_tokens':4096,
            'repair_requests':1,'retries':0,'tool_calls':0,'further_repairs':0,'feedback_utf8_bytes_ceiling':2048,
            'feedback_categories':2,'diagnostic_message_utf8_bytes_ceiling':512},
        'cumulative_generation_plus_repair_caps_per_arm':{'requests':2,'repairs':1,'max_output_tokens':8192},
        'token_accounting':{'status':'COMMON_BYTE_ESTIMATE_CAP_PLUS_SEPARATE_REPORTED_TOKEN_USAGE',
            'byte_units_are_not_tokens':True,'actual_usage_may_differ':True,'padding_added':False,
            'provider_input_token_limit_claimed':False,'exact_tokenizer_required':False,
            'policy':'Each request must fit the common 24000 byte+message-overhead estimate cap. Record actual provider input/output tokens separately; no comparison of input tokens against byte units, no padding, no assertion of equal actual tokens. Output max_tokens is 4096. Missing usage or output-token overrun halts the pair; unknown usage retains the reservation.'},
        'evaluation':{'runtime_semantics':'Unchanged shared engine and original fixed oracle',
            'case_count_per_arm':26,'reuse_each_generated_source_across_original_seeds':[1,123],
            'hidden_test_or_reference_solution_in_messages':False,'original_oracle_changed':False,
            'metrics':'Report original and repaired source/compile/gameplay/contract separately, including FAIL/NOTRUN/NOT_CHECKED.',
            'generated_source_execution':'New hash needs separate static review, scope approval and fresh actual isolation; old pair003 scope does not authorize it.',
            'invalid_source':'Do not strip fences or manually repair; record failure without source execution.'},
        'stop_conditions':['Plan quota/auth/route/model mismatch or unknown usage','Either arm fails transport or accounting','Isolation or source binding failure'],
        'no_fallback':{'paid_route':False,'purchase_or_upgrade':False,'reset_cards':0},
        'arms':arms,'additional_model_calls':0,'authenticated_requests':0,'candidate_executions':0,
        'architecture':'IR not frozen; historical Vitric baseline absent; TS deferred; A12 incomplete',
        'feedback_policy_review':'PASS_FOR_LIMITED_DEVELOPMENT_TRIAL',
        'review_gates':['User-local Plan status confirmation and SEND_REPAIR confirmation; never agent credential handling']}
    proposal['proposal_digest']=digest(proposal)
    return proposal


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.output.exists():parser.error('Preserve evidence; fresh output required')
    proposal=build_draft();args.output.mkdir(parents=True,exist_ok=False)
    with (args.output/'proposal.json').open('x',encoding='utf-8') as f:
        json.dump(proposal,f,ensure_ascii=False,indent=2);f.write('\n')
    for arm in proposal['arms']:
        with (args.output/(arm['representation']+'-request-draft.json')).open('x',encoding='utf-8') as f:
            json.dump(arm['request_body'],f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({'status':proposal['status'],'proposal_digest':proposal['proposal_digest'],
        'additional_model_calls':0,'authenticated_requests':0,'candidate_executions':0},ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
