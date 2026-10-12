"""Exact user-approved pair003 IR26 and raw JS parse only. No model transport.

Old pair002 execution gates stay unchanged. Original source/oracle never written,
JS never evaluated or stripped, IR uses independent fresh verified OS isolation.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import time

from headless_v01.compiler import digest
from headless_v01.types import Fault
from .adapter import EvaluationError,bounded_json
from .candidate_eval import fresh_isolation
from .candidate_parse_backend import CandidateParserBackend,parse_module
from .js_verify import profile_probe
from .manual_pair_v3_1 import load_review_requests,PROTOCOL_DIGEST,ROOT
from .node_backend import NodeBackend
from .oracle import final_cases
from .pair003_ir_backend import Pair003IRBackend

BUNDLE=ROOT/'results/user-local-create-pair-003'
HASHES={'gameir_text':'9cd7ea46ba86790a5413c78e5bd5846c538b75cfb65f6713c5596a47f1e87b42',
        'js_callback':'b682f563d87f28ce68064407ed7ab4094a7b1ee1881621fc3d248eb97ec85440'}
NEW_FILES=('ark_eval/pair003_eval.py','ark_eval/pair003_ir_backend.py','ark_eval/pair003_ir_worker.py',
           'ark_eval/pair003_ir_shim.mjs','ark_eval/tests/test_pair003_eval.py')


def source_bindings():
    contract,_=load_review_requests()
    names=set(contract['artifact_hashes'])|set(NEW_FILES)|{
        'ark_eval/create_pair.v3_1.review.freeze.json','ark_eval/candidate_parse_backend.py',
        'ark_eval/candidate_parser.mjs','ark_eval/candidate_eval.py','ark_eval/candidate_review.py',
        'ark_eval/candidate_runner.py','ark_eval/js_verify.py','ark_eval/js_runner.py',
        'ark_eval/bridge.py','ark_eval/contract_assessment.py'}
    return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in sorted(names)}


def load_candidates():
    contract,requests=load_review_requests()
    if bounded_json((BUNDLE/'protocol.json').read_text(encoding='utf-8'))!=contract:
        raise EvaluationError('PAIR003_PROTOCOL_DRIFT')
    sources={}
    for rep,request in requests:
        folder=BUNDLE/rep
        for name in ('candidate.txt','request.json','audit.json'):
            p=folder/name
            if p.is_symlink() or not p.resolve().is_relative_to(BUNDLE.resolve()):raise EvaluationError('PAIR003_SOURCE_PATH_REJECTED')
        if bounded_json((folder/'request.json').read_text(encoding='utf-8'))!=request.body:raise EvaluationError('PAIR003_REQUEST_DRIFT')
        path=folder/'candidate.txt'
        if not path.is_file() or not 0<path.stat().st_size<=16384:raise EvaluationError('PAIR003_SOURCE_SIZE')
        raw=path.read_bytes();text=raw.decode('utf-8',errors='strict')
        audit=bounded_json((folder/'audit.json').read_text(encoding='utf-8'))
        if (hashlib.sha256(raw).hexdigest()!=HASHES[rep] or audit['candidate_sha256']!=HASHES[rep]
                or audit['candidate_digest']!=digest(text) or audit['candidate_bytes']!=len(raw)
                or audit['request_digest']!=request.request_digest):raise EvaluationError('PAIR003_SOURCE_DRIFT')
        sources[rep]=text
    return sources


def authorization():
    load_candidates()
    return {'schema':'gir-pair003-fixed-run-authorization@1','generation_protocol_digest':PROTOCOL_DIGEST,
        'candidate_sha256':dict(HASHES),'bindings':source_bindings(),
        'human_scope':'IR_EXACT_26_AND_JS_RAW_PARSE_ONLY','IR_runtime_authorized':True,
        'JS_parse_authorized':True,'JS_evaluation_authorized':False,'fence_stripping_authorized':False,
        'source_modified':False,'oracle_modified':False,'repairs':0,'additional_model_calls':0,
        'basis':'User ok to original IR26 and raw JS parse only; parent delegation 2026-10-11.',
        'fresh_isolation_required':True,'old_pair002_authorization_used':False}


def validate_authorization(manifest):
    if manifest!=authorization():raise EvaluationError('PAIR003_FIXED_AUTHORIZATION_DRIFT')
    return load_candidates()


def resource_fixture(backend,name):
    worker=backend.invocation(2 if name in ('spin','tree') else 10);fault=None;exit_code=None
    try:
        worker.open();worker.write({'v':'gir-pair003-ir@1','purpose':'ir_resource_fixture','fixture':name},bootstrap=True)
        frame=bounded_json(worker.read_line())
        if name=='oom':
            exit_code=frame.get('code');worker.release(frame,require_zero=False)
        else:raise EvaluationError('EXPECTED_TIMEOUT_NOT_OBSERVED')
    except Fault as e:fault=e.code
    finally:worker.close()
    a=worker.audit;clean=not a['cleanup_errors'] and a['surviving_owned_processes']==0
    if name=='oom':
        passed=(exit_code==137 and a['final_memory_events']['oom_kill']>a['initial_memory_events']['oom_kill'] and clean)
    else:passed=(fault=='JS_TIMEOUT' and clean and (name!='tree' or a['terminated_tree_processes']>=4))
    return {'fixture':name,'status':'PASS' if passed else 'FAIL','fault':fault,'exit_code':exit_code,'worker_audit':a,'candidate_executions':0}


class FreshIRIsolation:
    def __init__(self,backend,profile,faults):
        required={'rootless','network_denied','no_user_mounts','environment_scrubbed','cpu_limits','memory_limits','process_tree_termination'}
        if (type(backend)is not Pair003IRBackend or backend.disabled or set(profile['checks'])!=required
                or any(v!='PASS' for v in profile['checks'].values()) or {f['fixture'] for f in faults}!={'spin','oom','tree'}
                or any(f['status']!='PASS' for f in faults)):
            raise EvaluationError('FRESH_IR_ISOLATION_REJECTED')
        self.backend=backend;self.image=backend.worker_image;self.worker_hash=backend.worker_hash
        self.bindings=source_bindings();self.created=time.monotonic()

    def require(self,backend):
        if (backend is not self.backend or backend.disabled or backend.worker_image!=self.image
                or backend.worker_hash!=self.worker_hash or source_bindings()!=self.bindings
                or time.monotonic()-self.created>600):raise EvaluationError('FRESH_IR_ISOLATION_STALE')


def evaluate_ir(manifest,backend,receipt,case):
    if type(backend)is not Pair003IRBackend or type(receipt)is not FreshIRIsolation:
        raise EvaluationError('IR_BACKEND_OR_RECEIPT_REJECTED')
    receipt.require(backend);source=validate_authorization(manifest)['gameir_text']
    worker=backend.invocation(10);result=None;started=time.monotonic()
    try:
        worker.open()
        worker.write({'v':'gir-pair003-ir@1','purpose':'ir_case','source':source,
                      'case':{k:case[k] for k in ('id','initial','seed','steps')}},bootstrap=True)
        worker.audit['generated_source_executions']=1
        worker.audit['candidate_sha256']=HASHES['gameir_text']
        frame=bounded_json(worker.read_line())
        if set(frame)!={'transport','result'} or frame['transport']!='gir-pair003-ir-result@1':raise EvaluationError('IR_RESULT_PROTOCOL_REJECTED')
        result=frame['result']
        if (result['case_id']!=case['id'] or result['source_sha256']!=HASHES['gameir_text']
                or result['JS_evaluations']!=0 or result['source_modified'] is not False
                or result['candidate_case_executions']!=1 or result['candidate_replay_case_executions']!=1):
            raise EvaluationError('IR_RESULT_BINDING_REJECTED')
        worker.release(bounded_json(worker.read_line()))
    finally:worker.close()
    cleanup=not worker.audit['cleanup_errors'] and worker.audit['surviving_owned_processes']==0
    matched=result['observed']==case['expected']
    return {'representation':'gameir_text','case_id':case['id'],
        'status':'PASS' if matched and result['replay_match'] and result['host_operations_bounded'] and cleanup else 'FAIL',
        'oracle_match':matched,'replay_match':result['replay_match'],'cleanup_verified':cleanup,
        'observed':result['observed'],'expected':case['expected'],'isolated_result':result,
        'worker_audit':worker.audit,'wall_seconds':time.monotonic()-started}


def save(output,name,value):
    with (output/(name+'.json')).open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--run-approved-ir-and-js-parse',action='store_true')
    args=parser.parse_args(argv)
    if args.output.exists():parser.error('Preserve evidence; fresh output required')
    manifest=authorization();args.output.mkdir(parents=True,exist_ok=False);save(args.output,'authorization-manifest',manifest)
    if not args.run_approved_ir_and_js_parse:
        print(json.dumps({'status':'PREPARED_NO_EXECUTION','candidate_executions':0,'additional_model_calls':0}));return 0
    # Parser shares the unchanged Node transport/kernel checks. Fresh reviewed
    # fixed Node fixtures run first; the new candidate is never evaluated.
    node=NodeBackend();_,node_profile,node_faults=fresh_isolation(node,args.output)
    if node.disabled:raise EvaluationError('NODE_ISOLATION_DISABLED')
    sources=validate_authorization(manifest)
    parsed=parse_module(sources['js_callback'],CandidateParserBackend());save(args.output,'JS-raw-parse-only',parsed)
    print(json.dumps({'progress':'JS_raw_parse_only','status':parsed['result']['status'],'candidate_evaluations':0}),flush=True)
    backend=Pair003IRBackend();save(args.output,'IR-runtime-image-payload-hashes',backend.payload_hashes)
    profile=profile_probe(backend);save(args.output,'IR-profile-before-resource-faults',profile)
    faults=[]
    for name in ('spin','oom','tree'):
        record=resource_fixture(backend,name);faults.append(record);save(args.output,'IR-resource-'+name,record)
        print(json.dumps({'progress':'IR_resource_fixture','fixture':name,'status':record['status']}),flush=True)
        if record['status']!='PASS' or backend.disabled:raise EvaluationError('IR_RESOURCE_ISOLATION_FAILED')
    by_name={f['fixture']:f for f in faults}
    profile['checks'].update(memory_limits=by_name['oom']['status'],process_tree_termination=by_name['tree']['status'])
    save(args.output,'IR-fresh-profile',profile);receipt=FreshIRIsolation(backend,profile,faults)
    save(args.output,'IR-runtime-image',{'image':backend.worker_image,'worker_hash':backend.worker_hash,'base':backend.lock})
    records=[];blocked=False
    for case in final_cases('P-C02-create'):
        if blocked:record={'representation':'gameir_text','case_id':case['id'],'status':'NOTRUN','reason':'ISOLATION_FAILED_OR_DRIFT'}
        else:
            try:record=evaluate_ir(manifest,backend,receipt,case)
            except (EvaluationError,Fault,OSError,ValueError,KeyError,TypeError):
                record={'representation':'gameir_text','case_id':case['id'],'status':'NOTRUN','reason':'ISOLATION_FAILED_OR_DRIFT'};blocked=True
        records.append(record);save(args.output,'case-'+case['id'],record)
        print(json.dumps({'progress':'original_IR_case','case':case['id'],'status':record['status']}),flush=True)
        if backend.disabled:blocked=True
    save(args.output,'IR-cases',records)
    summary={'kind':'PAIR003_ORIGINAL_DEVELOPMENT_TRIAL_NOT_AI_ADVANTAGE','protocol_digest':PROTOCOL_DIGEST,
        'candidate_sha256':HASHES,'IR_counts':{s:sum(r['status']==s for r in records) for s in ('PASS','FAIL','NOTRUN')},
        'IR_isolated_case_executions':sum('isolated_result' in r for r in records),
        'IR_replay_case_executions':sum(r.get('isolated_result',{}).get('candidate_replay_case_executions',0) for r in records),
        'IR_actual_handler_dispatches':sum(r.get('isolated_result',{}).get('handler_dispatches',0) for r in records),
        'JS_parser':parsed['result'],'JS_gameplay':{'PASS':0,'FAIL':0,'NOTRUN':26},'JS_candidate_evaluations':0,
        'contract_status_from_static_review':{'gameir_text':'FAIL','js_callback':'FAIL'},
        'contract_uncertain_rules':'Remain NOT_CHECKED unless independently evidenced; gameplay PASS is not contract PASS.',
        'IR_fresh_profile':profile['checks'],'IR_resource_fault_counts':{s:sum(f['status']==s for f in faults) for s in ('PASS','FAIL')},
        'Node_fresh_profile':node_profile['checks'],'Node_fixed_fault_counts':{s:sum(f['status']==s for f in node_faults) for s in ('PASS','FAIL')},
        'all_IR_replays':all(r.get('replay_match',False) for r in records),
        'all_IR_cleanup':all(r.get('cleanup_verified',False) for r in records),
        'source_bindings_unchanged':source_bindings()==manifest['bindings'],
        'IR_wall_seconds':sum(r.get('wall_seconds',0) for r in records),
        'source_modified':False,'oracle_modified':False,'JS_fences_stripped':False,
        'authenticated_requests':0,'additional_model_calls':0,'repairs':0,'reset_cards_used':0,'push_performed':False,
        'A12':'NOT_COMPLETE_ONE_POST_OBSERVATION_CREATE_PAIR','blocked':blocked}
    save(args.output,'summary',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)
    return int(blocked or not summary['source_bindings_unchanged'] or not summary['all_IR_cleanup'])


if __name__=='__main__':
    try:raise SystemExit(main())
    except EvaluationError as e:
        print(json.dumps({'status':'BLOCKED','code':str(e),'additional_model_calls':0}),flush=True);raise SystemExit(2)
