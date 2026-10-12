"""Trusted fixed IR worker. Original Core is inert JSON interpreted by the engine.

This file is baked into a read-only no-mount/no-network image. No JS loader,
credential handling, filesystem input, model call or candidate repair path.
"""
import hashlib
import json
import sys

sys.path.insert(0,'/gir-runtime')
from ark_eval.adapter import bounded_json
from ark_eval.oracle import observations
from headless_v01.compiler import compile_program,digest
from headless_v01.runtime import Runtime,replay

SOURCE_SHA256='9cd7ea46ba86790a5413c78e5bd5846c538b75cfb65f6713c5596a47f1e87b42'


class AuditedRuntime(Runtime):
    def __init__(self,*args,**kwargs):
        self.dispatches=0;self.audit=[]
        super().__init__(*args,**kwargs);self.host_operation_limit=128

    def execute_job(self,job,body,payload):
        self.dispatches+=1
        return super().execute_job(job,body,payload)

    def capture_execution(self,txn,result):
        self.audit.append({'host_operations':dict(txn.host_counts),'result_digest':digest(result)})


def evaluate(boot):
    if (set(boot)!={'v','purpose','source','case'} or boot['v']!='gir-pair003-ir@1'
            or boot['purpose']!='ir_case' or not isinstance(boot['source'],str)
            or hashlib.sha256(boot['source'].encode('utf-8')).hexdigest()!=SOURCE_SHA256):
        raise ValueError('FIXED_IR_BOOT_REJECTED')
    case=boot['case']
    if set(case)!={'id','initial','seed','steps'}:raise ValueError('CASE_KEYS_REJECTED')
    core=compile_program(bounded_json(boot['source']))
    runtime=AuditedRuntime(core,case['initial'],seed=case['seed'])
    results=[runtime.step(**step) for step in case['steps']]
    played=replay(core,case['initial'],runtime.history,seed=case['seed'])
    return {'case_id':case['id'],'source_sha256':SOURCE_SHA256,'python':sys.version.split()[0],
        'observed':observations(runtime,results),'snapshot':runtime.snapshot(),
        'replay_snapshot_digest':digest(played.snapshot()),'snapshot_digest':digest(runtime.snapshot()),
        'replay_match':played.snapshot()==runtime.snapshot(),'history_digest':digest(runtime.history),
        'step_count':len(results),'handler_dispatches':runtime.dispatches,'step_audits':runtime.audit,
        'host_operations_bounded':all(sum(a['host_operations'].values())<=128 for a in runtime.audit),
        'source_modified':False,'JS_evaluations':0,'candidate_case_executions':1,'candidate_replay_case_executions':1}


if __name__=='__main__':
    raw=sys.stdin.buffer.readline(32769)
    if len(raw)>32768 or not raw.endswith(b'\n'):raise ValueError('BOOT_BYTES_REJECTED')
    result=evaluate(bounded_json(raw.decode('utf-8')))
    print(json.dumps({'transport':'gir-pair003-ir-result@1','result':result},ensure_ascii=False,separators=(',',':')),flush=True)
