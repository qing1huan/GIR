"""Pilot24 scoring entry: prepare by default; new exact receipt required to run."""
import argparse
import json
import os
import time
from pathlib import Path

from headless_v01.compiler import digest
from headless_v01.types import Fault
from .adapter import EvaluationError
from .candidate_eval import collect_isolation_evidence
from .candidate_parse_backend import CandidateParserBackend, parse_module
from .candidate_runner import replay_candidate
from .js_verify import profile_probe
from .pair003_eval import resource_fixture
from .node_backend import NodeBackend
from .pilot24_execution_backend import Pilot24IRBackend, Pilot24JSRunner, Pilot24Host, ir_call
from .pilot24_execution_worker import observe_tick
from .pilot24_execution_scope import (ROOT, APPROVAL, ApprovedPilot24Scope, FreshPilotIsolation,
    load_manifest, static_manifest, case_sets, parse_status, read, require)

CLAIM_ROOT = ROOT / 'results/pilot24-execution-claims'


def write_json(path, value):
    # Exclusive, flushed evidence: failed persistence never releases a claim.
    with Path(path).open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())


def validate_output(output):
    output = Path(output).absolute()
    require(output.resolve() == output and not any(p.is_symlink() for p in
            (output, *output.parents, CLAIM_ROOT, *CLAIM_ROOT.parents)), 'P24_EVIDENCE_PATH_REJECTED')
    if output.exists():
        raise FileExistsError('Fresh output required')
    require(all(not p.exists() or p.is_dir() for p in (*output.parents, CLAIM_ROOT, *CLAIM_ROOT.parents)),
            'P24_EVIDENCE_PARENT_REJECTED')
    require(not output.is_relative_to(CLAIM_ROOT) and not CLAIM_ROOT.is_relative_to(output),
            'P24_OUTPUT_CLAIM_OVERLAP')
    return output


class ExecutionClaim:
    def __init__(self, scope, output):
        require(type(scope) is ApprovedPilot24Scope, 'P24_APPROVED_SCOPE_REQUIRED')
        scope.require()
        output = validate_output(output)
        self.path = CLAIM_ROOT / scope.manifest['batch_key']
        self.sequence = 0
        CLAIM_ROOT.mkdir(parents=True, exist_ok=True)
        try:
            self.path.mkdir()
        except FileExistsError:
            raise EvaluationError('P24_BATCH_ALREADY_CLAIMED') from None
        write_json(self.path / 'claim.json', {'schema': 'gir-pilot24-one-use-execution@1',
            'batch_key': scope.manifest['batch_key'], 'scope_digest': digest(scope.manifest),
            'approval_digest': scope.approval_digest, 'output': str(output),
            'state': 'CLAIMED_NOT_STARTED', 'retry_or_resume_allowed': False})

    def event(self, state, **details):
        self.sequence += 1
        write_json(self.path / ('event-%04d.json' % self.sequence), {'state': state, **details})


def fresh(scope, backend, rep, output):
    output.mkdir(parents=True, exist_ok=False)
    if rep == 'js_callback':
        profile, faults = collect_isolation_evidence(backend, output)
    else:
        profile = profile_probe(backend)
        write_json(output / 'profile-before.json', profile)
        faults = []
        for name in ('spin', 'oom', 'tree'):
            row = resource_fixture(backend, name)
            faults.append(row)
            write_json(output / (name + '.json'), row)
            require(row['status'] == 'PASS' and not backend.disabled, 'P24_IR_PRECHECK_FAILED')
        profile['checks']['memory_limits'] = next(f['status'] for f in faults if f['fixture'] == 'oom')
        profile['checks']['process_tree_termination'] = next(f['status'] for f in faults if f['fixture'] == 'tree')
        write_json(output / 'profile.json', profile)
    return FreshPilotIsolation(scope, backend, profile, faults, rep)


def evaluate_js(scope, slot_id, backend, proof, parsed, case, refresh=None):
    scope.case(slot_id, case)
    runner = (Pilot24JSRunner(scope, slot_id, backend, proof, parsed) if refresh is None else
              Pilot24JSRunner(scope, slot_id, backend, proof, parsed, refresh=refresh))
    program = scope.abi(slot_id)
    runtime = Pilot24Host(scope, slot_id, case['initial'], runner, case['seed'])
    observed = [observe_tick(runtime, runtime.step(**step)) for step in case['steps']]
    healthy = (runner.infrastructure_failure is None and not backend.disabled and
            all(a['cleanup_errors'] == [] and a['surviving_owned_processes'] == 0 and
            a['terminal'] not in {'P24_ISOLATION_OR_TRANSPORT_REJECTED', 'ISOLATION_CLEANUP_FAILED',
                                  'JS_DISCONNECTED'} for a in runner.audits))
    if not healthy:
        error = EvaluationError('P24_JS_INFRA_OR_CLEANUP_FAILED')
        error.first_infrastructure_exception = runner.first_infrastructure_exception
        raise error
    played = replay_candidate(program, case, runtime.history, runtime.execution_history)
    replayed = played.snapshot() == runtime.snapshot()
    require(replayed, 'P24_JS_REPLAY_UNVERIFIED')
    return {'status': 'PASS' if observed == case['expected_ticks'] else 'FAIL',
            'oracle_match': observed == case['expected_ticks'], 'replay_match': replayed,
            'actual_ticks': observed, 'expected_ticks': case['expected_ticks'],
            'worker_audits': runner.audits, 'execution_history': runtime.execution_history,
            'primary_case_executions': 1, 'JS_transcript_replays': 1, 'IR_case_replays': 0,
            'candidate_executions': sum(a['generated_source_executions'] for a in runner.audits),
            'trusted_executions': sum(a['reviewed_source_executions'] for a in runner.audits),
            'cleanup_verified': True}


def evaluate_ir(scope, slot_id, backend, proof, case, refresh=None):
    evidence = (ir_call(scope, slot_id, backend, proof, case) if refresh is None else
                ir_call(scope, slot_id, backend, proof, case, refresh=refresh))
    r = evidence['result']
    require(r['admission'] == 'PARSED', 'P24_IR_ADMISSION_CHANGED_AFTER_PARSE')
    match = r['observed'] == case['expected_ticks']
    return {'status': 'PASS' if match else 'FAIL', 'oracle_match': match, 'replay_match': r['replay_match'],
            'actual_ticks': r['observed'], 'expected_ticks': case['expected_ticks'],
            'isolated_result': r, 'worker_audit': evidence['worker_audit'], 'cleanup_verified': True,
            'primary_case_executions': 1, 'IR_case_replays': 1, 'JS_transcript_replays': 0,
            'candidate_executions': int(scope.generated), 'trusted_executions': int(not scope.generated)}


def planned_rows(manifest, cases):
    return [{'slot_id': s['slot_id'], 'slot_index': s['index'], 'task_id': s['task_id'],
             'representation': s['representation'], 'repetition': s['repetition'], 'case_id': c['id'],
             'group': c['group'], 'seed': c['seed'], 'source_sha256': s['source_sha256'],
             'status': 'NOTRUN', 'reason': s['static_rejection'] or 'NOT_STARTED',
             'phase': 'STATIC_REJECTED' if s['static_rejection'] else 'NOT_STARTED',
             'primary_case_executions': 0, 'candidate_executions': 0,
             'IR_case_replays': 0, 'JS_transcript_replays': 0}
            for s in manifest['slots'] for c in cases[s['task_id']]]


def scoring(rows, admissions):
    by_slot = []
    for a in admissions:
        r = [r for r in rows if r['slot_id'] == a['slot_id']]
        category = ('FAIL' if a['status'].startswith('FAIL_') else
                    'ERROR' if a['status'] == 'BLOCKED' else
                    'PASS' if all(x['status'] == 'PASS' for x in r) else
                    'FAIL' if any(x['status'] == 'FAIL' for x in r) else 'NOTRUN')
        by_slot.append({'slot_id': a['slot_id'], 'admission': a['status'], 'generation_status': category,
                        'case_counts': {s: sum(x['status'] == s for x in r) for s in ('PASS', 'FAIL', 'NOTRUN')}})
    return {'generation_counts': {s: sum(r['generation_status'] == s for r in by_slot)
                                  for s in ('PASS', 'FAIL', 'ERROR', 'NOTRUN')},
            'primary_case_counts': {s: sum(r['status'] == s for r in rows) for s in ('PASS', 'FAIL', 'NOTRUN')},
            'slots': by_slot}


def run(scope, output):
    require(type(scope) is ApprovedPilot24Scope, 'P24_APPROVED_SCOPE_REQUIRED')
    scope.require()
    output = validate_output(output)
    claim = ExecutionClaim(scope, output)
    try:
        summary = run_claimed(scope, output, claim)
        claim.event(summary['state'], halt=summary['halt'], scoring=summary['scoring'])
        return summary
    except BaseException:
        try:
            claim.event('INCOMPLETE_DO_NOT_RETRY')
        except OSError:
            pass
        raise


def run_claimed(scope, output, claim):
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / 'scope.json', scope.manifest)
    rows = planned_rows(scope.manifest, scope.cases)
    write_json(output / 'notrun-initial.json', rows)
    admissions = [{'slot_id': s['slot_id'], 'status': s['static_rejection'] or 'NOTRUN',
                   'precheck': 'NOTRUN', 'admission_phase': 'NOT_STARTED'} for s in scope.manifest['slots']]
    halt = None
    backends = []
    for slot, a in zip(scope.manifest['slots'], admissions):
        sid, rep = slot['slot_id'], slot['representation']
        selected = [r for r in rows if r['slot_id'] == sid]
        backend = proof = parsed = None
        if a['status'].startswith('FAIL_') or halt:
            continue
        try:
            scope.require()
            a['precheck'] = 'STARTED'
            claim.event('PRECHECK_STARTED', slot_id=sid)
            backend = NodeBackend() if rep == 'js_callback' else Pilot24IRBackend(scope, sid)
            backends.append(backend)
            proof = fresh(scope, backend, rep, output / ('precheck-' + sid))
            a['precheck'] = 'PASS'
            # Reservation precedes parser/admission launch too; unknown parsing is not re-run.
            write_json(output / ('admission-reservation-' + sid + '.json'),
                       {'slot_id': sid, 'source_sha256': slot['source_sha256'], 'primary_runs': 0, 'retry': False})
            a['admission_phase'] = 'RESERVED_UNCONFIRMED'
            claim.event('ADMISSION_RESERVED', slot_id=sid)
            if rep == 'js_callback':
                parsed = parse_module(scope.source(sid), CandidateParserBackend())
                status = parse_status(scope, sid, parsed)
                a['status'] = 'PARSED' if status == 'PARSED' else (
                    'FAIL_SYNTAX' if status == 'SYNTAX_REJECTED' else 'FAIL_OUTPUT_CONTRACT')
                evidence = parsed
            else:
                evidence = ir_call(scope, sid, backend, proof)
                a['status'] = ('PARSED' if evidence['result']['admission'] == 'PARSED'
                               else evidence['result']['admission_code'])
            write_json(output / ('admission-result-' + sid + '.json'), evidence)
            a['admission_phase'] = 'COMPLETED'
            claim.event('ADMISSION_COMPLETED', slot_id=sid, status=a['status'])
            if a['status'] != 'PARSED':
                for row in selected:
                    row.update(reason=a['status'], phase='ADMISSION_REJECTED')
        except (EvaluationError, Fault, OSError, ValueError, KeyError, TypeError, KeyboardInterrupt):
            a['status'] = 'BLOCKED'
            if a['precheck'] == 'STARTED':
                a['precheck'] = 'INCOMPLETE'
            halt = 'ADMISSION_OR_PRECHECK_INCOMPLETE'
        for row, case in zip(selected, scope.cases[slot['task_id']]):
            if halt or a['status'] != 'PARSED':
                continue
            reserved = False
            try:
                scope.case(sid, case)
                if time.monotonic() - proof.created > 550:
                    proof = fresh(scope, backend, rep, output / ('refresh-' + sid + '-' + case['id']))
                proof.require(backend)
                write_json(output / ('reservation-' + sid + '-' + case['id'] + '.json'),
                           {**row, 'maximum_primary_runs': 1, 'unknown_outcome': 'DO_NOT_RETRY'})
                reserved = True
                row['phase'] = 'RESERVED_EXECUTION_UNCONFIRMED'
                claim.event('PRIMARY_RESERVED', slot_id=sid, case_id=case['id'])
                result = (evaluate_js(scope, sid, backend, proof, parsed, case) if rep == 'js_callback'
                          else evaluate_ir(scope, sid, backend, proof, case))
                # Durable result precedes COMPLETED accounting; a lost write leaves unknown bounds.
                write_json(output / ('result-' + sid + '-' + case['id'] + '.json'), result)
                row.update(result, reason=None, phase='COMPLETED')
                claim.event('PRIMARY_COMPLETED', slot_id=sid, case_id=case['id'], status=row['status'])
            except (EvaluationError, Fault, OSError, ValueError, KeyError, TypeError, KeyboardInterrupt):
                halt = 'PRIMARY_OR_PERSISTENCE_INCOMPLETE'
                if reserved and row['phase'] != 'COMPLETED':
                    row['primary_case_executions'] = 'UNKNOWN_0_OR_1'
                    row['candidate_executions'] = 'UNKNOWN_AFTER_RESERVATION'
                    row['IR_case_replays'] = 'UNKNOWN_0_OR_1' if rep == 'gameir_text' else 0
                    row['JS_transcript_replays'] = 'UNKNOWN_0_OR_1' if rep == 'js_callback' else 0
                row['reason'] = halt
                a['status'] = 'BLOCKED'
        write_json(output / ('slot-' + sid + '.json'), {'admission': a, 'cases': selected})
    for row in rows:
        if row['phase'] == 'NOT_STARTED' and halt:
            row['reason'] = halt
    remaining = []
    if backends:
        try:
            remaining = json.loads(backends[-1]._ctl('ps', '--all', '--filter',
                                  'label=gir.reviewed-worker=phase3', '--format=json'))
            if remaining:
                halt = halt or 'PROFILE_CONTAINERS_REMAIN'
        except (EvaluationError, OSError, ValueError):
            halt = halt or 'INDEPENDENT_CLEANUP_UNVERIFIED'
    scope.require()
    summary = {'schema': 'gir-pilot24-exact-scoring@1', 'state': 'STOPPED_ON_BLOCK' if halt else 'COMPLETE',
               'halt': halt, 'scope_digest': digest(scope.manifest), 'batch_key': scope.manifest['batch_key'],
               'claim_consumed': True, 'claim_directory': str(claim.path), 'retry_or_resume_allowed': False,
               'scoring': scoring(rows, admissions), 'admissions': admissions,
               'completed_primary_runs': sum(r['primary_case_executions'] for r in rows
                                             if type(r['primary_case_executions']) is int),
               'unknown_primary_records': sum(type(r['primary_case_executions']) is not int for r in rows),
               'primary_runs_lower': sum(r['primary_case_executions'] for r in rows
                                         if type(r['primary_case_executions']) is int),
               'primary_runs_upper': sum(r['primary_case_executions'] if type(r['primary_case_executions']) is int
                                         else 1 for r in rows),
               'known_callback_or_IR_dispatches': sum(r['candidate_executions'] for r in rows
                                                      if type(r['candidate_executions']) is int),
               'IR_case_replays': (None if any(type(r['IR_case_replays']) is not int for r in rows)
                                   else sum(r['IR_case_replays'] for r in rows)),
               'JS_transcript_replays': (None if any(type(r['JS_transcript_replays']) is not int for r in rows)
                                         else sum(r['JS_transcript_replays'] for r in rows)),
               'known_IR_case_replays': sum(r['IR_case_replays'] for r in rows
                                            if type(r['IR_case_replays']) is int),
               'known_JS_transcript_replays': sum(r['JS_transcript_replays'] for r in rows
                                                  if type(r['JS_transcript_replays']) is int),
               'unknown_replay_records': sum(type(r['IR_case_replays']) is not int or
                                             type(r['JS_transcript_replays']) is not int for r in rows),
               'remaining_profile_containers': remaining, 'model_calls': 0, 'repairs': 0,
               'contract_status': 'FIXED_SCENARIOS_ONLY_FULL_CONTRACT_NOT_CHECKED',
               'sample_units': '6_TASK_CLUSTERS_24_GENERATION_SLOTS_NOT_288_INDEPENDENT_SAMPLES'}
    write_json(output / 'cases.json', rows)
    write_json(output / 'summary.json', summary)
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--execute-approved-pilot24', action='store_true')
    args = p.parse_args(argv)
    scope = None
    try:
        manifest = load_manifest()
        if args.execute_approved_pilot24:
            require(APPROVAL.is_file(), 'P24_EXPLICIT_EXECUTION_APPROVAL_REQUIRED')
            scope = ApprovedPilot24Scope(manifest, read(APPROVAL.relative_to(ROOT).as_posix()))
            summary = run(scope, args.output)
            print(json.dumps(summary))
            return int(summary['halt'] is not None)
        output = validate_output(args.output)
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / 'scope.json', manifest)
        rows = planned_rows(manifest, case_sets(static_manifest()))
        write_json(output / 'notrun-initial.json', rows)
        print(json.dumps({'state': 'PREPARED_NO_EXECUTION', 'scope_digest': digest(manifest),
                          'planned_rows': 288, 'candidate_executions': 0, 'approval_written': False}))
        return 0
    except (EvaluationError, Fault, OSError, ValueError, KeyError, TypeError, KeyboardInterrupt):
        # A propagated error after claim/result writes cannot assert zero execution.
        claimed = scope is not None and (CLAIM_ROOT / scope.manifest['batch_key']).exists()
        print(json.dumps({'state': 'EXECUTION_INCOMPLETE_DO_NOT_RETRY' if claimed else 'BLOCKED_BEFORE_EXECUTION',
                          'reason': 'P24_EXECUTION_OR_PERSISTENCE_INCOMPLETE' if claimed else
                                    'P24_APPROVAL_SCOPE_OR_OUTPUT_REJECTED',
                          'candidate_executions': None if claimed else 0,
                          'execution_count_status': 'UNKNOWN_READ_RETAINED_EVIDENCE' if claimed else 'NOT_STARTED',
                          'claim_consumed': claimed,
                          'claim_directory': str(CLAIM_ROOT / scope.manifest['batch_key']) if claimed else None,
                          'model_calls': 0}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
