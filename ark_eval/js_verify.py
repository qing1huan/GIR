"""Phase 3 engineering: same oracle, actual reviewed Node callbacks, no AI calls."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from headless_v01.types import Fault
from .adapter import bounded_json
from .callback_contract import callback_protocol, model_records
from .callback_verify import callback_manifest
from .host import HostRuntime, ScriptedMockRunner, replay_host
from .js_runner import IsolatedJSRunner
from .node_backend import NodeBackend
from .oracle import SUITE, final_cases, observations
from .reference import control_program
from .tasks import DOOR, PLAYER, TASKS, base_program


def save(output, name, data):
    (output / (name + '.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def source_digest():
    root = Path(__file__).resolve().parents[1]
    files = [p for directory in ('ark_eval','headless_v01') for p in (root/directory).rglob('*')
             if p.is_file() and p.suffix in ('.py','.mjs','.sh')]
    files += [root/'ark_eval/final_tests.json',root/'ark_eval/node_image_lock.json']
    entries = {str(p.relative_to(root)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(files)}
    return hashlib.sha256(json.dumps(entries,sort_keys=True).encode()).hexdigest(), entries


def git_metadata():
    # WSL DrvFs ownership may prevent Git inspection. Do not relax safe.directory
    # or change user configuration merely to populate optional evidence metadata.
    try:
        commit = subprocess.check_output(['git','rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(['git','status','--porcelain','--','ark_eval','headless_v01'],text=True,stderr=subprocess.DEVNULL))
        return {'source_commit':commit,'dirty':dirty,'git_metadata':'AVAILABLE'}
    except (FileNotFoundError,subprocess.CalledProcessError):
        return {'source_commit':None,'dirty':None,'git_metadata':'UNAVAILABLE_NO_CONFIG_CHANGED'}


def profile_probe(backend):
    directory = Path(tempfile.mkdtemp(prefix='gir-node-host-canary-'))
    canary = directory / 'canary'; canary.write_text('fixture-only-not-a-secret\n')
    original = dict(backend.environment)
    backend.environment.update(GIR_NON_SECRET_CANARY='fixture-only-not-a-secret',
                               HTTP_PROXY='http://127.0.0.1:9', HTTPS_PROXY='http://127.0.0.1:9')
    worker = backend.invocation(10)
    try:
        worker.open()
        worker.write({'v':'gir-node-bootstrap@1','purpose':'profile_probe','canary_path':str(canary)}, bootstrap=True)
        message = bounded_json(worker.read_line())
        if set(message) != {'transport','result'} or message['transport'] != 'gir-node-probe@1':
            raise Fault('ProtocolViolation')
        result = message['result']
        worker.release(bounded_json(worker.read_line()))
    finally:
        try: worker.close()
        finally:
            backend.environment = original; canary.unlink(); directory.rmdir()
    cpu_fraction = result['cpu_usage_usec'] / (result['elapsed_ms'] * 1000)
    checks = {
        'rootless': result['uid'] == os.getuid() != 0 and backend.host['rootless'],
        'network_denied': result['network_denial'] == 'ENETUNREACH' and
                          result['network_namespace'] != os.readlink('/proc/self/ns/net'),
        'no_user_mounts': result['canary_unreachable'] and result['windows_mounts_absent'] and not worker.audit['user_mounts'],
        'environment_scrubbed': result['no_host_environment'],
        'cpu_limits': result['cpu_max'] == '25000 100000' and result['throttled_periods'] > 0 and cpu_fraction <= .45,
    }
    return {'checks': {key:'PASS' if value else 'FAIL' for key,value in checks.items()},
            'result':result, 'cpu_fraction':cpu_fraction, 'actual_worker_audit':worker.audit}


def control_case(backend, task_id, case, inverted=False):
    program = callback_manifest()
    runner = IsolatedJSRunner('wrong_compare' if inverted else TASKS[task_id]['kind'].lower(), backend)
    runtime = HostRuntime(program, case['initial'], seed=case['seed'], runners={'open':runner})
    results = [runtime.step(**step) for step in case['steps']]
    matched = observations(runtime, results) == case['expected']
    played = replay_host(program, case['initial'], runtime.history, runtime.execution_history, seed=case['seed'])
    replayed = played.snapshot() == runtime.snapshot()
    ir_program = control_program(task_id, inverted=inverted)
    ir = HostRuntime(ir_program, case['initial'], seed=case['seed'])
    ir_results = [ir.step(**step) for step in case['steps']]
    ir_match = observations(ir, ir_results) == case['expected']
    ir_played = replay_host(ir_program, case['initial'], ir.history, ir.execution_history, seed=case['seed'], callback=False)
    ir_replay = ir_played.snapshot() == ir.snapshot()
    cost_equal = ([e['host_operations'] for e in runtime.execution_history] ==
                  [e['host_operations'] for e in ir.execution_history])
    input_keys_ok = all(a['input_keys'] == ['invocation','payload','purpose','source','v'] for a in runner.audits)
    clean = all(not a['cleanup_errors'] and a['surviving_owned_processes'] == 0 for a in runner.audits)
    js_record = {'representation':'js_callback', 'case_id':case['id'],
                 'status':'PASS' if matched and replayed and cost_equal and input_keys_ok and clean else 'FAIL',
                 'oracle_match':matched, 'replay_match':replayed, 'host_cost_equal':cost_equal,
                 'necessary_input_only':input_keys_ok, 'results':results,
                 'execution_audits':runtime.execution_history, 'worker_audits':runner.audits}
    ir_record = {'representation':'gameir_text','case_id':case['id'],
                 'status':'PASS' if ir_match and ir_replay else 'FAIL',
                 'oracle_match':ir_match,'replay_match':ir_replay,'results':ir_results,
                 'execution_audits':ir.execution_history}
    return ir_record, js_record


def fault_case(backend, name):
    program = base_program(); program['events'].update({
        'Prep':{'record':{}}, 'Bad':{'record':{'player':'EntityID','door':'EntityID'}}, 'Later':{'record':{}}})
    program['handlers'] = [{'id':'prep','event':'Prep','priority':0,'body':[]},
                           {'id':'bad','event':'Bad','priority':0,'body':[]}]
    def frame(op, **args): return {'op':op,'args':args}
    prep = ScriptedMockRunner((frame('write',entity=PLAYER,field='keys',value=1),
                              frame('draw',bound=10), frame('schedule',event='Later',payload={},delay=2),
                              frame('emit',event='Opened',payload={'door':DOOR})))
    runner = IsolatedJSRunner(name, backend, timeout=2 if name in ('spin_after_write','tree_timeout') else 10)
    initial = [{'id':PLAYER,'values':{'keys':2}},{'id':DOOR,'values':{'open':False}}]
    runtime = HostRuntime(program, initial, seed=19, runners={'prep':prep,'bad':runner})
    before = runtime.snapshot()
    try:
        result = runtime.step(inputs=[{'name':'Prep','payload':{}}, {'name':'Bad','payload':{'player':PLAYER,'door':DOOR}}])
    except Exception as error:
        runner.infrastructure_error(error, 'fixture_runtime')
        return {'fixture':name, 'status':'HARNESS_ERROR',
                'first_infrastructure_exception':runner.first_infrastructure_exception,
                'worker_audit':runner.audits[-1] if runner.audits else None,
                'diagnostic_gap':'WORKER_AUDIT_UNAVAILABLE' if not runner.audits else None}
    audit = runtime.execution_history[0]
    rolled_back = runtime.snapshot() == before
    replay_status = 'PASS'; rejection_assertion = None
    try:
        played = replay_host(program, initial, runtime.history, runtime.execution_history, seed=19)
        if played.snapshot() != before: replay_status = 'FAIL'
    except Fault as error:
        # Verify the specified refusal, not a fabricated canonical replay.
        if name == 'duplicate_key':
            diagnostics = [m for t in audit['transcripts'] for m in t['messages'] if m['request'] is None]
            denied = (error.code == 'ReplayUnsupported' and
                      error.details == {'reason':'uncanonicalized_raw_frame'} and len(diagnostics) == 1 and
                      diagnostics[0].get('rejection') == {'kind':'INVALID_RAW_FRAME','parser_code':'DUPLICATE_JSON_KEY'})
            rejection_assertion = {'status':'PASS' if denied else 'FAIL','observed_code':error.code,
                                   'observed_details':error.details,'canonical_replay_performed':False}
            replay_status = 'REJECTED_AS_SPECIFIED' if denied else 'FAIL'
        else: replay_status = 'FAIL'
    if not runner.audits:
        backend.disabled = True
        return {'fixture':name, 'status':'HARNESS_ERROR', 'worker_audit':None,
                'first_infrastructure_exception':runner.first_infrastructure_exception,
                'diagnostic_gap':'FIRST_EXCEPTION_NOT_RECORDED_AND_WORKER_AUDIT_UNAVAILABLE'}
    worker = runner.audits[-1]
    if runner.first_infrastructure_exception is not None:
        # Do not inspect incomplete resource fields and replace the first exception.
        return {'fixture':name, 'status':'HARNESS_ERROR',
                'first_infrastructure_exception':runner.first_infrastructure_exception,
                'before_snapshot':before,'after_snapshot':runtime.snapshot(),'root_rollback':rolled_back,
                'result':result,'replay_status':replay_status,
                'replay_rejection_assertion':rejection_assertion,'execution_audit':audit,'worker_audit':worker}
    requirements = [result['status'] == 'ABORTED', rolled_back, not audit['published_events'],
                    not audit['published_commands'], bool(result['effects']),
                    replay_status in ('PASS','REJECTED_AS_SPECIFIED'),
                    not worker['cleanup_errors'], worker.get('surviving_owned_processes') == 0,
                    runner.first_infrastructure_exception is None]
    if name in ('spin_after_write','tree_timeout'):
        requirements.append(result['error']['code'] == 'JS_TIMEOUT')
    if name == 'disconnect': requirements.append(result['error']['code'] == 'JS_DISCONNECTED')
    if name == 'tree_timeout':
        requirements.append(type(worker.get('terminated_tree_processes')) is int and
                            worker['terminated_tree_processes'] >= 4)
    if name == 'oom_after_write':
        before_events,after_events = worker.get('initial_memory_events'),worker.get('final_memory_events')
        before_oom = before_events.get('oom_kill') if type(before_events) is dict else None
        after_oom = after_events.get('oom_kill') if type(after_events) is dict else None
        requirements.extend([worker.get('node_exit_code') == 137,
                             type(after_oom) is int and type(before_oom) is int and after_oom > before_oom])
    if name in ('unknown_op','injected_key','duplicate_key'):
        requirements.append(result['error']['code'] == 'ProtocolViolation')
    if name == 'swallow_host_error': requirements.append(result['error']['code'] == 'TypeError')
    return {'fixture':name, 'status':'HARNESS_ERROR' if runner.first_infrastructure_exception else
            'PASS' if all(requirements) else 'FAIL',
            'first_infrastructure_exception':runner.first_infrastructure_exception,
            'before_snapshot':before,'after_snapshot':runtime.snapshot(),'root_rollback':rolled_back,
            'result':result,'replay_status':replay_status,'replay_rejection_assertion':rejection_assertion,
            'execution_audit':audit,'worker_audit':worker}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',required=True)
    args = parser.parse_args(argv); output = Path(args.output).resolve()
    if output.exists(): parser.error('Preserve prior evidence; choose a new output directory.')
    output.mkdir(parents=True)
    source_before, source_files = source_digest()
    save(output,'source-files',source_files)
    backend = NodeBackend(); profile = profile_probe(backend); save(output,'actual-node-profile',profile)
    fault_names = ['throw_after_write','spin_after_write','oom_after_write','disconnect','unknown_op',
                   'injected_key','oversized_line','stdout_flood','stderr_flood','duplicate_key',
                   'swallow_host_error','tree_timeout']
    faults = []
    for name in fault_names:
        faults.append(fault_case(backend,name)); save(output,'faults',faults)
        print(json.dumps({'progress':'fault_fixture','fixture':name,'status':faults[-1]['status']}),flush=True)
        if backend.disabled: raise SystemExit('Isolation cleanup failed; no further execution allowed')
    by_name = {f['fixture']:f for f in faults}
    profile['checks']['memory_limits'] = by_name['oom_after_write']['status']
    profile['checks']['process_tree_termination'] = by_name['tree_timeout']['status']
    save(output,'actual-node-profile',profile)
    if any(value != 'PASS' for value in profile['checks'].values()) or any(f['status'] != 'PASS' for f in faults):
        save(output,'blocked',{'kind':'FAIL_CLOSED_NO_MATRIX_EXECUTION','profile':profile['checks'],
                               'fault_counts':{s:sum(f['status']==s for f in faults) for s in ('PASS','FAIL')}})
        return 1
    controls = []; bugs = []
    for task_id in TASKS:
        for index,case in enumerate(final_cases(task_id)):
            controls.extend(control_case(backend,task_id,case))
            if index % 8 == 7: print(json.dumps({'progress':'matrix','task':task_id,'cases_complete':index+1}),flush=True)
        save(output,'controls',controls)
    for case in final_cases('P-C02-debug'): bugs.extend(control_case(backend,'P-C02-debug',case,inverted=True))
    save(output,'known-bug-controls',bugs)
    audits = [audit for control in controls+bugs for audit in control.get('worker_audits',[])]
    audits.extend(f['worker_audit'] for f in faults)
    source_after, _ = source_digest()
    summary = {'kind':'PHASE3_REVIEWED_REFERENCE_ENGINEERING_NOT_AI_EVALUATION', **git_metadata(),
               'source_digest':source_before,'source_drift_during_run':source_after != source_before,
               'python':sys.version,'node_image_lock':backend.lock,'worker_image':backend.worker_image,
               'protocol':callback_protocol(),'final_suite_sha256':hashlib.sha256(SUITE.read_bytes()).hexdigest(),
               'control_counts':{s:sum(c['status']==s for c in controls) for s in ('PASS','FAIL','NOTRUN')},
               'known_bug_counts':{s:sum(c['status']==s for c in bugs) for s in ('PASS','FAIL','NOTRUN')},
               'fault_counts':{s:sum(f['status']==s for f in faults) for s in ('PASS','FAIL','NOTRUN')},
               'actual_node_isolation_counts':{s:sum(v==s for v in profile['checks'].values()) for s in ('PASS','FAIL','NOTRUN')},
               'reviewed_callback_source_executions':sum(a['reviewed_source_executions'] for a in audits),
               'audited_worker_invocations':len(audits)+1,'model_generated_source_executions':0,
               'real_model_calls':0,'reset_cards_used':0,'model_counts':{'PASS':0,'FAIL':0,'NOTRUN':6},
               'a12':'NOT_RUN','ir_frozen':False,'ts_status':'DEFERRED','plan_permission':'UNKNOWN',
               'wall_timeout_is_not_cpu_equivalence':True,'js_compute_units':None,
               'raw_invalid_frame_replay':'REJECTED_AS_SPECIFIED; deterministic ReplayUnsupported verified, no canonical replay fabricated',
               'all_owned_containers_cleaned':all(not a['cleanup_errors'] for a in audits),
               'reference_answers_not_model_prompt_input':True}
    save(output,'summary',summary); save(output,'model_runs',model_records())
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    return int(summary['control_counts']['FAIL'] != 0 or summary['known_bug_counts']['FAIL'] != 16
               or summary['source_drift_during_run'])


if __name__ == '__main__': raise SystemExit(main())
