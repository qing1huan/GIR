"""Hash-bound repair001 review and pending approval request; no execution entry."""
import argparse
import hashlib
import json
from pathlib import Path
from headless_v01.compiler import compile_program,digest
from headless_v01.types import Fault
from .adapter import ACCOUNT_MODEL,EvaluationError,bounded_json,integer
from .budget import verify_chain
from .contract_assessment import make_assessment
from .manual_repair_pair003 import load_repair_requests,PROTOCOL_DIGEST
from .pair003_eval import ROOT,source_bindings
from .oracle import final_cases

BUNDLE=ROOT/'results/user-local-create-repair-001'
HASHES={'gameir_text':'a732e295362362b4e1c8b42110c4e9459473e814c67fcdda17e8d75aac6534ba',
        'js_callback':'f71eef2f8bf4298ad8e50ada9b171a4ecf94c1242e2aee943df76b5a5e39f920'}
EXPECTED_USAGE={'gameir_text':{'prompt_tokens':3555,'completion_tokens':597,'total_tokens':4152},
                'js_callback':{'prompt_tokens':3052,'completion_tokens':202,'total_tokens':3254}}
FILES=('protocol.json','summary.json','manual-entry.json',*(rep+'/'+name for rep in HASHES
    for name in ('request.json','reservation.json','audit.json','candidate.txt','contract-initial.json','contract-assessment.json')))


def artifact_hashes():
    result={}
    for name in FILES:
        p=BUNDLE/name
        if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(BUNDLE.resolve()):
            raise EvaluationError('REPAIR001_BUNDLE_PATH_REJECTED')
        if p.stat().st_size>1048576:raise EvaluationError('REPAIR001_BUNDLE_SIZE_REJECTED')
        result[name]=hashlib.sha256(p.read_bytes()).hexdigest()
    return result


def load_reviewed_bundle():
    before=artifact_hashes();contract,requests=load_repair_requests()
    def read(name):return bounded_json((BUNDLE/name).read_text(encoding='utf-8'))
    if read('protocol.json')!=contract:raise EvaluationError('REPAIR001_PROTOCOL_DRIFT')
    summary=read('summary.json');sources={}
    if (summary['protocol_digest']!=PROTOCOL_DIGEST or summary['request_attempts']!=2
            or summary['reported_model_calls']!=2 or summary['repairs']!=2
            or summary['state']!='RESPONSES_SAVED_UNREVIEWED'
            or summary['generated_source_executions']!=0 or summary['reset_cards_used']!=0
            or summary['tool_calls']!=0 or summary['automatic_retries']!=0 or summary['paid_fallback']
            or summary['original_candidate_sha256']!=contract['proposal']['original_candidate_sha256']):
        raise EvaluationError('REPAIR001_SUMMARY_REJECTED')
    metadata=read('manual-entry.json')
    if (metadata['protocol_digest']!=PROTOCOL_DIGEST or metadata['candidate_execution']!='BLOCKED'
            or metadata['entry_source_sha256']!=hashlib.sha256((ROOT/'ark_eval/manual_repair_pair003.py').read_bytes()).hexdigest()
            or metadata['key_file_reader_source_sha256']!=hashlib.sha256((ROOT/'ark_eval/local_key_file.py').read_bytes()).hexdigest()):
        raise EvaluationError('REPAIR001_ENTRY_BINDING_REJECTED')
    for index,(rep,request) in enumerate(requests):
        raw=(BUNDLE/rep/'candidate.txt').read_bytes();text=raw.decode('utf-8',errors='strict')
        audit=read(rep+'/audit.json');usage=audit['usage'];reservation=read(rep+'/reservation.json')
        for key in ('prompt_tokens','completion_tokens','total_tokens'):integer(usage[key])
        if (read(rep+'/request.json')!=request.body or audit['request_digest']!=request.request_digest
                or reservation['request_digest']!=request.request_digest or reservation['repair_requests_reserved']!=1
                or reservation['maximum_output_tokens']!=4096 or reservation['common_byte_unit_ceiling']!=24000
                or reservation['input_units_are_tokens'] is not False
                or audit['candidate_sha256']!=HASHES[rep] or hashlib.sha256(raw).hexdigest()!=HASHES[rep]
                or audit['candidate_digest']!=digest(text) or audit['candidate_bytes']!=len(raw) or not 0<len(raw)<=16384
                or audit['candidate_file']!='candidate.txt' or audit['usage']!=EXPECTED_USAGE[rep]
                or usage['total_tokens']!=usage['prompt_tokens']+usage['completion_tokens'] or usage['completion_tokens']>4096
                or audit['status']!='CANDIDATE_RECORDED_UNREVIEWED' or audit['failure'] is not None
                or audit['http_status']!=200 or audit['finish_reason']!='stop' or audit['request_attempts']!=1
                or audit['repairs']!=1 or audit['tool_calls']!=0 or audit['reported_model_calls']!=1
                or audit['execution_status']!='BLOCKED' or audit['generated_source_executions']!=0
                or audit['model_matches_requested'] is not True or audit['model_identity_digest']!=digest(ACCOUNT_MODEL)
                or audit['prompt_byte_units_estimate']!=request.prompt_units or audit['prompt_byte_units_ceiling']!=24000
                or audit['input_units_are_tokens'] is not False or audit['input_padding_added'] is not False
                or len(audit['ledger_events'])!=2 or not verify_chain(audit['ledger_events'])
                or audit['ledger_events'][0]['request_digest']!=request.request_digest
                or audit['ledger_events'][1]['usage']!=usage):raise EvaluationError('REPAIR001_ARM_BINDING_REJECTED')
        row=summary['arms'][index]
        if (row['representation']!=rep or row['usage']!=usage or row['execution_status']!='BLOCKED'
                or row['gameplay_status']!='NOTRUN' or row['contract_status']!='NOT_CHECKED'):
            raise EvaluationError('REPAIR001_ARM_SUMMARY_REJECTED')
        for filename,sha in [('contract-initial.json',None),('contract-assessment.json',HASHES[rep])]:
            if read(rep+'/'+filename)!=make_assessment(representation=rep,source_sha256=sha,protocol_digest=PROTOCOL_DIGEST):
                raise EvaluationError('REPAIR001_INITIAL_ASSESSMENT_DRIFT')
        sources[rep]=text
    if artifact_hashes()!=before:raise EvaluationError('REPAIR001_BUNDLE_CHANGED_DURING_REVIEW')
    return sources,summary,before


def static_findings():
    sources,summary,before=load_reviewed_bundle()
    try:core=compile_program(bounded_json(sources['gameir_text']))
    except Fault as e:raise EvaluationError('REPAIR001_IR_STATIC_COMPILATION_REJECTED') from None
    # These are manually reviewed findings for ONLY the two exact hashes above.
    # This is not a generic scanner granting permission to new programs.
    return {'schema':'gir-repair001-static-findings@1','protocol_digest':PROTOCOL_DIGEST,
        'candidate_sha256':dict(HASHES),'bundle15_sha256':before,'usage':EXPECTED_USAGE,
        'model_identity':'USER_LOCAL_REPORTED_IDENTIFIER_MATCH_NOT_PROVIDER_ROUTE_ATTESTATION',
        'gameir_text':{'static_compile':'PASS','compiled_core_digest':digest(core),
            'source_abi':'PASS_STATIC_REVIEW','prior_missing_entity_defect':'FIXED_IN_STATIC_CONTROL_FLOW',
            'reason':'pOpt and dOpt are checked with is_some before require; empty else has no effects. door.open is required only after both exist; keys is required only when closed.',
            'branch_error_semantics':'PASS_STATIC_REVIEW','effects_and_repeat_rollback':'NOT_CHECKED',
            'gameplay':{'PASS':0,'FAIL':0,'NOTRUN':26}},
        'js_callback':{'source_abi':'PASS_TEXT_STATIC_REVIEW','native_export_text':'export function onOpen',
            'fences_present':False,'actual_module_parser':'NOTRUN','finite_control_flow':'MANUALLY_REVIEWED_EXACT_TEXT',
            'host_methods':['resolve','require','read','sub','write','emit'],
            'file_network_process_import_eval_dynamic_load':'ABSENT_IN_EXACT_REVIEWED_TEXT',
            'prior_fence_defect':'FIXED_IN_TEXT_FORMAT_NOT_YET_ACTUAL_PARSER_EVIDENCE',
            'branch_error_semantics':'FAIL_STATIC_COUNTEREXAMPLE',
            'counterexample':'Both entities exist, but the door open cell is absent. The source guards on some in openCell and has no else: it finishes normally without require(none), suppressing the mandatory-cell InvalidEntity behavior.',
            'public_policy':'branch_error_semantics: missing required cell errors must be preserved; C05/N03 describe require(none) as InvalidEntity. Door open is the required state determining the business branch.',
            'effects_and_repeat_rollback':'NOT_CHECKED','gameplay':{'PASS':0,'FAIL':0,'NOTRUN':26},
            'runtime_request':'NOT_REQUESTED_KNOWN_STATIC_CONTRACT_VIOLATION'},
        'scope':'ONE_CREATE_DEVELOPMENT_PAIR_ONE_REPAIR_PER_ARM_NOT_HOLDOUT_OR_AI_ADVANTAGE',
        'candidate_executions':0,'actual_JS_parses':0,'additional_model_calls':0,'authenticated_requests':0,
        'reset_cards_used':0,'source_modified':False,'oracle_modified':False}


def review_bindings():
    bindings=source_bindings()
    for name in ('ark_eval/create_repair_pair003.freeze.json','ark_eval/manual_repair_pair003.py',
                 'ark_eval/local_key_file.py','ark_eval/pair003_repair_draft.py',
                 'ark_eval/repair001_review.py','ark_eval/tests/test_repair001_review.py'):
        bindings[name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    return bindings


def approval_request():
    findings=static_findings();cases=final_cases('P-C02-create')
    return {'schema':'gir-repair001-approval-request@1','state':'PENDING_HUMAN_APPROVAL_NO_EXECUTION',
        'protocol_digest':PROTOCOL_DIGEST,'candidate_sha256':dict(HASHES),
        'bundle15_sha256':findings['bundle15_sha256'],'bindings':review_bindings(),
        'human_approval_received':False,'execution_authorized':False,
        'requested':{'gameir_text':{'source_sha256':HASHES['gameir_text'],'fixed_cases':26,'seeds':[1,123],
                'original_case_ids':[c['id'] for c in cases],'fixed_oracle_digest':digest(cases),'replay':True},
            'js_callback':{'source_sha256':HASHES['js_callback'],'runtime_requested':False,
                'reason':'Known static mandatory-open-cell error suppression; do not create unnecessary execution.'}},
        'fresh_isolation_required':{'rootless':True,'network':'none','readonly_rootfs':True,'user_mounts':[],
            'memory_bytes':67108864,'swap_bytes':0,'cpu_quota':'25000 100000','pids':32,
            'capabilities':'ALL_DROPPED','no_new_privileges':True,'seccomp':2,
            'wall_seconds_per_case':10,'host_operations_per_tick':128,
            'checks':['actual kernel/profile','trusted fixed timeout/OOM/process-tree faults','bounded output','owned cleanup']},
        'implementation':'Reuse unchanged engine/oracle, NodeBackend/WorkerProcess, Python payload packaging and fixed isolation probes. Add only a manifest-scoped hash adapter after approval; leave old pair002/pair003 exact-source gates unchanged.',
        'new_executable_adapter':'NOT_IMPLEMENTED_OR_ACTIVE_BEFORE_APPROVAL',
        'risks':'Generated source remains untrusted; prior isolation proof must be refreshed. Any drift, isolation failure or cleanup failure halts remaining cases; unknown results stay NOTRUN.',
        'not_checked':['new IR gameplay','new JS actual parsing and gameplay','effect_authorization full rule','publication/repeat/rollback full rule'],
        'old_approval_reused':False,'source_modified':False,'oracle_modified':False,
        'candidate_executions':0,'additional_model_calls':0,'reset_cards_used':0,'push_performed':False}


def validate_pending_request(manifest):
    if manifest!=approval_request():raise EvaluationError('REPAIR001_APPROVAL_REQUEST_DRIFT')
    return True


def require_execution_approval(manifest):
    validate_pending_request(manifest)
    # JSON flags are not human authorization. There is no candidate executor.
    raise EvaluationError('REPAIR001_HUMAN_APPROVAL_PENDING')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.output.exists():parser.error('Preserve evidence; fresh review output required')
    findings=static_findings();manifest=approval_request();args.output.mkdir(parents=True,exist_ok=False)
    for name,value in [('static-findings',findings),('approval-request-manifest',manifest)]:
        with (args.output/(name+'.json')).open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
    evidence={'reference':(args.output/'static-findings.json').as_posix(),
              'sha256':hashlib.sha256((args.output/'static-findings.json').read_bytes()).hexdigest()}
    for rep in HASHES:
        receipts={}
        for rule,status,coverage in [('source_abi','PASS','FULL_RULE'),
                ('branch_error_semantics','PASS' if rep=='gameir_text' else 'FAIL',
                 'FULL_RULE' if rep=='gameir_text' else 'COUNTEREXAMPLE')]:
            receipts[rule]={'status':status,'method':'STATIC_REVIEW','coverage':coverage,
                'origin':'EVALUATOR_VERIFIED','representation':rep,'source_sha256':HASHES[rep],
                'protocol_digest':PROTOCOL_DIGEST,'evidence':[evidence]}
        report=make_assessment(representation=rep,source_sha256=HASHES[rep],protocol_digest=PROTOCOL_DIGEST,receipts=receipts)
        with (args.output/(rep+'-static-contract-assessment.json')).open('x',encoding='utf-8') as f:
            json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({'state':manifest['state'],'candidate_sha256':HASHES,'IR':'STATIC_COMPILE_PASS_GAMEPLAY_NOTRUN',
        'JS':'STATIC_CONTRACT_FAIL_GAMEPLAY_NOTRUN','candidate_executions':0,'additional_model_calls':0},ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
