"""Run human-approved exact repair001 pair through unchanged isolated semantics."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path
import time
from headless_v01.types import Fault
from .adapter import EvaluationError,bounded_json
from .callback_verify import callback_manifest
from .candidate_eval import fresh_isolation,save
from .candidate_parse_backend import CandidateParserBackend,parse_module
from .candidate_runner import replay_candidate
from .js_verify import profile_probe
from .node_backend import NodeBackend
from .oracle import final_cases,observations
from .pair003_eval import resource_fixture
from .repair001_scope import ExactScope,FreshScopeIsolation,approved_manifest,require_JS_parse
from .repair001_review import ROOT,HASHES
from .scoped_candidate_backend import ScopedIRBackend
from .scoped_candidate_runner import ScopedJSRunner,ScopedHostRuntime
from headless_v01.compiler import digest

PREVIOUS_RUN=ROOT/'results/repair001-isolated-evaluation-20261011'
PREVIOUS_VERSION='a982e1e5d29b11d08076b75ebbe3d81306eae274'
CHANGED_CONTROLS={'ark_eval/repair001_scope.py','ark_eval/repair001_eval.py','ark_eval/tests/test_repair001_scope.py'}


def validate_completed(row,rep,case):
    """Recheck prior evidence and replay; never cache or synthesize gameplay."""
    if (row['representation']!=rep or row['case_id']!=case['id'] or row['status']!='PASS'
            or row['expected']!=case['expected'] or row['observed']!=case['expected']
            or any(row[k] is not True for k in ('oracle_match','replay_match','cleanup_verified'))):
        raise EvaluationError('CONTINUATION_CASE_REJECTED')
    audits=[row['worker_audit']] if rep=='gameir_text' else row['worker_audits']
    if not audits:raise EvaluationError('CONTINUATION_AUDIT_MISSING')
    for a in audits:
        if (a['candidate_sha256']!=HASHES[rep] or a['generated_source_executions']!=1
                or a['cleanup_errors']!=[] or a['surviving_owned_processes']!=0
                or a['network_mode']!='none' or a['readonly_rootfs'] is not True or a['user_mounts']!=[]
                or a['rootless_host']['rootless'] is not True
                or a['kernel_security']!={'CapEff':'0000000000000000','NoNewPrivs':'1','Seccomp':'2'}
                or a['kernel_limits']!={'cpu.max':'25000 100000','memory.max':'67108864','memory.swap.max':'0','pids.max':'32'}):
            raise EvaluationError('CONTINUATION_ISOLATION_REJECTED')
    if rep=='gameir_text':
        result=row['isolated_result']
        if (result['case_id']!=case['id'] or result['source_sha256']!=HASHES[rep]
                or result['observed']!=row['observed'] or result['snapshot_digest']!=digest(result['snapshot'])
                or result['replay_snapshot_digest']!=result['snapshot_digest']
                or result['replay_match'] is not True or result['host_operations_bounded'] is not True
                or result['source_modified'] is not False or result['JS_evaluations']!=0
                or result['candidate_case_executions']!=1 or result['candidate_replay_case_executions']!=1):
            raise EvaluationError('CONTINUATION_IR_REPLAY_REJECTED')
    else:
        if len(row['results'])!=len(case['steps']):raise EvaluationError('CONTINUATION_JS_STEPS_REJECTED')
        history=[{'request':step,'result':result} for step,result in zip(case['steps'],row['results'])]
        played=replay_candidate(callback_manifest(),case,history,row['execution_audits'])
        if observations(played,row['results'])!=case['expected']:
            raise EvaluationError('CONTINUATION_JS_REPLAY_REJECTED')


def load_completed(scope):
    """Only the known paused run; bind preserved bytes and unchanged semantics."""
    scope.require();raw=(PREVIOUS_RUN/'approved-both-arms-manifest.json').read_bytes();old=bounded_json(raw.decode())
    current=scope.manifest
    if ({k:v for k,v in old.items() if k!='bindings'}!={k:v for k,v in current.items() if k!='bindings'}
            or set(old['bindings'])!=set(current['bindings'])):raise EvaluationError('CONTINUATION_SCOPE_DRIFT')
    changes={}
    for name,sha in old['bindings'].items():
        if current['bindings'][name]==sha:continue
        if name not in CHANGED_CONTROLS:raise EvaluationError('CONTINUATION_SEMANTICS_CHANGED')
        blob=subprocess.run(['git','show',PREVIOUS_VERSION+':'+name],cwd=ROOT,capture_output=True,timeout=20,check=True).stdout
        if hashlib.sha256(blob).hexdigest()!=sha:raise EvaluationError('CONTINUATION_OLD_VERSION_REJECTED')
        changes[name]={'previous':sha,'current':current['bindings'][name]}
    retained={};file_hashes={}
    cases={c['id']:c for c in final_cases('P-C02-create')}
    for rep in ('gameir_text','js_callback'):
        for path in sorted(PREVIOUS_RUN.glob(rep+'-P-C02-create-*.json')):
            if path.is_symlink():raise EvaluationError('CONTINUATION_PATH_REJECTED')
            data=path.read_bytes();row=bounded_json(data.decode());case_id=row['case_id'];key=(rep,case_id)
            if case_id not in cases or key in retained or path.name!=rep+'-'+case_id+'.json':
                raise EvaluationError('CONTINUATION_DUPLICATE_OR_UNKNOWN_CASE')
            validate_completed(row,rep,cases[case_id]);retained[key]=row
            file_hashes[path.name]=hashlib.sha256(data).hexdigest()
    if {rep:sum(key[0]==rep for key in retained) for rep in HASHES}!={'gameir_text':9,'js_callback':8}:
        raise EvaluationError('CONTINUATION_COMPLETED_SET_CHANGED')
    return retained,{'previous_run':str(PREVIOUS_RUN.relative_to(ROOT)),'previous_version':PREVIOUS_VERSION,
        'previous_manifest_sha256':hashlib.sha256(raw).hexdigest(),'completed_file_sha256':file_hashes,
        'changed_control_bindings':changes,'unchanged_semantics':'All host, oracle, candidate, parser, worker, transport and scoped runner bindings identical; admission optimization and continuation bookkeeping only.',
        'old_records_overwritten':False,'old_records_counted_once':True,'interrupted_partial_JS_callback_count':'UNKNOWN_NOT_INCLUDED_IN_COMPLETED_RECORD_COUNTS'}


def require_completed_bytes(provenance):
    if hashlib.sha256((PREVIOUS_RUN/'approved-both-arms-manifest.json').read_bytes()).hexdigest()!=provenance['previous_manifest_sha256']:
        raise EvaluationError('CONTINUATION_MANIFEST_CHANGED')
    for name,sha in provenance['completed_file_sha256'].items():
        if hashlib.sha256((PREVIOUS_RUN/name).read_bytes()).hexdigest()!=sha:
            raise EvaluationError('CONTINUATION_RECORD_CHANGED')


def evaluate_ir(scope,backend,receipt,case):
    if (type(scope)is not ExactScope or type(backend)is not ScopedIRBackend
            or type(receipt)is not FreshScopeIsolation or receipt.kind!='gameir_text'
            or receipt.scope is not scope):raise EvaluationError('SCOPED_IR_BACKEND_REJECTED')
    receipt.require(backend);source=scope.source('gameir_text')
    worker=backend.invocation(10);started=time.monotonic()
    try:
        worker.open();worker.write({'v':'gir-pair003-ir@1','purpose':'ir_case','source':source,
            'case':{k:case[k] for k in ('id','initial','seed','steps')}},bootstrap=True)
        worker.audit.update(generated_source_executions=1,candidate_sha256=HASHES['gameir_text'])
        frame=bounded_json(worker.read_line())
        if set(frame)!={'transport','result'} or frame['transport']!='gir-pair003-ir-result@1':raise EvaluationError('IR_RESULT_PROTOCOL_REJECTED')
        result=frame['result']
        if (result['case_id']!=case['id'] or result['source_sha256']!=HASHES['gameir_text']
                or result['JS_evaluations']!=0 or result['source_modified'] is not False
                or result['candidate_case_executions']!=1 or result['candidate_replay_case_executions']!=1):
            raise EvaluationError('IR_RESULT_SCOPE_REJECTED')
        worker.release(bounded_json(worker.read_line()))
    finally:worker.close()
    clean=not worker.audit['cleanup_errors'] and worker.audit['surviving_owned_processes']==0
    matched=result['observed']==case['expected']
    return {'representation':'gameir_text','case_id':case['id'],
        'status':'PASS' if matched and result['replay_match'] and result['host_operations_bounded'] and clean else 'FAIL',
        'oracle_match':matched,'replay_match':result['replay_match'],'cleanup_verified':clean,
        'observed':result['observed'],'expected':case['expected'],'isolated_result':result,'worker_audit':worker.audit,
        'wall_seconds':time.monotonic()-started}


def evaluate_js(scope,backend,receipt,parsed,case):
    started=time.monotonic();runner=ScopedJSRunner(scope,backend,receipt,parsed);program=callback_manifest()
    runtime=ScopedHostRuntime(program,case['initial'],runner,seed=case['seed'])
    results=[runtime.step(**step) for step in case['steps']]
    observed=observations(runtime,results);played=replay_candidate(program,case,runtime.history,runtime.execution_history)
    clean=all(not a['cleanup_errors'] and a['surviving_owned_processes']==0 for a in runner.audits)
    rejected=any(a['terminal'] in ('SCOPED_ISOLATION_OR_TRANSPORT_REJECTED','ISOLATION_CLEANUP_FAILED') for a in runner.audits)
    if rejected:raise EvaluationError('SCOPED_JS_ISOLATION_FAILED')
    replayed=played.snapshot()==runtime.snapshot();matched=observed==case['expected']
    return {'representation':'js_callback','case_id':case['id'],'status':'PASS' if matched and replayed and clean else 'FAIL',
        'oracle_match':matched,'replay_match':replayed,'cleanup_verified':clean,'observed':observed,'expected':case['expected'],
        'results':results,'execution_audits':runtime.execution_history,'worker_audits':runner.audits,
        'wall_seconds':time.monotonic()-started}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--run-approved-repair001',action='store_true')
    p.add_argument('--continue-validated-paused-run',action='store_true');args=p.parse_args(argv)
    if args.output.exists():p.error('Preserve history; fresh execution output required')
    manifest=approved_manifest();scope=ExactScope(manifest);args.output.mkdir(parents=True,exist_ok=False)
    save(args.output,'approved-both-arms-manifest',manifest)
    retained,provenance=load_completed(scope) if args.continue_validated_paused_run else ({},{})
    if provenance:save(args.output,'continuation-provenance',provenance)
    if not args.run_approved_repair001:
        print(json.dumps({'state':'PREPARED_NO_EXECUTION','candidate_executions':0,'additional_model_calls':0}));return 0
    # Reuse trusted fixed Node checks; old FreshIsolation receipt is discarded.
    node=NodeBackend();_,node_profile,node_faults=fresh_isolation(node,args.output)
    node_receipt=FreshScopeIsolation(scope,node,node_profile,node_faults,'js_callback')
    parsed=parse_module(scope.source('js_callback'),CandidateParserBackend());save(args.output,'JS-parse-only',parsed)
    js_ready=True
    try:require_JS_parse(scope,parsed)
    except EvaluationError:js_ready=False
    print(json.dumps({'progress':'JS_actual_parse_only','status':parsed['result']['status'],'candidate_evaluations':0}),flush=True)
    ir=ScopedIRBackend(scope);save(args.output,'IR-runtime-image-payload-hashes',ir.payload_hashes)
    save(args.output,'IR-runtime-image',{'image':ir.worker_image,'worker_hash':ir.worker_hash,'base':ir.lock,
        'trusted_worker_template_config_change':'Approved SHA line only; original worker file and candidate bytes unchanged'})
    ir_profile=profile_probe(ir);save(args.output,'IR-profile-before-resource-faults',ir_profile);faults=[]
    for name in ('spin','oom','tree'):
        record=resource_fixture(ir,name);faults.append(record);save(args.output,'IR-resource-'+name,record)
        print(json.dumps({'progress':'IR_resource_fixture','fixture':name,'status':record['status']}),flush=True)
        if record['status']!='PASS' or ir.disabled:raise EvaluationError('IR_FRESH_RESOURCE_CHECK_FAILED')
    ir_profile['checks'].update(memory_limits='PASS',process_tree_termination='PASS');save(args.output,'IR-fresh-profile',ir_profile)
    ir_receipt=FreshScopeIsolation(scope,ir,ir_profile,faults,'gameir_text')
    records=[];blocked={'gameir_text':False,'js_callback':not js_ready}
    for case in final_cases('P-C02-create'):
        for rep in ('gameir_text','js_callback'):
            if (rep,case['id']) in retained:
                require_completed_bytes(provenance);row=retained[(rep,case['id'])]
            elif blocked[rep]:row={'representation':rep,'case_id':case['id'],'status':'NOTRUN','reason':'PARSE_REJECTED_OR_ISOLATION_FAILED'}
            else:
                try:row=evaluate_ir(scope,ir,ir_receipt,case) if rep=='gameir_text' else evaluate_js(scope,node,node_receipt,parsed,case)
                except (EvaluationError,Fault,OSError,ValueError,KeyError,TypeError) as e:
                    row={'representation':rep,'case_id':case['id'],'status':'NOTRUN','reason':str(e) if isinstance(e,EvaluationError) else 'BOUNDED_EXECUTION_OR_REPLAY_REJECTED'};blocked[rep]=True
            records.append(row);save(args.output,rep+'-'+case['id'],row)
            print(json.dumps({'progress':'repair001_original_case','arm':rep,'case':case['id'],'status':row['status'],
                'retained_complete_evidence':(rep,case['id']) in retained}),flush=True)
            if ir.disabled or node.disabled:blocked={r:True for r in blocked}
    save(args.output,'cases',records)
    if provenance:require_completed_bytes(provenance)
    js_audits=[a for r in records for a in r.get('worker_audits',[])];ir_audits=[r['worker_audit'] for r in records if 'worker_audit' in r]
    original=bounded_json((ROOT/'results/pair003-isolated-evaluation-20261011/summary.json').read_text(encoding='utf-8'))
    summary={'kind':'REPAIR001_EXACT_DEVELOPMENT_PAIR_ORIGINAL26_NOT_AI_ADVANTAGE','protocol_digest':manifest['protocol_digest'],
        'candidate_sha256':HASHES,'counts':{rep:{s:sum(r['representation']==rep and r['status']==s for r in records) for s in ('PASS','FAIL','NOTRUN')} for rep in blocked},
        'JS_actual_parser':parsed['result'],'JS_parser_evaluations':0,
        'IR_case_executions':len(ir_audits),'IR_case_replays':sum(r.get('isolated_result',{}).get('candidate_replay_case_executions',0) for r in records),
        'IR_handler_dispatches':sum(r.get('isolated_result',{}).get('handler_dispatches',0) for r in records),
        'JS_callback_invocations':sum(a['generated_source_executions'] for a in js_audits),
        'JS_case_transcript_replays':sum(r['representation']=='js_callback' and r.get('replay_match',False) for r in records),
        'all_replays_match':all(r.get('replay_match',False) for r in records),
        'all_workers_clean':all(not a['cleanup_errors'] and a['surviving_owned_processes']==0 for a in js_audits+ir_audits),
        'Node_fresh_profile':node_profile['checks'],'Node_fault_counts':{s:sum(f['status']==s for f in node_faults) for s in ('PASS','FAIL')},
        'IR_fresh_profile':ir_profile['checks'],'IR_fault_counts':{s:sum(f['status']==s for f in faults) for s in ('PASS','FAIL')},
        'contract_static':{'gameir_text':'NOT_CHECKED_FULL_RULES','js_callback':'FAIL_REQUIRED_OPEN_CELL_ERROR_SUPPRESSION'},
        'contract_unchecked':['effect_authorization full rule','publication_repeat_rollback full rule'],
        'baseline_original_pair003':{'IR':original['IR_counts'],'JS':original['JS_gameplay'],'JS_parser':original['JS_parser']['status']},
        'source_modified':False,'oracle_modified':False,'source_bindings_unchanged':manifest['bindings']==approved_manifest()['bindings'],
        'blocked':blocked,'additional_model_calls':0,'authenticated_requests':0,'reset_cards_used':0,'push_performed':False,
        'continuation':provenance,'retained_completed_counts':{rep:sum(k[0]==rep for k in retained) for rep in HASHES},
        'newly_executed_case_counts':{rep:sum(r['representation']==rep and 'observed' in r and (rep,r['case_id']) not in retained for r in records) for rep in HASHES},
        'limits':'One repaired Create pair, fixed original tests/seeds, post-observation development; JS contract FAIL can coexist with gameplay PASS; not strict token equality or general AI advantage'}
    save(args.output,'summary',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)
    return int(any(blocked.values()) or not summary['all_workers_clean'] or not summary['source_bindings_unchanged'])


if __name__=='__main__':
    try:raise SystemExit(main())
    except EvaluationError as e:
        print(json.dumps({'state':'BLOCKED','code':str(e),'candidate_executions':'SEE_EXISTING_PARTIAL_AUDITS','additional_model_calls':0}),flush=True);raise SystemExit(2)
