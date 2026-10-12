"""Offline tests of exact repository-authored controls, never model candidates.

Thin test configuration only: existing host, worker, IPC and profile unchanged.
There is no source/path argument and no external candidate execution interface.
"""
import argparse
import copy
import json
import platform
import time
from pathlib import Path

from headless_v01.compiler import compile_program
from headless_v01.types import Fault, canonical_json
from .adapter import EvaluationError,bounded_json
from .host import HostRuntime,MockClient,FAULTS,replay_host
from .node_backend import NodeBackend
from .pilot24_authored import TASKS,ROOT,abi,ir,js,write_json
from .pilot24_materials import read,sha

def authored_path(task,version,rep,mutant):
    if task not in TASKS or type(version)is not int or version not in (0,1) or type(mutant)is not int or mutant not in (0,1,2,3):
        raise EvaluationError('PILOT24_AUTHORED_CONTROL_ONLY')
    if version==0 and mutant:raise EvaluationError('PILOT24_AUTHORED_CONTROL_ONLY')
    if rep not in ('gameir_text','js_callback'):raise EvaluationError('PILOT24_AUTHORED_CONTROL_ONLY')
    suffix='.ir.json' if rep=='gameir_text' else '.js'
    if mutant:return ROOT/'private'/task/'mutants'/('m'+str(mutant)+suffix)
    return ROOT/('public' if version==0 else 'private')/task/(('starter' if version==0 else 'control_v1')+suffix)

def authored_source(task,version,rep,mutant=0):
    p=authored_path(task,version,rep,mutant)
    expected=ir(task,version,mutant) if rep=='gameir_text' else js(task,version,mutant)
    actual=read(p) if rep=='gameir_text' else p.read_text(encoding='utf-8')
    if actual!=expected:raise EvaluationError('PILOT24_AUTHORED_CONTROL_DRIFT')
    if len(p.read_bytes())>16384:raise EvaluationError('PILOT24_CONTROL_SIZE_LIMIT')
    return actual

class TrustedControlRunner:
    def __init__(self,task,version,mutant,backend):
        if type(backend)is not NodeBackend:raise EvaluationError('PILOT24_EXISTING_BACKEND_REQUIRED')
        self.task=task;self.version=version;self.mutant=mutant;self.backend=backend;self.audits=[]
        self.source=authored_source(task,version,'js_callback',mutant)

    def run(self,session,payload):
        if self.source!=authored_source(self.task,self.version,'js_callback',self.mutant):
            raise EvaluationError('PILOT24_AUTHORED_CONTROL_DRIFT')
        worker=self.backend.invocation(10);fault=None;started=time.monotonic()
        try:
            worker.open()
            boot={'v':'gir-node-bootstrap@1','invocation':session.invocation,'payload':payload,'purpose':'callback','source':self.source}
            worker.audit.update(reviewed_source_executions=1,generated_source_executions=0,
              trusted_material_source_sha256=sha(authored_path(self.task,self.version,'js_callback',self.mutant)),
              bootstrap_bytes=len(canonical_json(boot).encode())+1,max_response_bytes=0,max_request_bytes=0)
            worker.write(boot,bootstrap=True)
            while True:
                raw=worker.read_line();worker.audit['max_request_bytes']=max(worker.audit['max_request_bytes'],len(raw.encode())+1)
                try:frame=bounded_json(raw)
                except EvaluationError:session.receive(raw);raise AssertionError('Invalid frame accepted')
                if isinstance(frame,dict) and frame.get('transport')=='gir-node-exit@1':
                    worker.audit['node_exit_code']=frame.get('code');worker.audit['final_memory_events']=worker.memory_events()
                    if not session.closed or frame.get('code')!=0:raise Fault('JS_EXITED',phase='callback')
                    worker.release(frame);break
                reply=session.receive(raw)
                worker.audit['max_response_bytes']=max(worker.audit['max_response_bytes'],len(canonical_json(reply).encode())+1)
                worker.write(reply)
            worker.audit['terminal']='COMPLETED'
        except Fault as e:fault=e;worker.audit['terminal']=e.code
        except (EvaluationError,OSError,ValueError,KeyError,TypeError):
            fault=Fault('JS_EXITED',phase='callback');worker.audit['terminal']='ISOLATION_OR_TRANSPORT_REJECTED'
        finally:
            try:worker.close()
            except EvaluationError:fault=fault or Fault('JS_EXITED',phase='callback');worker.audit['terminal']='ISOLATION_CLEANUP_FAILED'
            worker.audit['elapsed_seconds']=time.monotonic()-started;self.audits.append(worker.audit)
        if fault is not None:
            if session.failed is not None:raise session.failed
            MockClient(session).fault(fault.code if fault.code in FAULTS else 'JS_EXITED')

class TrustedControlHost(HostRuntime):
    def __init__(self,task,initial,runner,seed):
        if type(runner)is not TrustedControlRunner:raise EvaluationError('PILOT24_TRUSTED_RUNNER_REQUIRED')
        super().__init__(abi(task),initial,seed=seed)
        self.runners={'open':runner};self._isolated=True

def observe_tick(runtime,result):
    return {'entities':runtime.snapshot()['entities'],'status':result['status'],
      'error':result['error']['code'] if result['error'] else None,
      'events':[{'name':e['name'],'payload':e['payload']} for e in result['events'] if not e['id'].startswith('root:')]
         if result['status']=='COMMITTED' else [],'commands':result['commands']}

def check_control(task,version,rep,case,backend=None,mutant=0):
    source=authored_source(task,version,rep,mutant);runner=None
    if rep=='gameir_text':runtime=HostRuntime(source,case['initial'],seed=case['seed']);program=source
    else:
        runner=TrustedControlRunner(task,version,mutant,backend)
        runtime=TrustedControlHost(task,case['initial'],runner,case['seed']);program=abi(task)
    actual=[]
    for step in case['steps']:
        result=runtime.step(**step);actual.append(observe_tick(runtime,result))
    replayed=replay_host(program,case['initial'],runtime.history,runtime.execution_history,seed=case['seed'],callback=rep=='js_callback')
    deterministic=replayed.snapshot()==runtime.snapshot()
    audits=runner.audits if runner else []
    clean=all(a.get('cleanup_errors')==[] and a.get('surviving_owned_processes')==0 and a.get('generated_source_executions')==0
              and a.get('terminal') not in {'ISOLATION_OR_TRANSPORT_REJECTED','ISOLATION_CLEANUP_FAILED'} for a in audits)
    match=actual==case['expected_ticks']
    return {'task_id':task,'version':version,'representation':rep,'mutant':mutant,'case_id':case['id'],
      'oracle_match':match,'replay_match':deterministic,'audits_clean':clean,
      'status':'PASS' if match and deterministic and clean else 'FAIL','actual_ticks':actual,
      'expected_ticks':case['expected_ticks'],'execution_history':runtime.execution_history,'worker_audits':audits}

def validate(output):
    destination=Path(output)
    if destination.exists():raise EvaluationError('PILOT24_PRESERVE_VALIDATION_EVIDENCE')
    destination.mkdir(parents=True)
    backend=NodeBackend();controls=[];negative=[]
    for task in TASKS:
        target_cases=read(ROOT/'private'/task/'cases.json');old_cases=read(ROOT/'private'/task/'v0_cases.json')
        for rep in ('gameir_text','js_callback'):
            for version,suite in ((0,old_cases),(1,target_cases)):
                for case in suite:
                    row=check_control(task,version,rep,case,backend)
                    controls.append(row)
                    if not row['audits_clean']:raise EvaluationError('PILOT24_ISOLATION_FAILURE_STOP')
                print(json.dumps({'task':task,'representation':rep,'version':version,'cases':len(suite),
                  'FAIL':sum(r['status']=='FAIL' for r in controls if r['task_id']==task and r['representation']==rep and r['version']==version)}),flush=True)
            for defect in read(ROOT/'private'/task/'mutants/index.json'):
                case=next(c for c in target_cases if c['id']==defect['case_id'])
                row=check_control(task,1,rep,case,backend,defect['index'])
                row['defect_intent']=defect['intent'];row['detected']=not row['oracle_match'] and row['replay_match'] and row['audits_clean']
                negative.append(row)
                if not row['audits_clean']:raise EvaluationError('PILOT24_ISOLATION_FAILURE_STOP')
        write_json(destination/(task+'.json'),{'controls':[r for r in controls if r['task_id']==task],
          'negative_controls':[r for r in negative if r['task_id']==task]})
    inventory=json.loads(backend._ctl('ps','--all','--filter','label=gir.reviewed-worker=phase3','--format=json'))
    audits=[a for r in controls+negative for a in r['worker_audits']]
    histories=[h for r in controls+negative for h in r['execution_history']]
    paired=all(next(r for r in controls if r['task_id']==t and r['version']==version and r['case_id']==c['id'] and r['representation']=='gameir_text')['actual_ticks']
      ==next(r for r in controls if r['task_id']==t and r['version']==version and r['case_id']==c['id'] and r['representation']=='js_callback')['actual_ticks']
      for t in TASKS for version,filename in ((0,'v0_cases.json'),(1,'cases.json')) for c in read(ROOT/'private'/t/filename))
    counts={s:sum(r['status']==s for r in controls) for s in ('PASS','FAIL','NOTRUN')}
    report={'schema':'gir-pilot24-trusted-material-validation@1','status':'PASS' if counts['FAIL']==0 and all(r['detected'] for r in negative) and paired and not inventory else 'FAIL',
      'python':platform.python_version(),'node':backend.lock['node_version'],'rootless_podman_version':backend._ctl('--version'),
      'command':'python3 -m ark_eval.pilot24_trusted --output '+str(destination),
      'control_cases':counts,'target_control_cases':{s:sum(r['status']==s and r['version']==1 for r in controls) for s in ('PASS','FAIL','NOTRUN')},
      'v0_control_cases':{s:sum(r['status']==s and r['version']==0 for r in controls) for s in ('PASS','FAIL','NOTRUN')},
      'negative_controls':{'DETECTED':sum(r['detected'] for r in negative),'MISSED':sum(not r['detected'] for r in negative),'NOTRUN':0},
      'paired_actual_observations_equal':paired,'replays':{'PASS':sum(r['replay_match'] for r in controls+negative),'FAIL':sum(not r['replay_match'] for r in controls+negative)},
      'runtime_maxima':{'host_operations_per_tick':max(h['host_operations_total'] for h in histories),
        'wire_messages_per_tick':max(h['callback_requests'] for h in histories),
        'wire_messages_per_handler':max(len(t['messages']) for h in histories for t in h['transcripts']),
        'bootstrap_bytes':max(a['bootstrap_bytes'] for a in audits),'request_line_bytes':max(a['max_request_bytes'] for a in audits),
        'response_line_bytes':max(a['max_response_bytes'] for a in audits)},
      'worker_audits':{'count':len(audits),'all_clean':all(not a['cleanup_errors'] and a['surviving_owned_processes']==0 for a in audits)},
      'independent_final_container_inventory':inventory,'real_model_calls':0,'generated_source_executions':0,
      'credential_reads':0,'send_enabled':False,'contract':'FIXED_SCENARIOS_ONLY_FULL_CONTRACT_NOT_CHECKED',
      'evidence_sha256':{task+'.json':sha(destination/(task+'.json')) for task in TASKS}}
    write_json(destination/'summary.json',report)
    print(json.dumps(report),flush=True)
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);args=p.parse_args()
    return 0 if validate(args.output)['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
