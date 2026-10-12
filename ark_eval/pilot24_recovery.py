"""One-use exact remainder, detached controller and read-only durable status."""
import argparse
import copy
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .adapter import EvaluationError,digest
from . import pilot24_execution_eval as old
from . import pilot24_recovery_scope as s
from .pilot24_execution_scope import ROOT,read,require
from .container_profile import clean_environment

CLAIMS = ROOT / 'results/pilot24-recovery-claims'
LAUNCHES = ROOT / 'results/pilot24-recovery-launches'
ZERO_CLAIMS = ROOT / 'results/pilot24-zero-start-claims'
ZERO_LAUNCHES = ROOT / 'results/pilot24-zero-start-launches'


def unit_name(m):
    return 'gir-pilot24-recovery-' + m['recovery_key']


def output_path(output):
    p = old.validate_output(output)
    require(p.parent == ROOT / 'results' and p.name.startswith('pilot24-recovery-score-') and
            len(p.name) <= 100, 'P24R_OUTPUT_PATH')
    return p


def linux_call(argv):
    require(sys.platform == 'linux', 'P24R_LINUX_SERVICE_REQUIRED')
    return subprocess.run(argv,capture_output=True,text=True,cwd=ROOT,timeout=30)


def service_state(m):
    r = linux_call(['/usr/bin/systemctl','--user','show',unit_name(m),
                    '--property=LoadState,ActiveState,SubState,ExecMainPID,ExecMainStatus,Result,Restart'])
    require(r.returncode == 0, 'P24R_SERVICE_STATE_UNVERIFIED')
    return dict(line.split('=',1) for line in r.stdout.splitlines() if '=' in line)


def services_available():
    r = linux_call(['/usr/bin/systemctl','--user','is-system-running'])
    require(r.returncode in (0,1) and r.stdout.strip() in ('running','degraded'), 'P24R_SERVICE_UNAVAILABLE')
    r = linux_call(['/usr/bin/pgrep','-af',
        '^python3 -m ark_eval.pilot24_execution_eval --output results/pilot24-isolated-score-001 --execute-approved-pilot24$'])
    require(r.returncode == 1, 'P24R_PARENT_CONTROLLER_NOT_ABSENT')


class RecoveryClaim:
    def __init__(self,scope,output):
        require(type(scope) is s.ApprovedRecovery, 'P24R_SCOPE_REQUIRED')
        scope.require()
        output = output_path(output)
        require(CLAIMS.resolve() == CLAIMS and LAUNCHES.resolve() == LAUNCHES, 'P24R_CLAIM_PATH')
        self.path = CLAIMS / scope.manifest['recovery_key']
        self.sequence = 0
        CLAIMS.mkdir(parents=True,exist_ok=True)
        try:self.path.mkdir()
        except FileExistsError:raise EvaluationError('P24R_ALREADY_CLAIMED') from None
        old.write_json(self.path/'claim.json',{'schema':'gir-pilot24-remainder-one-use@1',
            'recovery_key':scope.manifest['recovery_key'],'scope_digest':digest(scope.manifest),
            'approval_digest':scope.approval_digest,'parent_batch_key':s.PARENT_KEY,
            'output':str(output),'controller_pid':os.getpid(),'service_unit':unit_name(scope.manifest),
            'retry_or_resume_allowed':False,'state':'CLAIMED_NOT_STARTED'})

    def event(self,state,**details):
        self.sequence += 1
        old.write_json(self.path/('event-%04d.json'%self.sequence),{'state':state,**details})


class ZeroStartClaim(RecoveryClaim):
    def __init__(self,scope,output):
        require(type(scope) is s.ApprovedZeroStartRecovery, 'P24Z_SCOPE_REQUIRED')
        scope.require()
        output=output_path(output)
        identity=process_identity(os.getpid())
        require(identity is not None,'P24Z_CONTROLLER_IDENTITY_REQUIRED')
        require(ZERO_CLAIMS.resolve()==ZERO_CLAIMS,'P24Z_CLAIM_PATH')
        self.path=ZERO_CLAIMS/scope.manifest['recovery_key']
        self.sequence=0
        self.scope=scope
        ZERO_CLAIMS.mkdir(parents=True,exist_ok=True)
        try:self.path.mkdir()
        except FileExistsError:raise EvaluationError('P24Z_PERMIT_ALREADY_CONSUMED') from None
        old.write_json(self.path/'claim.json',{'schema':'gir-pilot24-zero-start-one-use@1',
            'scope_digest':digest(scope.manifest),'recovery_key':scope.manifest['recovery_key'],
            'permit_digest':scope.approval_digest,'prior_claim':scope.manifest.get('prior_claim',s.FAILED_CLAIM),
            'prior_evidence_digest':scope.manifest.get('prior_evidence_digest',s.FAILED_DIGEST),'output':str(output),
            'controller_identity':identity,'route':'direct-wsl',
            'maximum_primary_runs':scope.manifest['maximum_primary_runs'],'retry_or_resume_allowed':False})

    def reserve(self,row):
        require(any(x['slot_id']==row['slot_id'] and x['case_id']==row['case_id'] and
                    x['source_sha256']==row['source_sha256'] for x in self.scope.manifest['allowed_rows']),
                'P24Z_CASE_LEDGER_RANGE')
        old.write_json(self.path/('case-'+row['slot_id']+'-'+row['case_id']+'.json'),
                       {**row,'maximum_primary_runs':1,'unknown_outcome':'DO_NOT_RETRY'})


def safe_failure(error):
    allowed={'ROOTLESS_LINUX_REQUIRED','ROOTLESS_RESOURCE_LIMITS_UNAVAILABLE','PODMAN_CONTROL_FAILED',
             'IMAGE_QUERY_FAILED','WORKER_IMAGE_DRIFT','P24R_PARENT_ADMISSION_CHANGED',
             'P24_IR_PRECHECK_FAILED','CANDIDATE_ISOLATION_DISABLED','FRESH_CANDIDATE_ISOLATION_REJECTED',
             'P24R_RESULT_CONTRACT','P24_FRESH_ISOLATION_STALE','P24_FRESH_ISOLATION_BINDING_CHANGED',
             'P24_FRESH_ISOLATION_REFRESH_REJECTED','P24_JS_INFRA_OR_CLEANUP_FAILED',
             'P24_FORWARD_SCOPE_DRIFT','P24_LIVE_SCOPE_DRIFT','P24F_FREEZE_DRIFT',
             'P24_CALLBACK_ALREADY_BLOCKED','P24_JS_PARSE_CHANGED','P24_JS_SOURCE_NOT_PARSED',
             'JS_TIMEOUT','JS_DISCONNECTED','JS_EXITED','BACKEND_DISABLED_AFTER_ISOLATION_FAILURE'}
    code=getattr(error,'code',None)
    if code not in allowed:code=error.args[0] if error.args and isinstance(error.args[0],str) else None
    kinds={'EvaluationError','Fault','OSError','TimeoutExpired','KeyboardInterrupt','FileExistsError',
           'JSONDecodeError','AssertionError'}
    value={'code':code if code in allowed else 'UNCLASSIFIED_INFRASTRUCTURE_ERROR',
            'exception_type':type(error).__name__ if type(error).__name__ in kinds else 'Exception',
            'raw_message_saved':False}
    first=getattr(error,'first_infrastructure_exception',None)
    if type(first) is dict and set(first)=={'code','exception_type','raw_message_saved','stage'} and \
            first['code'] in allowed|{'UNCLASSIFIED_INFRASTRUCTURE_ERROR'} and \
            first['exception_type'] in kinds|{'Exception'} and first['raw_message_saved'] is False and \
            first['stage'] in {'metadata_or_transport','bootstrap_or_transport','cleanup'}:
        value['first_infrastructure_exception']=dict(first)
    return value


def remainder_rows(scope):
    allowed = {(r['slot_id'],r['case_id']) for r in scope.manifest['allowed_rows']}
    return [copy.deepcopy(r) for r in scope.preserved if (r['slot_id'],r['case_id']) in allowed]


def merge_rows(scope,rows):
    fresh = {(r['slot_id'],r['case_id']):r for r in rows}
    require(set(fresh) == {(r['slot_id'],r['case_id']) for r in scope.manifest['allowed_rows']},'P24R_MERGE_RANGE')
    return [copy.deepcopy(fresh.get((r['slot_id'],r['case_id']),r)) for r in scope.preserved]


def accounting(rows):
    fields = ('primary_case_executions','IR_case_replays','JS_transcript_replays','candidate_executions')
    known = {k:sum(r[k] for r in rows if type(r[k]) is int) for k in fields}
    unknown = {k:sum(type(r[k]) is not int for r in rows) for k in fields}
    return {'counts':{k:sum(r['status']==k for r in rows) for k in ('PASS','FAIL','NOTRUN','HARNESS_ERROR')},
            'known':known,'unknown_records':unknown,
            'primary_runs_lower':known['primary_case_executions'],
            'primary_runs_upper':known['primary_case_executions']+unknown['primary_case_executions'],
            'IR_case_replays':None if unknown['IR_case_replays'] else known['IR_case_replays'],
            'JS_transcript_replays':None if unknown['JS_transcript_replays'] else known['JS_transcript_replays']}


def validate_result(rep,case,value):
    require(value['status'] == ('PASS' if value['actual_ticks']==case['expected_ticks'] else 'FAIL') and
            value['expected_ticks']==case['expected_ticks'] and value['replay_match'] is True and
            value['cleanup_verified'] is True and type(value['primary_case_executions']) is int and
            value['primary_case_executions']==1 and type(value['candidate_executions']) is int and
            value['candidate_executions']>=0 and type(value['IR_case_replays']) is int and
            value['IR_case_replays']==int(rep=='gameir_text') and
            type(value['JS_transcript_replays']) is int and value['JS_transcript_replays']==int(rep=='js_callback'),
            'P24R_RESULT_CONTRACT')


def run_controller(scope,output):
    require(type(scope) in (s.ApprovedRecovery,s.ApprovedZeroStartRecovery),'P24R_RECOVERY_TYPE')
    direct=type(scope) is s.ApprovedZeroStartRecovery
    claim = ZeroStartClaim(scope,output) if direct else RecoveryClaim(scope,output)
    output.mkdir(parents=True,exist_ok=False)
    old.write_json(output/'scope.json',scope.manifest)
    rows = remainder_rows(scope)
    old.write_json(output/'notrun-initial.json',rows)
    halt = None
    backends = []
    admissions = []
    failure = None
    for sid in dict.fromkeys(r['slot_id'] for r in rows):
        if halt:break
        backend = None
        selected = [r for r in rows if r['slot_id']==sid]
        admission = {'slot_id':sid,'status':'NOTRUN'}
        try:
            rep = scope.slot(sid)['representation']
            claim.event('PRECHECK_STARTED',slot_id=sid)
            backend = old.NodeBackend() if rep=='js_callback' else old.Pilot24IRBackend(scope.base,sid)
            backends.append(backend)
            proof = old.fresh(scope.base,backend,rep,output/('precheck-'+sid))
            scope.slot(sid)
            old.write_json(output/('admission-reservation-'+sid+'.json'),{'slot_id':sid,'retry':False})
            claim.event('ADMISSION_RESERVED',slot_id=sid)
            parsed = None
            if rep=='js_callback':
                parsed = old.parse_module(scope.base.source(sid),old.CandidateParserBackend())
                status = old.parse_status(scope.base,sid,parsed)
                value = parsed
            else:
                admission_refresh_count = 0
                def refresh_admission():
                    nonlocal admission_refresh_count
                    admission_refresh_count += 1
                    return old.fresh(scope.base,backend,rep,
                        output/('refresh-admission-'+sid+'-%04d'%admission_refresh_count))
                value = old.ir_call(scope.base,sid,backend,proof,refresh=refresh_admission)
                status = value['result']['admission']
            old.write_json(output/('admission-result-'+sid+'.json'),value)
            require(status=='PARSED','P24R_PARENT_ADMISSION_CHANGED')
            admission['status']='PARSED'
            claim.event('ADMISSION_COMPLETED',slot_id=sid,status='PARSED')
        except (Exception,KeyboardInterrupt) as error:
            halt = 'ADMISSION_OR_PRECHECK_INCOMPLETE'
            admission['status']='HARNESS_ERROR'
            failure=safe_failure(error)
            admission['failure']=failure
        for row in selected:
            if halt:break
            reserved = False
            case = next(c for c in scope.cases[row['task_id']] if c['id']==row['case_id'])
            try:
                scope.case(sid,case)
                if time.monotonic()-proof.created > 550:
                    proof = old.fresh(scope.base,backend,rep,output/('refresh-'+sid+'-'+case['id']))
                proof.require(backend)
                scope.case(sid,case)
                if direct:
                    claim.reserve(row)
                    reserved=True
                    row['phase']='RESERVED_EXECUTION_UNCONFIRMED'
                old.write_json(output/('reservation-'+sid+'-'+case['id']+'.json'),
                    {**row,'maximum_primary_runs':1,'unknown_outcome':'DO_NOT_RETRY'})
                reserved = True
                row['phase']='RESERVED_EXECUTION_UNCONFIRMED'
                claim.event('PRIMARY_RESERVED',slot_id=sid,case_id=case['id'])
                refresh_count=0
                def refresh_after_metadata():
                    nonlocal refresh_count
                    refresh_count+=1
                    return old.fresh(scope.base,backend,rep,
                        output/('refresh-metadata-'+sid+'-'+case['id']+'-%04d'%refresh_count))
                value = (old.evaluate_js(scope.base,sid,backend,proof,parsed,case,refresh=refresh_after_metadata)
                         if rep=='js_callback' else
                         old.evaluate_ir(scope.base,sid,backend,proof,case,refresh=refresh_after_metadata))
                validate_result(rep,case,value)
                old.write_json(output/('result-'+sid+'-'+case['id']+'.json'),value)
                row.update(value,phase='COMPLETED',reason=None)
                claim.event('PRIMARY_COMPLETED',slot_id=sid,case_id=case['id'],status=row['status'])
            except (Exception,KeyboardInterrupt) as error:
                halt = 'PRIMARY_OR_PERSISTENCE_INCOMPLETE'
                failure=safe_failure(error)
                if direct and (claim.path/('case-'+sid+'-'+case['id']+'.json')).exists():
                    reserved=True
                    if row['phase']!='COMPLETED':row['phase']='RESERVED_EXECUTION_UNCONFIRMED'
                if reserved and row['phase']!='COMPLETED':
                    row.update(status='HARNESS_ERROR',reason=halt,primary_case_executions='UNKNOWN_0_OR_1',
                        candidate_executions='UNKNOWN_AFTER_RESERVATION',
                        IR_case_replays='UNKNOWN_0_OR_1' if rep=='gameir_text' else 0,
                        JS_transcript_replays='UNKNOWN_0_OR_1' if rep=='js_callback' else 0)
                admission['status']='HARNESS_ERROR'
        admissions.append(admission)
        old.write_json(output/('slot-'+sid+'.json'),{'admission':admission,'cases':selected})
    for row in rows:
        if row['status']=='NOTRUN':row.update(reason=halt or 'NOT_STARTED')
    remaining = None
    if backends:
        try:
            remaining=json.loads(backends[-1]._ctl('ps','--all','--filter','label=gir.reviewed-worker=phase3','--format=json'))
            if remaining:halt=halt or 'PROFILE_CONTAINERS_REMAIN'
        except Exception:halt=halt or 'CLEANUP_INVENTORY_UNVERIFIED'
    scope.require()
    combined=merge_rows(scope,rows)
    summary={'schema':'gir-pilot24-exact-remainder-result@1','state':'STOPPED_ON_BLOCK' if halt else 'REMAINDER_COMPLETE',
             'halt':halt,'scope_digest':digest(scope.manifest),'recovery_key':scope.manifest['recovery_key'],
             'claim_consumed':True,'retry_or_resume_allowed':False,'remainder':accounting(rows),
             'combined_fixed_288':accounting(combined),'preserved_parent_unknown':[list(s.UNKNOWN)],
             'admissions':admissions,'remaining_profile_containers':remaining,'model_calls':0,'repairs':0,
             'parent_evidence_digest':s.EVIDENCE_DIGEST,'original_execution_not_restarted':True}
    summary.update(route='direct-wsl' if direct else 'legacy-fixture',failure=failure,
                   failed_recovery_claim_unchanged=True)
    old.write_json(output/'cases.json',rows)
    old.write_json(output/'combined-cases.json',combined)
    old.write_json(output/'summary.json',summary)
    claim.event(summary['state'],halt=halt)
    return summary


def launch(scope,output):
    scope.require()
    output=output_path(output)
    services_available()
    require(service_state(scope.manifest).get('LoadState')=='not-found','P24R_SERVICE_ALREADY_EXISTS')
    require(LAUNCHES.resolve()==LAUNCHES,'P24R_LAUNCH_PATH')
    LAUNCHES.mkdir(parents=True,exist_ok=True)
    launch_dir=LAUNCHES/scope.manifest['recovery_key']
    try:launch_dir.mkdir()
    except FileExistsError:raise EvaluationError('P24R_LAUNCH_ALREADY_RESERVED') from None
    intent={'scope_digest':digest(scope.manifest),'recovery_key':scope.manifest['recovery_key'],
            'output':str(output),'service_unit':unit_name(scope.manifest),'retry':False}
    old.write_json(launch_dir/'intent.json',intent)
    command=['/usr/bin/systemd-run','--user','--unit='+unit_name(scope.manifest),
             '--property=Type=exec','--property=RemainAfterExit=yes','--property=Restart=no',
             '--property=KillMode=control-group','--property=TimeoutStopSec=20',
             '--property=StandardOutput=null','--property=StandardError=null',
             '--working-directory='+str(ROOT),'/usr/bin/python3','-m','ark_eval.pilot24_recovery',
             '--controller-recovery-key',scope.manifest['recovery_key'],'--output',str(output)]
    try:r=linux_call(command)
    except Exception:raise EvaluationError('P24R_LAUNCH_OUTCOME_UNKNOWN_DO_NOT_RETRY') from None
    old.write_json(launch_dir/'launch-result.json',{'returncode':r.returncode,'service_unit':unit_name(scope.manifest)})
    require(r.returncode==0,'P24R_LAUNCH_FAILED_DO_NOT_RETRY')
    return {'state':'DETACHED_SERVICE_LAUNCH_ACCEPTED','service_unit':unit_name(scope.manifest),
            'output':str(output),'model_calls':0,'results_not_yet_confirmed':True}


def status(manifest):
    launch_dir=LAUNCHES/manifest['recovery_key']
    if not (launch_dir/'intent.json').is_file():return {'state':'NOT_LAUNCHED','model_calls':0}
    intent=read((launch_dir/'intent.json').relative_to(ROOT).as_posix())
    require(intent['scope_digest']==digest(manifest) and intent['recovery_key']==manifest['recovery_key'],
            'P24R_STATUS_INTENT_DRIFT')
    output=Path(intent['output'])
    require(output.parent==ROOT/'results' and output.name.startswith('pilot24-recovery-score-') and
            output.resolve()==output,'P24R_STATUS_PATH')
    service=service_state(manifest)
    running=service.get('ActiveState') in ('activating','active') and service.get('SubState')!='exited'
    completed=[];unknown=[];pending=[]
    parent_manifest=s.parent.load_manifest()
    slots={x['slot_id']:x for x in parent_manifest['slots']}
    fixed_cases=s.parent.case_sets(s.parent.static_manifest())
    for entry in manifest['allowed_rows']:
        suffix=entry['slot_id']+'-'+entry['case_id']+'.json'
        p=output/('result-'+suffix)
        if p.is_file():
            task,rep=slots[entry['slot_id']]['task_id'],slots[entry['slot_id']]['representation']
            case=next(c for c in fixed_cases[task] if c['id']==entry['case_id'])
            require((output/('reservation-'+suffix)).is_file(),'P24R_RESULT_WITHOUT_RESERVATION')
            value=read(p.relative_to(ROOT).as_posix());validate_result(rep,case,value)
            completed.append({'slot_id':entry['slot_id'],'case_id':entry['case_id'],'status':value['status']})
        elif (output/('reservation-'+suffix)).is_file():unknown.append([entry['slot_id'],entry['case_id']])
        else:pending.append([entry['slot_id'],entry['case_id']])
    terminal=output/'summary.json'
    if terminal.is_file():
        receipt=read(terminal.relative_to(ROOT).as_posix())
        require(receipt['scope_digest']==digest(manifest) and receipt['recovery_key']==manifest['recovery_key'] and
                receipt['state'] in ('REMAINDER_COMPLETE','STOPPED_ON_BLOCK'),'P24R_TERMINAL_RECEIPT_DRIFT')
    return {'state':'TERMINAL_RECEIPT_AVAILABLE' if terminal.is_file() else 'IN_PROGRESS' if running else
            'STOPPED_WITHOUT_TERMINAL_RECEIPT','service':service,'output':str(output),
            'durable_results':completed,'reserved_unconfirmed':unknown,'not_reserved':pending,
            'primary_runs_lower':len(completed),'primary_runs_upper':len(completed)+len(unknown),
            'claim_consumed':(CLAIMS/manifest['recovery_key']).exists(),
            'retry_or_resume_allowed':False,'model_calls':0}


def process_identity(pid):
    if type(pid) is not int or pid<=0:return None
    try:
        fields=Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()
        return {'pid':pid,'start_ticks':fields[19]}
    except FileNotFoundError:return None


def process_alive(identity):
    if not isinstance(identity,dict) or set(identity)!={'pid','start_ticks'} or \
            type(identity['pid']) is not int or identity['pid']<=0 or \
            not isinstance(identity['start_ticks'],str) or not identity['start_ticks'].isdigit() or \
            not 1<=len(identity['start_ticks'])<=30:return False
    if process_identity(identity['pid'])!=identity:return False
    try:return Path('/proc/'+str(identity['pid'])+'/stat').read_text().rsplit(')',1)[1].split()[0] not in ('Z','X')
    except FileNotFoundError:return False


def forward_prior_controller_stopped(manifest):
    require(manifest['schema'] in ('gir-pilot24-exact-remainder@3','gir-pilot24-exact-remainder@4','gir-pilot24-exact-remainder@5') and
            digest(manifest)==digest(s.forward_provider(manifest).load_forward_manifest()),
            'P24F_PRIOR_SCOPE_REQUIRED')
    identity=read(manifest['prior_claim']+'/claim.json').get('controller_identity')
    require(type(identity) is dict and set(identity)=={'pid','start_ticks'} and
            type(identity['pid']) is int and identity['pid']>0 and
            type(identity['start_ticks']) is str and 1<=len(identity['start_ticks'])<=30 and
            identity['start_ticks'].isdigit(),'P24F_PRIOR_IDENTITY_UNVERIFIED')
    # An unreadable/malformed proc record raises and blocks. No signalling or permission edits.
    require(not process_alive(identity),'P24F_PRIOR_CONTROLLER_NOT_STOPPED')


def direct_preflight(manifest=None):
    require(sys.platform=='linux','P24Z_LINUX_REQUIRED')
    environment=clean_environment()
    info=subprocess.run(['/usr/bin/podman','info','--format=json'],cwd='/',env=environment,
                        capture_output=True,text=True,timeout=20)
    require(info.returncode==0,'P24Z_PODMAN_METADATA_UNAVAILABLE')
    host=json.loads(info.stdout)['host']
    require(host['security']['rootless'] is True and host['cgroupVersion']=='v2' and
            host['cgroupManager']=='systemd' and
            {'cpu','memory','pids'} <= set(host.get('cgroupControllers',[])), 'P24Z_DIRECT_RESOURCES')
    require(Path('/proc/self/cgroup').read_text().strip()=='0::/non-systemd','P24Z_DIFFERENT_DIRECT_CONTEXT')
    services_available()
    prior=service_state({'recovery_key':s.RECOVERY_KEY})
    require(prior.get('ActiveState') in ('failed','inactive') and prior.get('SubState')!='running',
            'P24Z_PRIOR_CONTROLLER_NOT_STOPPED')
    if manifest is not None:forward_prior_controller_stopped(manifest)
    return environment


def launch_zero_start(scope,output):
    require(type(scope) is s.ApprovedZeroStartRecovery,'P24Z_SCOPE_REQUIRED')
    scope.require()
    output=output_path(output)
    forward=scope.manifest['schema'] in ('gir-pilot24-exact-remainder@3','gir-pilot24-exact-remainder@4','gir-pilot24-exact-remainder@5')
    environment=direct_preflight(scope.manifest) if forward else direct_preflight()
    require(ZERO_LAUNCHES.resolve()==ZERO_LAUNCHES,'P24Z_LAUNCH_PATH')
    ZERO_LAUNCHES.mkdir(parents=True,exist_ok=True)
    target=ZERO_LAUNCHES/scope.manifest['recovery_key']
    try:target.mkdir()
    except FileExistsError:raise EvaluationError('P24Z_LAUNCH_ALREADY_RESERVED') from None
    intent={'scope_digest':digest(scope.manifest),'recovery_key':scope.manifest['recovery_key'],
            'permit_digest':scope.approval_digest,'output':str(output),'route':'direct-wsl','retry':False}
    old.write_json(target/'intent.json',intent)
    try:
        child=subprocess.Popen(['/usr/bin/python3','-m','ark_eval.pilot24_recovery',
            *(['--forward51'] if scope.manifest['schema']=='gir-pilot24-exact-remainder@5' else
              ['--forward60'] if scope.manifest['schema']=='gir-pilot24-exact-remainder@4' else
              ['--forward61'] if scope.manifest['schema']=='gir-pilot24-exact-remainder@3' else []),
            '--zero-start-controller-key',scope.manifest['recovery_key'],'--output',str(output)],
            cwd=ROOT,env=environment,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,close_fds=True,start_new_session=True)
        identity=process_identity(child.pid)
        require(identity is not None,'P24Z_CHILD_IDENTITY_UNKNOWN')
        old.write_json(target/'launch-result.json',{'controller_identity':identity,
                                                   'outcome':'PROCESS_CREATED_RESULTS_UNCONFIRMED'})
    except Exception:raise EvaluationError('P24Z_LAUNCH_OUTCOME_UNKNOWN_DO_NOT_RETRY') from None
    return {'state':'DIRECT_PROCESS_LAUNCH_ACCEPTED','controller_identity':identity,
            'output':str(output),'results_not_yet_confirmed':True,'model_calls':0}


def direct_status(manifest):
    target=ZERO_LAUNCHES/manifest['recovery_key']
    if not (target/'intent.json').is_file():
        return {'state':'DIRECT_NOT_LAUNCHED','prior_failed_claim_consumed':True,'model_calls':0}
    intent=read((target/'intent.json').relative_to(ROOT).as_posix())
    require(set(intent)=={'scope_digest','recovery_key','permit_digest','output','route','retry'} and
            intent['scope_digest']==digest(manifest) and intent['recovery_key']==manifest['recovery_key'] and
            intent['route']=='direct-wsl' and intent['retry'] is False and
            type(intent['permit_digest']) is str and len(intent['permit_digest'])==64 and
            all(c in '0123456789abcdef' for c in intent['permit_digest']),'P24Z_STATUS_INTENT_DRIFT')
    output=Path(intent['output'])
    require(output.resolve()==output and output.parent==ROOT/'results' and
            output.name.startswith('pilot24-recovery-score-'),'P24Z_STATUS_OUTPUT')
    claim=ZERO_CLAIMS/manifest['recovery_key']
    identity=None;identity_source=None
    for path,source in ((target/'launch-result.json','launch-result'),(claim/'claim.json','claim')):
        if not path.is_file():continue
        try:
            value=read(path.relative_to(ROOT).as_posix())
            candidate=value.get('controller_identity')
            valid=(type(candidate) is dict and set(candidate)=={'pid','start_ticks'} and
                   type(candidate['pid']) is int and candidate['pid']>0 and
                   type(candidate['start_ticks']) is str and 1<=len(candidate['start_ticks'])<=30 and
                   candidate['start_ticks'].isdigit())
            if source=='launch-result':
                valid=valid and set(value)=={'controller_identity','outcome'} and \
                    value['outcome']=='PROCESS_CREATED_RESULTS_UNCONFIRMED'
            else:
                expected={'schema':'gir-pilot24-zero-start-one-use@1','scope_digest':digest(manifest),
                    'recovery_key':manifest['recovery_key'],'permit_digest':intent['permit_digest'],
                    'prior_claim':manifest.get('prior_claim',s.FAILED_CLAIM),
                    'prior_evidence_digest':manifest.get('prior_evidence_digest',s.FAILED_DIGEST),
                    'output':str(output),'route':'direct-wsl','maximum_primary_runs':manifest['maximum_primary_runs'],
                    'retry_or_resume_allowed':False}
                valid=valid and {k:v for k,v in value.items() if k!='controller_identity'}==expected and \
                    type(value.get('maximum_primary_runs')) is int and value.get('retry_or_resume_allowed') is False
            if valid:
                if identity is not None and identity!=candidate:
                    identity=None;identity_source=None;break
                identity=candidate;identity_source=source
        except (EvaluationError,OSError,ValueError,TypeError,AttributeError,KeyError):
            pass
    try:running=process_alive(identity) if identity is not None and sys.platform=='linux' else None
    except (OSError,ValueError,IndexError):running=None
    done=[];unknown=[];pending=[]
    forward=manifest['schema'] in ('gir-pilot24-exact-remainder@3','gir-pilot24-exact-remainder@4','gir-pilot24-exact-remainder@5')
    case_sets=s.parent.case_sets(s.forward_provider(manifest).forward_static_manifest()
                                if forward else s.parent.static_manifest())
    slots={x['slot_id']:x for x in (s.historical_parent() if forward else s.parent.load_manifest())['slots']}
    for entry in manifest['allowed_rows']:
        suffix=entry['slot_id']+'-'+entry['case_id']+'.json'
        result=output/('result-'+suffix)
        reserved=(output/('reservation-'+suffix)).is_file() or (claim/('case-'+suffix)).exists()
        if result.is_file():
            require(reserved,'P24Z_RESULT_WITHOUT_RESERVATION')
            slot=slots[entry['slot_id']]
            case=next(c for c in case_sets[slot['task_id']] if c['id']==entry['case_id'])
            value=read(result.relative_to(ROOT).as_posix());validate_result(slot['representation'],case,value)
            done.append({'slot_id':entry['slot_id'],'case_id':entry['case_id'],'status':value['status']})
        elif reserved:unknown.append([entry['slot_id'],entry['case_id']])
        else:pending.append([entry['slot_id'],entry['case_id']])
    terminal=output/'summary.json'
    if terminal.is_file():
        receipt=read(terminal.relative_to(ROOT).as_posix())
        require(receipt['scope_digest']==digest(manifest) and receipt['recovery_key']==manifest['recovery_key'] and
                receipt['state'] in ('REMAINDER_COMPLETE','STOPPED_ON_BLOCK'),'P24Z_TERMINAL_DRIFT')
    return {'state':'TERMINAL_RECEIPT_AVAILABLE' if terminal.is_file() else 'IN_PROGRESS' if running else
            'PROCESS_STATE_UNAVAILABLE' if running is None else 'STOPPED_WITHOUT_TERMINAL_RECEIPT',
            'controller_identity':identity,'identity_source':identity_source,'running':running,'durable_results':done,
            'reserved_unconfirmed':unknown,'not_reserved':pending,'claim_consumed':claim.exists(),
            'primary_runs_lower':len(done),'primary_runs_upper':len(done)+len(unknown),
            'retry_or_resume_allowed':False,'model_calls':0}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path)
    version=p.add_mutually_exclusive_group()
    version.add_argument('--forward61',action='store_true',help='Explicit v3 binding; old gates remain strict')
    version.add_argument('--forward60',action='store_true',help='Exact unreserved v4; consumed v3 remains immutable')
    version.add_argument('--forward51',action='store_true',help='Exact never-reserved v5 after interrupted v4')
    group=p.add_mutually_exclusive_group()
    group.add_argument('--execute-approved-remainder',action='store_true')
    group.add_argument('--controller-recovery-key')
    group.add_argument('--status',action='store_true')
    group.add_argument('--execute-authorized-zero-start',action='store_true')
    group.add_argument('--zero-start-controller-key')
    args=p.parse_args(argv)
    try:
        if args.forward51:
            from . import pilot24_forward51_scope as provider
        elif args.forward60:
            from . import pilot24_forward60_scope as provider
        else:provider=s
        m=provider.load_forward_manifest() if args.forward61 or args.forward60 or args.forward51 else s.load_manifest()
        if args.status:result=direct_status(m)
        elif args.execute_authorized_zero_start or args.zero_start_controller_key:
            permit=provider.PERMIT if args.forward60 or args.forward51 else s.FORWARD_PERMIT if args.forward61 else s.ZERO_START_PERMIT
            require(args.output is not None and permit.is_file(),'P24Z_PERMIT_REQUIRED')
            scope=s.ApprovedZeroStartRecovery(m,read(permit.relative_to(ROOT).as_posix()))
            if args.execute_authorized_zero_start:result=launch_zero_start(scope,args.output)
            else:
                require(args.zero_start_controller_key==m['recovery_key'],'P24Z_CONTROLLER_KEY')
                intent=read((ZERO_LAUNCHES/m['recovery_key']/'intent.json').relative_to(ROOT).as_posix())
                require(intent=={'scope_digest':digest(m),'recovery_key':m['recovery_key'],
                    'permit_digest':scope.approval_digest,'output':str(args.output),
                    'route':'direct-wsl','retry':False},'P24Z_CONTROLLER_INTENT')
                direct_preflight(m) if args.forward61 or args.forward60 or args.forward51 else direct_preflight()
                def interrupted(signum,frame):raise KeyboardInterrupt()
                signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
                result=run_controller(scope,args.output)
        elif args.execute_approved_remainder or args.controller_recovery_key:
            require(False,'P24Z_OLD_CLAIM_CONSUMED_USE_EXPLICIT_ZERO_START_GATE')
        else:
            require(args.output is not None,'P24R_OUTPUT_REQUIRED')
            output=output_path(args.output);output.mkdir(parents=True,exist_ok=False)
            old.write_json(output/'scope.json',m)
            old.write_json(output/'notrun-initial.json',[{**r,'status':'NOTRUN'} for r in m['allowed_rows']])
            result={'state':'PREPARED_NO_EXECUTION','scope_digest':digest(m),'recovery_key':m['recovery_key'],
                    'rows':m['maximum_primary_runs'],'approval_written':False,'claim_taken':False,'candidate_executions':0,'model_calls':0}
        print(json.dumps(result))
        return int(result.get('state') in ('STOPPED_ON_BLOCK','STOPPED_WITHOUT_TERMINAL_RECEIPT'))
    except (Exception,KeyboardInterrupt):
        print(json.dumps({'state':'BLOCKED_READ_RETAINED_EVIDENCE_DO_NOT_RETRY',
                          'execution_count':None,'model_calls':0,'approval_written':False}))
        return 2


if __name__=='__main__':raise SystemExit(main())
