"""Trusted Pilot24 IR worker; exact inert source/ABI/case inside existing sandbox."""
import hashlib
import json
import sys

if __name__ == '__main__':
    sys.path.insert(0, '/gir-runtime')

from ark_eval.adapter import EvaluationError, bounded_json
from ark_eval.host import HostRuntime, replay_host
from headless_v01.compiler import compile_program, digest
from headless_v01.types import Fault


def observe_tick(runtime, result):
    return {'entities': runtime.snapshot()['entities'], 'status': result['status'],
            'error': result['error']['code'] if result['error'] else None,
            'events': [{'name': e['name'], 'payload': e['payload']} for e in result['events']
                       if not e['id'].startswith('root:')] if result['status'] == 'COMMITTED' else [],
            'commands': result['commands']}


def evaluate(boot, config):
    if (set(boot) != {'v', 'purpose', 'source', 'case'} or boot['v'] != 'gir-pair003-ir@1' or
            boot['purpose'] != 'ir_case' or not isinstance(boot['source'], str) or
            hashlib.sha256(boot['source'].encode()).hexdigest() != config['source_sha256']):
        raise EvaluationError('P24_WORKER_SOURCE_REJECTED')
    case = boot['case']
    if case is not None and (set(case) != {'id', 'initial', 'seed', 'steps'} or
            config['cases'].get(case['id']) != digest(case)):
        raise EvaluationError('P24_WORKER_CASE_REJECTED')
    identity = {'source_sha256': config['source_sha256'], 'abi_digest': digest(config['abi']),
                'case_id': None if case is None else case['id'], 'source_modified': False,
                'candidate_case_executions': 0, 'IR_case_replays': 0}
    try:
        document = bounded_json(boot['source'])
        header = json.loads(json.dumps(document))
        if isinstance(header, dict) and isinstance(header.get('handlers'), list):
            for handler in header['handlers']:
                if isinstance(handler, dict):
                    handler['body'] = []
        if digest(header) != digest(config['abi']):
            return {**identity, 'admission': 'REJECTED', 'admission_code': 'FAIL_ABI'}
        compile_program(document)
    except EvaluationError:
        return {**identity, 'admission': 'REJECTED', 'admission_code': 'FAIL_SYNTAX'}
    except Fault as exc:
        return {**identity, 'admission': 'REJECTED', 'admission_code': 'FAIL_COMPILE',
                'compiler_code': exc.code}
    if case is None:
        return {**identity, 'admission': 'PARSED'}
    runtime = HostRuntime(document, case['initial'], seed=case['seed'])
    observed = [observe_tick(runtime, runtime.step(**step)) for step in case['steps']]
    played = replay_host(document, case['initial'], runtime.history, runtime.execution_history,
                         seed=case['seed'], callback=False)
    return {**identity, 'admission': 'PARSED', 'candidate_case_executions': 1, 'IR_case_replays': 1,
            'observed': observed, 'snapshot_digest': digest(runtime.snapshot()),
            'replay_snapshot_digest': digest(played.snapshot()),
            'replay_match': played.snapshot() == runtime.snapshot(),
            'host_operations': [e['host_operations_total'] for e in runtime.execution_history],
            'history_digest': digest(runtime.history), 'execution_history_digest': digest(runtime.execution_history)}


if __name__ == '__main__':
    with open('/gir-pilot24-config.json', encoding='utf-8') as handle:
        config = bounded_json(handle.read())
    raw = sys.stdin.buffer.readline(32769)
    if len(raw) > 32768 or not raw.endswith(b'\n'):
        raise EvaluationError('P24_BOOT_SIZE_REJECTED')
    result = evaluate(bounded_json(raw.decode('utf-8')), config)
    print(json.dumps({'transport': 'gir-pilot24-ir-result@1', 'result': result},
                     ensure_ascii=False, separators=(',', ':')), flush=True)
