"""Small reporting schema, not a source analyser or a new execution/trace system.

Only trusted evaluator-reviewed evidence records may mark a rule checked. Model
outputs/claims never supply this input. Missing, partial or unverified evidence
stays NOT_CHECKED; gameplay PASS is never contract PASS by implication.
"""
import copy
import re
from .adapter import EvaluationError

RULES = (
    {'id':'source_abi','requirement':'既定表示输出格式、事件输入绑定、允许的宿主 API；JS 自然局部条件语义允许。',
     'methods':['STATIC_REVIEW','COMPILER_OR_CTX_EVIDENCE']},
    {'id':'branch_error_semantics','requirement':'不存在实体/已开门应正常结束；不得因提前 require 本应跳过的 cell 产生错误。缺失必需 cell 的错误保留。',
     'methods':['STATIC_REVIEW','EXECUTION_TRACE_REVIEW','COMPILER_OR_CTX_EVIDENCE']},
    {'id':'effect_authorization','requirement':'仅满足业务条件才扣钥匙/开门/发相应事件；写入、checked 数值与事件 payload 满足宿主规则，不能主动发保留事件。',
     'methods':['STATIC_REVIEW','EXECUTION_TRACE_REVIEW','COMPILER_OR_CTX_EVIDENCE']},
    {'id':'publication_repeat_rollback','requirement':'规定事件次数、重复 Open 不再扣钥匙、错误整 tick 回滚；按规则覆盖范围独立检查。',
     'methods':['STATIC_REVIEW','EXECUTION_TRACE_REVIEW','COMPILER_OR_CTX_EVIDENCE']},
)
STATUSES=('PASS','FAIL','NOT_CHECKED')

def assessment_policy():
    return {'schema':'gir-contract-assessment-policy@1','rules':copy.deepcopy(list(RULES)),
        'gameplay':'UNCHANGED_FIXED_ORACLE_RESULT_ONLY',
        'contract':'SEPARATE_RULE_EVIDENCE_NO_INFERENCE_FROM_GAMEPLAY',
        'aggregate':'FAIL if any evidenced violation; otherwise NOT_CHECKED if any required rule not fully checked; otherwise PASS.',
        'necessary_order':'Only causal guards/error/effect timing are hard requirements. No exact syntax/host-call-count equality criterion.',
        'style':'Redundant safe resolve/read within budgets is diagnostic cost, not gameplay failure; native JS int truthiness is allowed.',
        'evidence_boundary':'Trusted evaluator must inspect actual evidence before supplying EVALUATOR_VERIFIED receipts. This schema records that verdict; it does not independently verify source/trace facts.',
        'positive_coverage':'PASS requires FULL_RULE coverage. A single evidenced counterexample may FAIL; partial passing observations remain NOT_CHECKED.',
        'checkers_implemented':False,'current_model_evidence':'NONE_V3_1_UNSENT',
        'checks_not_automatically_executed':['source_abi','branch_error_semantics','effect_authorization','publication_repeat_rollback'],
        'uncovered_by_existing_oracle_alone':['host_access_order','allowed_API_usage','missing_open_cell','player_door_alias','all_source_paths'],
        'sources':['ark_eval/oracle.py:19','ark_eval/candidate_eval.py:47','ark_eval/build_corpus.py:18','ark_eval/host.py:275']}

def make_assessment(gameplay='NOTRUN', *, representation, source_sha256=None,
                    protocol_digest, receipts=None):
    """Serialize trusted evaluator findings only; no reads, execution or checker.

    receipts is an evaluator-owned map, NOT a candidate-produced data object.
    Evidence descriptors bind an existing reviewed artifact, but validating their
    content is the evaluator's responsibility. There is no import/API path here.
    """
    if gameplay not in ('PASS','FAIL','NOTRUN') or representation not in ('gameir_text','js_callback'):
        raise EvaluationError('CONTRACT_REPORT_INVALID_CONTEXT')
    def sha(value):return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None
    if not sha(protocol_digest) or source_sha256 is not None and not sha(source_sha256):
        raise EvaluationError('CONTRACT_REPORT_INVALID_BINDING')
    receipts={} if receipts is None else receipts
    names={rule['id'] for rule in RULES}
    if not isinstance(receipts,dict) or not set(receipts)<=names:
        raise EvaluationError('CONTRACT_REPORT_UNKNOWN_RULE')
    records=[]
    for rule in RULES:
        row={'rule_id':rule['id'],'status':'NOT_CHECKED','method':None,'evidence':[],
             'coverage':'NONE','reason':'NO_EVALUATOR_VERIFIED_EVIDENCE'}
        record=receipts.get(rule['id'])
        if record is not None:
            keys={'status','method','evidence','coverage','origin','representation','source_sha256','protocol_digest'}
            if not isinstance(record,dict) or set(record)!=keys or record['status'] not in STATUSES:
                raise EvaluationError('CONTRACT_REPORT_INVALID_RECEIPT')
            if (record['representation']!=representation or record['source_sha256']!=source_sha256
                    or record['protocol_digest']!=protocol_digest):
                raise EvaluationError('CONTRACT_REPORT_EVIDENCE_BINDING_MISMATCH')
            if (record['method'] not in rule['methods']+[None]
                    or record['coverage'] not in ('NONE','PARTIAL','FULL_RULE','COUNTEREXAMPLE')
                    or record['origin'] not in ('EVALUATOR_VERIFIED','MODEL_CLAIM','UNVERIFIED')
                    or not isinstance(record['evidence'],list)):
                raise EvaluationError('CONTRACT_REPORT_INVALID_RECEIPT')
            for item in record['evidence']:
                if (not isinstance(item,dict) or set(item)!={'reference','sha256'} or not sha(item['sha256'])
                        or not isinstance(item['reference'],str) or not 1<=len(item['reference'])<=512):
                    raise EvaluationError('CONTRACT_REPORT_INVALID_EVIDENCE_DESCRIPTOR')
            verified=(record['origin']=='EVALUATOR_VERIFIED' and source_sha256 is not None
                      and record['method'] is not None and bool(record['evidence']))
            covered=record['coverage']=='FULL_RULE' if record['status']=='PASS' else record['coverage'] in ('FULL_RULE','COUNTEREXAMPLE')
            if verified and covered and record['status']!='NOT_CHECKED':
                row.update(status=record['status'],method=record['method'],evidence=copy.deepcopy(record['evidence']),
                           coverage=record['coverage'],reason='RECORDED_EVALUATOR_VERDICT_NOT_AUTOMATIC_FACT_VERIFICATION')
            elif verified and record['coverage']=='PARTIAL':
                row.update(method=record['method'],evidence=copy.deepcopy(record['evidence']),
                           coverage='PARTIAL',reason='PARTIAL_OBSERVATION_NOT_FULL_RULE_CHECK')
        records.append(row)
    statuses={row['status'] for row in records}
    aggregate='FAIL' if 'FAIL' in statuses else 'NOT_CHECKED' if 'NOT_CHECKED' in statuses else 'PASS'
    return {'schema':'gir-contract-assessment@1','representation':representation,
        'source_sha256':source_sha256,'protocol_digest':protocol_digest,
        'gameplay_status':gameplay,'contract_status':aggregate,'rules':records,
        'checked_rule_ids':[row['rule_id'] for row in records if row['status']!='NOT_CHECKED'],
        'not_checked_rule_ids':[row['rule_id'] for row in records if row['status']=='NOT_CHECKED'],
        'schema_only':True,'evidence_verification':'EVALUATOR_RESPONSIBILITY_NO_AUTOMATIC_FACT_CHECK',
        'style_observations_affect_gameplay':False,'native_JS_truthiness_allowed':True}
