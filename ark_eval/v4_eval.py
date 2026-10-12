"""Offline by default. Exact approval file required for a future single v4 run."""
import argparse,json,time
from pathlib import Path
from headless_v01.compiler import digest
from headless_v01.types import Fault
from .adapter import EvaluationError,bounded_json
from .manual_pair import write_json
from .v4_scope import (ROOT,ORDER,APPROVAL_FILE,ApprovedV4Scope,FreshV4Isolation,
                       load_manifest,approval_template,case_sets,read,require_parse)
from .v4_backend import V4IRBackend
from .v4_runner import V4JSRunner,V4HostRuntime
from .node_backend import NodeBackend
from .candidate_eval import fresh_isolation
from .candidate_parse_backend import CandidateParserBackend,parse_module
from .candidate_runner import replay_candidate
from .callback_verify import callback_manifest
from .oracle import observations
from .js_verify import profile_probe
from .pair003_eval import resource_fixture

CLAIM_ROOT=ROOT/'results/v4-batch001-execution-evidence'

def validate_output(output):
    output=Path(output).absolute()
    if output.exists():raise FileExistsError('Fresh output required')
    if any(p.is_symlink() for p in (output,*output.parents,CLAIM_ROOT,*CLAIM_ROOT.parents)):
        raise EvaluationError('V4_EXECUTION_EVIDENCE_PATH_REJECTED')
    if any(p.exists() and not p.is_dir() for p in (*output.parents,CLAIM_ROOT,*CLAIM_ROOT.parents)):
        raise EvaluationError('V4_EXECUTION_EVIDENCE_PARENT_REJECTED')
    if output.resolve().is_relative_to(CLAIM_ROOT.resolve()):
        raise EvaluationError('V4_OUTPUT_IN_CLAIM_ROOT_REJECTED')
    return output

class ExecutionClaim:
    """Durable single-use scope claim; never released, even on an incomplete run."""
    def __init__(self,scope,output):
        if type(scope)is not ApprovedV4Scope:raise EvaluationError('V4_APPROVED_SCOPE_REQUIRED')
        scope.require();output=validate_output(output) # All read-only validation precedes claim.
        self.path=CLAIM_ROOT/digest(scope.manifest);self.sequence=0
        CLAIM_ROOT.mkdir(parents=True,exist_ok=True)
        try:self.path.mkdir() # Atomic exclusive claim across processes/output directories.
        except FileExistsError:raise EvaluationError('V4_SCOPE_ALREADY_CLAIMED') from None
        write_json(self.path/'claim.json',{'schema':'gir-v4-single-use-claim@1',
            'scope_digest':digest(scope.manifest),'output':str(output),
            'state':'CLAIMED_EXECUTION_NOT_STARTED','retry_allowed':False})
    def event(self,state,**details):
        self.sequence+=1
        write_json(self.path/('event-%04d.json'%self.sequence),{'state':state,**details})

def fresh(scope,backend,rep,output):
    output.mkdir(parents=True,exist_ok=False)
    if rep=='js_callback':_,profile,faults=fresh_isolation(backend,output)
    else:
        profile=profile_probe(backend);write_json(output/'profile-before.json',profile);faults=[]
        for name in ('spin','oom','tree'):
            row=resource_fixture(backend,name);faults.append(row);write_json(output/(name+'.json'),row)
            if row['status']!='PASS' or backend.disabled:raise EvaluationError('V4_IR_PRECHECK_FAILED')
        by={r['fixture']:r for r in faults}
        profile['checks']['memory_limits']=by['oom']['status']
        profile['checks']['process_tree_termination']=by['tree']['status']
        write_json(output/'profile.json',profile)
    return FreshV4Isolation(scope,backend,profile,faults,rep)

def evaluate_ir(scope,task,backend,receipt,group,case):
    if (type(backend)is not V4IRBackend or backend.task!=task or backend.scope is not scope
            or type(receipt)is not FreshV4Isolation or receipt.rep!='gameir_text' or receipt.scope is not scope):
        raise EvaluationError('V4_IR_ADMISSION_REJECTED')
    scope.case(task,'gameir_text',group,case);receipt.require(backend)
    worker=backend.invocation(10);source=scope.source(task,'gameir_text')
    source_sha=scope.manifest['source_sha256'][task+'/gameir_text']
    try:
        worker.open();worker.write({'v':'gir-pair003-ir@1','purpose':'ir_case','source':source,
            'case':{k:case[k] for k in ('id','initial','seed','steps')}},bootstrap=True)
        worker.audit.update(generated_source_executions=1,candidate_sha256=source_sha)
        frame=bounded_json(worker.read_line());result=frame['result']
        if (set(frame)!={'transport','result'} or frame['transport']!='gir-pair003-ir-result@1'
                or result['case_id']!=case['id'] or result['source_sha256']!=source_sha
                or result['source_modified'] is not False or result['JS_evaluations']!=0
                or result['candidate_case_executions']!=1 or result['candidate_replay_case_executions']!=1
                or result['snapshot_digest']!=digest(result['snapshot'])
                or result['replay_snapshot_digest']!=result['snapshot_digest']):
            raise EvaluationError('V4_IR_RESULT_SCOPE_REJECTED')
        worker.release(bounded_json(worker.read_line()))
    finally:worker.close()
    clean=worker.audit['cleanup_errors']==[] and worker.audit['surviving_owned_processes']==0
    if not clean or backend.disabled:raise EvaluationError('V4_IR_CLEANUP_FAILED')
    matched=result['observed']==case['expected']
    return {'status':'PASS' if matched and result['replay_match'] and result['host_operations_bounded'] else 'FAIL',
        'oracle_match':matched,'replay_match':result['replay_match'],'expected':case['expected'],
        'observed':result['observed'],'isolated_result':result,'worker_audit':worker.audit,'cleanup_verified':clean,
        'candidate_executions':1,'IR_case_replays':1}

def evaluate_js(scope,task,backend,receipt,parsed,group,case):
    scope.case(task,'js_callback',group,case)
    runner=V4JSRunner(scope,task,backend,receipt,parsed);program=callback_manifest()
    runtime=V4HostRuntime(program,case['initial'],runner,seed=case['seed'])
    results=[runtime.step(**step) for step in case['steps']]
    if (backend.disabled or any(a['cleanup_errors'] or a['surviving_owned_processes']!=0
            or a['terminal'] in ('V4_ISOLATION_OR_TRANSPORT_REJECTED','ISOLATION_CLEANUP_FAILED') for a in runner.audits)):
        raise EvaluationError('V4_JS_ISOLATION_OR_CLEANUP_FAILED')
    played=replay_candidate(program,case,runtime.history,runtime.execution_history)
    observed=observations(runtime,results);matched=observed==case['expected'];replayed=played.snapshot()==runtime.snapshot()
    return {'status':'PASS' if matched and replayed else 'FAIL','oracle_match':matched,'replay_match':replayed,
        'expected':case['expected'],'observed':observed,'results':results,'execution_audits':runtime.execution_history,
        'worker_audits':runner.audits,'cleanup_verified':True,
        'candidate_executions':sum(a['generated_source_executions'] for a in runner.audits),'JS_transcript_replays':1}

def planned_rows(cases):
    return [{'task_id':t,'representation':r,'group':g,'case_id':c['id'],'seed':c['seed'],
        'status':'NOTRUN','reason':'NOT_STARTED','candidate_phase':'NOT_STARTED','candidate_executions':0} for t,r in ORDER for g,c in cases[t,r]]

def counts(rows):
    return {t:{r:{g:{s:sum(row['task_id']==t and row['representation']==r and row['group']==g and row['status']==s
        for row in rows) for s in ('PASS','FAIL','NOTRUN')} for g in ('legacy26','diagnostic8')}
        for r in ('gameir_text','js_callback')} for t in ('P-C02-modify','P-C02-debug')}

def run(scope,output):
    if type(scope)is not ApprovedV4Scope:raise EvaluationError('V4_APPROVED_SCOPE_REQUIRED')
    scope.require();output=validate_output(output)
    claim=ExecutionClaim(scope,output)
    try:
        summary=_run_claimed(scope,output,claim)
        claim.event('STOPPED_ON_BLOCK' if summary['halt'] else 'COMPLETED',
            halt=summary['halt'],counts=summary['counts'],prechecks=summary['prechecks'])
        return summary
    except BaseException:
        # A failure recording this event still leaves the atomic claim consumed.
        try:claim.event('INCOMPLETE_DO_NOT_RETRY')
        except OSError:pass
        raise

def _run_claimed(scope,output,claim):
    output.mkdir(parents=True,exist_ok=False)
    write_json(output/'scope.json',scope.manifest);write_json(output/'approval.json',scope.approval)
    rows=planned_rows(scope.cases);write_json(output/'notrun-initial.json',rows)
    index=0;halt=None;backends=[]
    prechecks=[{'task_id':t,'representation':r,'status':'NOTRUN'} for t,r in ORDER]
    for arm_index,(task,rep) in enumerate(ORDER,1):
        backend=None;receipt=None;parsed=None;refresh_count=0
        try:
            if halt is None:
                scope.require()
                prechecks[arm_index-1]['status']='STARTED'
                claim.event('PRECHECK_STARTED',task_id=task,representation=rep)
                backend=NodeBackend() if rep=='js_callback' else V4IRBackend(scope,task);backends.append(backend)
                receipt=fresh(scope,backend,rep,output/('precheck-'+str(arm_index)))
                if rep=='js_callback':
                    parsed=parse_module(scope.source(task,rep),CandidateParserBackend())
                    write_json(output/('parse-'+str(arm_index)+'.json'),parsed);require_parse(scope,task,parsed)
                else:write_json(output/('image-'+str(arm_index)+'.json'),{'image':backend.worker_image,
                    'worker_hash':backend.worker_hash,'public_payload_sha256':backend.payload_hashes})
                prechecks[arm_index-1]['status']='PASS'
                claim.event('PRECHECK_PASSED',task_id=task,representation=rep)
        except (EvaluationError,Fault,OSError,ValueError,KeyError,TypeError,KeyboardInterrupt):
            halt='ADMISSION_OR_PRECHECK_FAILED'
            if prechecks[arm_index-1]['status']=='STARTED':prechecks[arm_index-1]['status']='INCOMPLETE'
            claim.event('ADMISSION_OR_PRECHECK_INCOMPLETE',task_id=task,representation=rep,
                precheck_status=prechecks[arm_index-1]['status'])
        for group,case in scope.cases[task,rep]:
            row=rows[index];reserved=False
            if halt is None:
                try:
                    scope.case(task,rep,group,case)
                    if time.monotonic()-receipt.created>550:
                        refresh_count+=1
                        claim.event('PRECHECK_REFRESH_STARTED',task_id=task,representation=rep,refresh=refresh_count)
                        receipt=fresh(scope,backend,rep,output/('refresh-'+str(arm_index)+'-'+str(refresh_count)))
                        claim.event('PRECHECK_REFRESH_PASSED',task_id=task,representation=rep,refresh=refresh_count)
                    receipt.require(backend)
                    write_json(output/('reservation-'+str(index+1)+'.json'),{**row,'source_sha256':scope.manifest['source_sha256'][task+'/'+rep],
                        'unknown_outcome':'DO_NOT_AUTOMATICALLY_RETRY','maximum_case_runs':1,'IR_worker_replays':1 if rep=='gameir_text' else 0})
                    claim.event('CANDIDATE_RESERVED_EXECUTION_UNCONFIRMED',case_index=index+1,
                        task_id=task,representation=rep,case_id=case['id'])
                    reserved=True;row['candidate_phase']='RESERVED_EXECUTION_UNCONFIRMED'
                    result=(evaluate_ir(scope,task,backend,receipt,group,case) if rep=='gameir_text'
                        else evaluate_js(scope,task,backend,receipt,parsed,group,case))
                    row.update(result);row['reason']=None;row['candidate_phase']='COMPLETED'
                    claim.event('CANDIDATE_COMPLETED',case_index=index+1,status=row['status'])
                except (EvaluationError,Fault,OSError,ValueError,KeyError,TypeError,KeyboardInterrupt):
                    halt='ISOLATION_ADMISSION_OR_EXECUTION_INCOMPLETE';row['reason']=halt
                    if reserved and row['candidate_phase']!='COMPLETED':row['candidate_executions']='UNKNOWN_IF_RESERVED'
                    claim.event('CANDIDATE_PHASE_INCOMPLETE',case_index=index+1,candidate_phase=row['candidate_phase'])
            else:row['reason']=halt
            write_json(output/('case-'+str(index+1)+'.json'),row);index+=1
    # Read-only independent container inventory; never remove foreign containers.
    remaining=[]
    if backends:
        try:remaining=json.loads(backends[-1]._ctl('ps','--all','--filter','label=gir.reviewed-worker=phase3','--format=json'))
        except (EvaluationError,OSError,ValueError):halt=halt or 'INDEPENDENT_CLEANUP_UNVERIFIED'
        if remaining:halt=halt or 'OWNED_PROFILE_CONTAINERS_REMAIN'
    scope.require()
    summary={'schema':'gir-v4-exact136-summary@1','scope_digest':digest(scope.manifest),'proposal_digest':scope.manifest['proposal_digest'],
        'state':'STOPPED_ON_BLOCK' if halt else 'COMPLETE','halt':halt,'counts':counts(rows),
        'contract_status':'NOT_CHECKED_FULL_RULES','rows':136,'remaining_profile_containers':remaining,
        'prechecks':prechecks,'claim_directory':str(claim.path),'claim_consumed':True,'retry_allowed':False,
        'completed_record_candidate_executions':sum(r['candidate_executions'] for r in rows if isinstance(r['candidate_executions'],int)),
        'unknown_execution_records':sum(not isinstance(r['candidate_executions'],int) for r in rows),
        'IR_case_replays':sum(r.get('IR_case_replays',0) for r in rows),
        'JS_transcript_replays':sum(r.get('JS_transcript_replays',0) for r in rows),
        'additional_model_calls':0,'repairs':0,'reset_cards':0,'push':False,
        'evidence_scope':'MODIFY_DEBUG_DEVELOPMENT_NOT_HOLDOUT_OR_AI_ADVANTAGE'}
    write_json(output/'cases.json',rows);write_json(output/'summary.json',summary);return summary

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--run-approved-v4',action='store_true')
    args=parser.parse_args(argv)
    if args.output.exists():parser.error('Fresh output required; no overwrite, resume or retry')
    try:
        manifest=load_manifest()
        if not args.run_approved_v4:
            args.output.mkdir(parents=True,exist_ok=False)
            write_json(args.output/'scope.json',manifest)
            write_json(args.output/'approval-template-NOT-APPROVED.json',approval_template(manifest))
            write_json(args.output/'notrun-initial.json',planned_rows(case_sets()))
            print(json.dumps({'state':'PREPARED_NO_EXECUTION','scope_digest':digest(manifest),'candidate_executions':0}));return 0
        # Only this explicit, fixed non-secret receipt path is ever read.
        if not APPROVAL_FILE.is_file():raise EvaluationError('V4_EXPLICIT_HUMAN_APPROVAL_REQUIRED')
        scope=ApprovedV4Scope(manifest,read(APPROVAL_FILE));summary=run(scope,args.output)
        print(json.dumps(summary));return int(summary['halt'] is not None)
    except (EvaluationError,Fault,OSError,ValueError,KeyError,TypeError):
        print(json.dumps({'state':'BLOCKED','code':'V4_SCOPE_APPROVAL_OR_ADMISSION_REJECTED','new_model_calls':0}));return 2

if __name__=='__main__':raise SystemExit(main())
