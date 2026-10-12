"""Exact authored controls through new adapters; no external-candidate entry."""
import argparse
import json
import platform
import time
from pathlib import Path

from .adapter import EvaluationError
from .candidate_eval import fresh_isolation
from .candidate_parse_backend import CandidateParserBackend, parse_module
from .node_backend import NodeBackend
from .pilot24_execution_scope import TrustedPilotControl, FreshPilotIsolation, read, parse_status, require
from .pilot24_execution_backend import Pilot24IRBackend, ir_call
from .pilot24_execution_eval import fresh, evaluate_ir, evaluate_js, write_json, validate_output


def validate(output):
    output = validate_output(output)
    output.mkdir(parents=True, exist_ok=False)
    node = NodeBackend()
    parser = CandidateParserBackend()
    checks = []
    paired = {}
    js_precheck = output / 'JS-precheck'
    js_precheck.mkdir()
    _, profile, faults = fresh_isolation(node, js_precheck)
    proven_at = time.monotonic()
    for task in ('M1', 'M2', 'C1', 'C2', 'R1', 'R2'):
        defects = read('ark_eval/pilot24/private/' + task + '/mutants/index.json')
        for mutant in (0, 1, 2, 3):
            for rep in ('gameir_text', 'js_callback'):
                scope = TrustedPilotControl(task, rep, mutant)
                sid = scope._slot['slot_id']
                cases = scope.cases[task] if mutant == 0 else [next(c for c in scope.cases[task]
                         if c['id'] == next(d for d in defects if d['index'] == mutant)['case_id'])]
                if rep == 'gameir_text':
                    backend = Pilot24IRBackend(scope, sid)
                    proof = fresh(scope, backend, rep, output / ('precheck-' + sid))
                    parsed = ir_call(scope, sid, backend, proof)
                    require(parsed['result']['admission'] == 'PARSED', 'P24_TRUSTED_IR_CONTROL_REJECTED')
                else:
                    if time.monotonic() - proven_at > 500:
                        folder = output / ('JS-refresh-' + sid)
                        folder.mkdir()
                        _, profile, faults = fresh_isolation(node, folder)
                        proven_at = time.monotonic()
                    backend = node
                    proof = FreshPilotIsolation(scope, node, profile, faults, rep)
                    proof.created = proven_at  # Preserve real profile age; do not freshen cached evidence.
                    parsed = parse_module(scope.source(sid), parser)
                    require(parse_status(scope, sid, parsed) == 'PARSED', 'P24_TRUSTED_JS_CONTROL_REJECTED')
                write_json(output / ('admission-' + sid + '.json'), parsed)
                for case in cases:
                    result = (evaluate_ir(scope, sid, backend, proof, case) if rep == 'gameir_text'
                              else evaluate_js(scope, sid, backend, proof, parsed, case))
                    require(result['candidate_executions'] == 0, 'P24_CONTROL_EXTERNAL_EXECUTION_FORBIDDEN')
                    expected_status = 'PASS' if mutant == 0 else 'FAIL'
                    row = {'task_id': task, 'rep': rep, 'mutant': mutant, 'case_id': case['id'],
                           'check': 'PASS' if result['status'] == expected_status else 'FAIL', **result}
                    checks.append(row)
                    write_json(output / (sid + '-' + case['id'] + '.json'), row)
                    if mutant == 0:
                        paired[task, rep, case['id']] = result['actual_ticks']
                print(json.dumps({'trusted_control': sid, 'cases': len(cases),
                                  'new_model_calls': 0, 'generated_source_executions': 0}), flush=True)
    equal = all(paired[t, 'gameir_text', c['id']] == paired[t, 'js_callback', c['id']]
                for t in ('M1', 'M2', 'C1', 'C2', 'R1', 'R2')
                for c in read('ark_eval/pilot24/private/' + t + '/cases.json'))
    remaining = json.loads(node._ctl('ps', '--all', '--filter', 'label=gir.reviewed-worker=phase3', '--format=json'))
    report = {'kind': 'PILOT24_NEW_ADAPTER_AUTHORED_CONTROLS_ONLY', 'python': platform.python_version(),
              'node': node.lock['node_version'], 'podman': node._ctl('--version'),
              'positive_controls': {s: sum(r['mutant'] == 0 and r['check'] == s for r in checks)
                                    for s in ('PASS', 'FAIL')},
              'negative_controls': {'DETECTED': sum(r['mutant'] != 0 and r['check'] == 'PASS' for r in checks),
                                    'MISSED': sum(r['mutant'] != 0 and r['check'] == 'FAIL' for r in checks)},
              'paired_observations_equal': equal, 'remaining_containers': remaining,
              'replays': sum(r['IR_case_replays'] + r['JS_transcript_replays'] for r in checks),
              'real_model_calls': 0, 'generated_source_executions': 0,
              'status': 'PASS' if equal and not remaining and all(r['check'] == 'PASS' for r in checks) else 'FAIL'}
    write_json(output / 'summary.json', report)
    print(json.dumps(report), flush=True)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    return 0 if validate(args.output)['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
