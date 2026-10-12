"""Review and evaluate only unchanged pair002 candidates, with no model transport."""
import argparse
import json
from pathlib import Path
import sys
import time

from headless_v01.types import Fault
from .adapter import EvaluationError
from .callback_verify import callback_manifest
from .candidate_parse_backend import CandidateParserBackend,parse_module
from .candidate_review import static_review,load_candidates,approval_manifest,version_bindings
from .candidate_runner import FreshIsolation,ApprovedCandidateJSRunner,CandidateHostRuntime,replay_candidate
from .js_verify import profile_probe,fault_case
from .node_backend import NodeBackend
from .oracle import final_cases,observations

FAULT_NAMES=('throw_after_write','spin_after_write','oom_after_write','disconnect','unknown_op',
 'injected_key','oversized_line','stdout_flood','stderr_flood','duplicate_key','swallow_host_error','tree_timeout')

def save(output,name,value):
    with (output/(name+'.json')).open('x',encoding='utf-8') as handle:
        json.dump(value,handle,ensure_ascii=False,indent=2);handle.write('\n')


def collect_isolation_evidence(backend,output):
    """Common safety checks only; each caller still constructs its own bound receipt."""
    profile=profile_probe(backend);faults=[]
    save(output,'fresh-profile-before-faults',profile)
    for name in FAULT_NAMES:
        fault=fault_case(backend,name);faults.append(fault)
        save(output,'fault-'+name,fault)
        print(json.dumps({'progress':'fresh_fixed_isolation_fault','name':name,'status':fault['status']}),flush=True)
        if backend.disabled:raise EvaluationError('CANDIDATE_ISOLATION_DISABLED')
    by_name={f['fixture']:f for f in faults}
    profile['checks']['memory_limits']=by_name['oom_after_write']['status']
    profile['checks']['process_tree_termination']=by_name['tree_timeout']['status']
    save(output,'fresh-profile',profile)
    return profile,faults


def fresh_isolation(backend,output):
    profile,faults=collect_isolation_evidence(backend,output)
    receipt=FreshIsolation(backend,profile,faults)
    return receipt,profile,faults


def evaluate_js(manifest,backend,receipt,case):
    started=time.monotonic();program=callback_manifest()
    runner=ApprovedCandidateJSRunner(manifest,backend,receipt)
    runtime=CandidateHostRuntime(program,case['initial'],runner,seed=case['seed'])
    results=[runtime.step(**step) for step in case['steps']]
    observed=observations(runtime,results);matched=observed==case['expected']
    played=replay_candidate(program,case,runtime.history,runtime.execution_history)
    replayed=played.snapshot()==runtime.snapshot()
    clean=all(not a['cleanup_errors'] and a['surviving_owned_processes']==0 for a in runner.audits)
    return {'representation':'js_callback','case_id':case['id'],
            'status':'PASS' if matched and replayed and clean else 'FAIL',
            'oracle_match':matched,'replay_match':replayed,'cleanup_verified':clean,
            'observed':observed,'expected':case['expected'],'results':results,
            'execution_audits':runtime.execution_history,'worker_audits':runner.audits,
            'wall_seconds':time.monotonic()-started}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--run-approved-pair002',action='store_true')
    args=parser.parse_args(argv)
    if args.output.exists():parser.error('Preserve prior evidence; choose a fresh directory')
    args.output.mkdir(parents=True,exist_ok=False)
    review=static_review();save(args.output,'static-review',review)
    if not args.run_approved_pair002:
        print(json.dumps({'status':'REVIEW_ONLY_NO_EXECUTION','IR':'REJECTED_STATIC_COMPILATION',
                         'JS':'PENDING_ACTUAL_PARSE_AND_ISOLATION','authenticated_requests':0,'candidate_executions':0}))
        return 0
    bindings=version_bindings();backend=NodeBackend()
    receipt,profile,faults=fresh_isolation(backend,args.output)
    sources,_=load_candidates()
    parsed=parse_module(sources['js_callback'],CandidateParserBackend());save(args.output,'JS-parse-only',parsed)
    manifest=approval_manifest(parsed);save(args.output,'approval-manifest',manifest)
    if bindings!=version_bindings():raise EvaluationError('CANDIDATE_SOURCE_OR_RUNNER_DRIFT')
    cases=final_cases('P-C02-create');records=[];blocked=False
    for case in cases:
        # Static rejection is a candidate failure; semantic runtime never ran.
        records.append({'representation':'gameir_text','case_id':case['id'],'status':'FAIL',
                        'reason':'STATIC_COMPILATION_REJECTED','runtime_status':'NOTRUN',
                        'fault':review['gameir_text']['compiler_fault'],'source_executions':0})
        if blocked:
            record={'representation':'js_callback','case_id':case['id'],'status':'NOTRUN','reason':'ISOLATION_FAILED_OR_DRIFT'}
        else:
            try:record=evaluate_js(manifest,backend,receipt,case)
            except (EvaluationError,Fault):
                record={'representation':'js_callback','case_id':case['id'],'status':'NOTRUN','reason':'ISOLATION_FAILED_OR_DRIFT'}
                blocked=True
            if backend.disabled:blocked=True
        records.append(record);save(args.output,'case-'+case['id'],records[-2:])
        print(json.dumps({'progress':'original_candidate_case','case':case['id'],'JS':record['status'],'IR':'FAIL_STATIC_REJECTION'}),flush=True)
    save(args.output,'cases',records)
    audits=[a for r in records for a in r.get('worker_audits',[])]
    summary={'kind':'ONE_DEVELOPMENT_PAIR_ORIGINAL_CANDIDATES_NOT_AI_ADVANTAGE',
        'generation_protocol_digest':review['generation_protocol_digest'],'candidate_sha256':review['candidate_sha256'],
        'bindings':bindings,'source_drift':bindings!=version_bindings(),
        'counts':{rep:{s:sum(r['representation']==rep and r['status']==s for r in records) for s in ('PASS','FAIL','NOTRUN')}
                  for rep in ('gameir_text','js_callback')},
        'IR_runtime_cases':{'PASS':0,'FAIL':0,'NOTRUN':len(cases)},
        'static_rejection_denominator':'IR failure repeated over fixed cases; one rejected submission, not 26 independent model failures',
        'fresh_profile':profile['checks'],'fixed_fault_counts':{s:sum(f['status']==s for f in faults) for s in ('PASS','FAIL','NOTRUN')},
        'actual_generated_JS_callback_invocations':sum(a['generated_source_executions'] for a in audits),
        'IR_generated_runtime_executions':0,'parser_candidate_evaluations':0,
        'all_candidate_workers_clean':all(not a['cleanup_errors'] and a['surviving_owned_processes']==0 for a in audits),
        'worker_image':backend.worker_image,'worker_source_sha256':backend.worker_hash,'node_image':backend.lock,
        'model_usage':review['original_model_summary'],'generation_latency':'NOT_MEASURED_NO_REQUEST_START_TIMESTAMPS',
        'executor_wall_seconds':sum(r.get('wall_seconds',0) for r in records),'JS_compute_units':None,
        'authenticated_requests':0,'additional_model_calls':0,'repairs':0,'reset_cards_used':0,'push_performed':False,
        'limitations':['One Create development pair, fixed order, no holdout or independent model repetitions',
                      'IR compile rejection; token estimates are not tokenizer equality; wall time not CPU equivalence',
                      'IR architecture not frozen; TS deferred; original Vitric baseline absent'],
        'A12':'INCOMPLETE_SMALL_PILOT_ONLY','python':sys.version}
    save(args.output,'summary',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)
    return int(blocked or summary['source_drift'] or not summary['all_candidate_workers_clean'])


if __name__=='__main__':
    try:raise SystemExit(main())
    except EvaluationError as error:
        print(json.dumps({'status':'BLOCKED','code':str(error),'additional_model_calls':0}),flush=True)
        raise SystemExit(2)
