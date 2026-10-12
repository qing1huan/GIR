"""Offline public-only request builder; this module has no send operation."""
import argparse
import hashlib
import json
import math
import re
from pathlib import Path

from .adapter import ACCOUNT_MODEL, PlanConfig, prepare, EvaluationError, import_response, bounded_json

ROOT=Path(__file__).with_name('pilot24')
TASKS=('M1','M2','C1','C2','R1','R2')
REPS=('gameir_text','js_callback')
COMMON=('common_semantics.md','api_ir.md','api_js.md')

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))

def public_paths(task,rep):
    if task not in TASKS or rep not in REPS:raise EvaluationError('PILOT24_UNKNOWN_PUBLIC_ARM')
    root=ROOT/'public'
    return (root/'common_semantics.md',root/('api_ir.md' if rep==REPS[0] else 'api_js.md'),
       root/task/'requirements.md',root/task/'abi.json',root/task/('starter.ir.json' if rep==REPS[0] else 'starter.js'))

def request(task,rep):
    paths=public_paths(task,rep)
    # Only these five explicit paths can provide model-message content.
    texts=[p.read_text(encoding='utf-8') for p in paths]
    messages=[{'role':'system','content':texts[0]+'\n'+texts[1]},
      {'role':'user','content':texts[2]+'\nFIXED ABI (empty body denotes host header):\n'+texts[3]
        +'\nYOUR CORRECT V0 STARTER:\n'+texts[4]+'\nReturn only complete v1 source in your supplied representation.'}]
    result=prepare(messages,4096,PlanConfig(model=ACCOUNT_MODEL),thinking={'type':'disabled'})
    if result.prompt_units>24000:raise EvaluationError('PILOT24_INPUT_ESTIMATE_LIMIT')
    return result

def preview():
    schedule=read(ROOT.parents[1]/'docs/pilot24/schedule.json')
    arms=[]
    for task in TASKS:
        for rep in REPS:
            r=request(task,rep)
            arms.append({'task_id':task,'representation':rep,'public_request_id':task+'-'+rep,
             'request_digest':r.request_digest,'prompt_units_estimate':r.prompt_units,'input_units_are_tokens':False,
             'body':r.body,'source_paths':[str(p.relative_to(ROOT)) for p in public_paths(task,rep)]})
    mapping={a['public_request_id']:a['request_digest'] for a in arms}
    return {'schema':'gir-pilot24-offline-preview@1','status':'OFFLINE_REVIEW_REQUIRED',
      'send_enabled':False,'candidate_execution_enabled':False,'real_model_calls':0,
      'schedule_sha256':sha(ROOT.parents[1]/'docs/pilot24/schedule.json'),
      'maximum_attempts':24,'repairs':0,'tool_calls':0,'automatic_retries':0,'maximum_output_tokens':98304,
      'arms':arms,'slots':[{**s,'request_digest':mapping[s['public_request_id']]} for s in schedule['slots']]}

def metrics():
    rows=[]
    for task in TASKS:
        for kind,base in (('starter',ROOT/'public'/task),('control_v1',ROOT/'private'/task)):
            for suffix,rep in (('.ir.json',REPS[0]),('.js',REPS[1])):
                p=base/(kind+suffix);raw=p.read_text(encoding='utf-8')
                count=0
                def walk(x):
                    nonlocal count
                    if isinstance(x,dict):
                        count+=int('op' in x)
                        for val in x.values():walk(val)
                    elif isinstance(x,list):
                        for val in x:walk(val)
                if rep==REPS[0]:walk(json.loads(raw))
                # Conservative planning heuristic, not the service tokenizer.
                rows.append({'task_id':task,'kind':kind,'representation':rep,'sha256':sha(p),
                  'bytes':len(raw.encode()),'IR_op_nodes':count if rep==REPS[0] else None,
                  'planning_output_tokens_byte_divisor_3':math.ceil(len(raw.encode())/3),
                  'estimate_is_model_tokenizer':False,'actual_output_token_fit':'NOT_VERIFIED'})
    return rows

def audit_preview(data):
    """Evaluator-side leak checks, separate from public-only construction."""
    if len(data['arms'])!=12 or len(data['slots'])!=24:raise EvaluationError('PILOT24_PREVIEW_SHAPE')
    for arm in data['arms']:
        text='\n'.join(m['content'] for m in arm['body']['messages'])
        if re.search(r'\b[NBR]0[1-4]\b|expected_ticks|private/|mutants/',text):
            raise EvaluationError('PILOT24_PRIVATE_MARKER_LEAK')
        task=arm['task_id'];other='js_callback' if arm['representation']=='gameir_text' else 'gameir_text'
        counterpart=public_paths(task,other)[-1].read_text(encoding='utf-8').strip()
        if counterpart in text:raise EvaluationError('PILOT24_COUNTERPART_LEAK')
        for suffix in ('.ir.json','.js'):
            p=ROOT/'private'/task/('control_v1'+suffix)
            raw=p.read_text(encoding='utf-8').strip()
            if raw in text:raise EvaluationError('PILOT24_REFERENCE_LEAK')
            if suffix=='.ir.json' and json.dumps(read(p),separators=(',',':')) in text:
                raise EvaluationError('PILOT24_REFERENCE_LEAK')
        if arm['source_paths']!=[str(p.relative_to(ROOT)) for p in public_paths(task,arm['representation'])]:
            raise EvaluationError('PILOT24_PUBLIC_ALLOWLIST_DRIFT')
    return True

def collection_policy(http_status,raw,input_reservation_units=24000):
    """Inert draft policy for mock receipts. No sending, ledger, or execution."""
    row={'kind':'OFFLINE_POLICY_ONLY','generation_status':'OUTCOME_UNKNOWN','continue_fixed_schedule':False,
         'case_statuses':['NOTRUN']*12,'retry':False,'execute_source':False}
    if http_status is None:return row
    if type(http_status)is not int or http_status!=200:
        row['generation_status']='ERROR_PROVIDER';return row
    try:
        imported=import_response(raw,requested_model=ACCOUNT_MODEL)
        message=bounded_json(raw)['choices'][0]['message']
        reasoning=message.get('reasoning_content')
        if (not imported.model_matches_requested or imported.tokens_out>4096 or imported.tokens_in>input_reservation_units
            or (reasoning is not None and (not isinstance(reasoning,str) or reasoning))):
            row['generation_status']='INVALID_PROVIDER_PROTOCOL';return row
    except EvaluationError:
        row['generation_status']='INVALID_PROVIDER_PROTOCOL';return row
    row['candidate_digest']=imported.candidate_digest
    if imported.finish_reason=='length':row.update(generation_status='FAIL_TRUNCATED',continue_fixed_schedule=True)
    elif not imported.candidate.strip():row.update(generation_status='FAIL_EMPTY',continue_fixed_schedule=True)
    elif len(imported.candidate.encode())>16384:row.update(generation_status='FAIL_SOURCE_LIMIT',continue_fixed_schedule=True)
    else:row.update(generation_status='COLLECTED_UNREVIEWED',continue_fixed_schedule=True)
    return row

def candidate_check_policy(reason,known_defect=False):
    """Fail closed on environment/worker uncertainty; no actual admission API."""
    if known_defect is True and reason in {'SYNTAX','ABI','FUNCTIONAL'}:
        return {'generation_status':'FAIL_'+reason,'continue_fixed_schedule':True,'retry':False}
    return {'generation_status':'INVALID_EXPERIMENT','continue_fixed_schedule':False,'retry':False}

def write_preview():
    from .pilot24_authored import write_json
    data=preview();audit_preview(data)
    write_json(ROOT/'protocol.draft.json',data)
    write_json(ROOT/'output-feasibility.json',metrics())

def freeze(validation):
    """Seal offline materials; no credential or candidate execution approval."""
    report=read(validation)
    if report.get('status')!='PASS' or report.get('real_model_calls')!=0:
        raise EvaluationError('PILOT24_TRUSTED_VALIDATION_REQUIRED')
    files=[p for directory in ('public','private') for p in (ROOT/directory).rglob('*') if p.is_file()]
    files += [ROOT/'protocol.draft.json',ROOT/'output-feasibility.json',ROOT/'README.md']
    repo=ROOT.parents[1]
    files += [repo/'docs/pilot24'/n for n in ('DESIGN.md','TASKS.md','HANDOFF_SOL.md','schedule.json')]
    files += [repo/'ark_eval'/n for n in ('pilot24_authored.py','pilot24_oracles.py','pilot24_materials.py','pilot24_trusted.py')]
    files += [repo/'ark_eval/tests'/n for n in ('test_pilot24_materials.py','test_pilot24_trusted_controls.py')]
    bindings={str(p.relative_to(repo)).replace('\\','/'):sha(p) for p in sorted(files)}
    if bindings!=report.get('material_sha256'):
        raise EvaluationError('PILOT24_VALIDATION_MATERIAL_DRIFT')
    if any(sha(repo/n)!=h for n,h in report.get('evidence_sha256',{}).items()):
        raise EvaluationError('PILOT24_VALIDATION_EVIDENCE_DRIFT')
    # Bind existing runtime/worker/profile files without modifying the old freeze.
    old=read(repo/'ark_eval/v4_execution.freeze.json')
    # Reuse the whole existing closure, including transitively imported budget/tasks.
    # Conservative binding does not grant any old candidate/execution permission.
    runtime=dict(old['bindings'])
    if any(sha(repo/n)!=h for n,h in runtime.items()):raise EvaluationError('PILOT24_RUNTIME_DRIFT')
    spec={'schema':'gir-pilot24-materials-freeze@1','status':'MATERIALS_SEALED_PENDING_INDEPENDENT_REVIEW',
      'send_enabled':False,'candidate_execution_enabled':False,'bindings':bindings,'runtime_bindings':runtime,
      'trusted_validation_path':str(Path(validation).resolve().relative_to(repo)).replace('\\','/'),
      'trusted_validation_sha256':sha(Path(validation)),'model_generations':0,'sample_units':6,
      'validation_evidence_sha256':report['evidence_sha256'],
      'planned_slots':24,'planned_case_definitions':72,'planned_primary_evaluations':288,
      'input_units_are_tokens':False,'output_token_fit':'HEURISTIC_ONLY_NOT_VERIFIED',
      'old_v4_freeze_sha256':sha(repo/'ark_eval/v4_execution.freeze.json')}
    from headless_v01.compiler import digest
    spec['freeze_digest']=digest(spec)
    from .pilot24_authored import write_json
    if (ROOT/'freeze.json').exists():raise EvaluationError('PILOT24_FREEZE_ALREADY_EXISTS')
    write_json(ROOT/'freeze.json',spec)
    return spec['freeze_digest']

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--preview',action='store_true')
    p.add_argument('--freeze-after-validation');args=p.parse_args()
    if args.preview:write_preview();print(json.dumps({'status':'OFFLINE_PREVIEW','real_model_calls':0,'send_enabled':False}))
    elif args.freeze_after_validation:print(json.dumps({'freeze_digest':freeze(args.freeze_after_validation),'send_enabled':False}))
    else:p.error('Choose an offline material operation')

if __name__=='__main__':main()
