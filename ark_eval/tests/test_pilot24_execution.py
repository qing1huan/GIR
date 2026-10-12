"""Mock orchestration and authored worker tests; never execute collected sources."""
import copy
import hashlib
import io
import json
import subprocess
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock, patch

from headless_v01.compiler import digest
from headless_v01.types import Fault
from ark_eval.adapter import EvaluationError
from ark_eval import pilot24_execution_scope as s, pilot24_execution_eval as e
from ark_eval import pilot24_execution_backend as b
from ark_eval.pilot24_execution_backend import (Pilot24IRBackend, ir_config, pilot_payload,
    Pilot24JSRunner, ir_call)
from ark_eval.pilot24_execution_worker import evaluate as worker_evaluate
from ark_eval.node_backend import NodeBackend


def memory_mock_approval(m):
    return {**s.approval_fields(m), 'human_statement': 'PUBLIC IN-MEMORY MOCK ONLY; NOT HUMAN PERMISSION'}


class Pilot24ExecutionTests(unittest.TestCase):
    def scope(self):
        m = s.load_manifest()
        return s.ApprovedPilot24Scope(m, memory_mock_approval(m))

    def test_exact_mapping_counts_and_raw_baseline_still_bound(self):
        m = s.load_manifest()
        rows = e.planned_rows(m, s.case_sets(s.static_manifest()))
        self.assertEqual(len(rows), 288)
        self.assertEqual(len(m['slots']), 24)
        self.assertEqual(len({x['source_sha256'] for x in m['slots']}), 14)
        self.assertEqual(sum(r['phase'] == 'STATIC_REJECTED' for r in rows), 12)
        self.assertEqual(sum(r['phase'] == 'NOT_STARTED' for r in rows), 276)
        self.assertEqual({r['status'] for r in rows}, {'NOTRUN'})

    def test_old_pending_inexact_and_boolean_substitution_rejected(self):
        m = s.load_manifest()
        for approval in ({}, {'schema': 'gir-v4-explicit-human-approval@1', 'human_statement': 'old'},
                         {'decision': 'PENDING', 'human_statement': 'no'}):
            with self.assertRaises(EvaluationError):
                s.ApprovedPilot24Scope(m, approval)
        for k, v in [('human_explicit_approval', 1), ('scope_digest', '0' * 64),
                     ('maximum_primary_runs', 288), ('static_rejected_slots', [])]:
            a = memory_mock_approval(m)
            a[k] = v
            with self.assertRaises(EvaluationError):
                s.ApprovedPilot24Scope(m, a)

    def test_case_abi_order_bundle_code_and_live_object_drift_rejected(self):
        scope = self.scope()
        case = copy.deepcopy(scope.cases['M1'][0])
        case['seed'] = True
        with self.assertRaisesRegex(EvaluationError, 'UNKNOWN_CASE'):
            scope.case('M1-r1-gameir_text', case)
        scope.cases['M1'][0]['expected_ticks'] = []
        with self.assertRaises(EvaluationError):
            scope.require()
        scope = self.scope()
        scope.slots['M1-r1-gameir_text']['source_sha256'] = '0' * 64
        with self.assertRaises(EvaluationError):
            scope.slot('M1-r1-gameir_text')
        with patch.object(s, 'sha', return_value='0' * 64):
            with self.assertRaisesRegex(EvaluationError, 'STATIC_MANIFEST_DRIFT'):
                s.static_manifest()
        with patch.object(s, 'make_manifest', return_value={}):
            with self.assertRaisesRegex(EvaluationError, 'FREEZE_DRIFT'):
                s.load_manifest()

    def test_static_rejected_raw_source_cannot_enter_backend(self):
        scope = self.scope()
        with patch('ark_eval.pilot24_execution_backend.NodeBackend.__init__') as backend:
            with self.assertRaisesRegex(EvaluationError, 'STATICALLY_REJECTED'):
                Pilot24IRBackend(scope, 'R1-r1-gameir_text')
            backend.assert_not_called()

    def test_default_does_not_read_approval_claim_or_create_workers(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(e, 'NodeBackend') as node, \
                patch.object(e, 'Pilot24IRBackend') as ir, patch.object(e, 'ExecutionClaim') as claim, \
                patch.object(e, 'run') as run, patch('sys.stdout', new=io.StringIO()):
            out = Path(folder) / 'preview'
            self.assertEqual(e.main(['--output', str(out)]), 0)
            self.assertEqual(len(json.loads((out / 'notrun-initial.json').read_text())), 288)
            self.assertEqual(set(p.name for p in out.iterdir()), {'scope.json', 'notrun-initial.json'})
            node.assert_not_called()
            ir.assert_not_called()
            claim.assert_not_called()
            run.assert_not_called()

    def test_explicit_switch_without_receipt_cannot_start(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(e, 'APPROVAL', Path(folder) / 'absent'), \
                patch.object(e, 'NodeBackend') as node, patch.object(e, 'run') as run, \
                patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(e.main(['--output', str(Path(folder) / 'out'), '--execute-approved-pilot24']), 2)
            node.assert_not_called()
            run.assert_not_called()

    def test_fresh_proof_requires_actual_backend_current_profile_and_faults(self):
        scope = self.scope()
        for rep, cls, names in [('js_callback', NodeBackend, s.JS_FAULTS),
                                ('gameir_text', Pilot24IRBackend, {'spin', 'oom', 'tree'})]:
            b = object.__new__(cls)
            b.disabled, b.worker_image, b.worker_hash = False, 'public-image', 'public-hash'
            profile = {'checks': {n: 'PASS' for n in s.PROFILE}}
            faults = [{'fixture': n, 'status': 'PASS'} for n in names]
            proof = s.FreshPilotIsolation(scope, b, profile, faults, rep)
            proof.created -= 601
            with self.assertRaisesRegex(EvaluationError, 'STALE'):
                proof.require(b)
            faults[0]['status'] = 'FAIL'
            with self.assertRaisesRegex(EvaluationError, 'ISOLATION_REJECTED'):
                s.FreshPilotIsolation(scope, b, profile, faults, rep)
        with self.assertRaises(EvaluationError):
            Pilot24JSRunner(scope, 'M1-r1-js_callback', object(), object(), {})

    def test_parser_rejection_is_candidate_failure_only_with_bound_clean_parse(self):
        scope = self.scope()
        slot = 'M1-r1-js_callback'
        from ark_eval.container_profile import image_lock
        fp = hashlib.sha256(s.read_bytes('ark_eval/candidate_parser.mjs') +
             s.read_bytes('ark_eval/node_supervisor.sh') + image_lock()['repository_digest'].encode()).hexdigest()
        evidence = {'candidate_executions': 0, 'result': {'status': 'SYNTAX_REJECTED', 'exports': [],
            'static_imports': [], 'source_sha256': scope.slot(slot)['source_sha256'],
            'top_level_evaluated': False, 'callback_invoked': False}, 'worker_audit': {
            'worker_source_sha256': fp, 'cleanup_errors': [], 'surviving_owned_processes': 0,
            'network_mode': 'none', 'readonly_rootfs': True, 'user_mounts': [], 'generated_source_executions': 0,
            'rootless_host': {'rootless': True},
            'kernel_security': {'CapEff': '0000000000000000', 'NoNewPrivs': '1', 'Seccomp': '2'},
            'kernel_limits': {'cpu.max': '25000 100000', 'memory.max': '67108864',
                              'memory.swap.max': '0', 'pids.max': '32'}}}
        self.assertEqual(s.parse_status(scope, slot, evidence), 'SYNTAX_REJECTED')
        for key, value in [('source_sha256', '0' * 64), ('callback_invoked', True)]:
            bad = copy.deepcopy(evidence)
            bad['result'][key] = value
            with self.assertRaises(EvaluationError):
                s.parse_status(scope, slot, bad)
        evidence['worker_audit']['cleanup_errors'] = ['mock failure']
        with self.assertRaises(EvaluationError):
            s.parse_status(scope, slot, evidence)

    def test_worker_uses_only_authored_control_exact_case_abi_and_shared_oracle(self):
        for task in ('M1', 'M2', 'C1', 'C2', 'R1', 'R2'):
            scope = s.TrustedPilotControl(task, 'gameir_text')
            sid = scope._slot['slot_id']
            config = ir_config(scope, sid)
            source = scope.source(sid)  # Exact repository-authored catalog only.
            admission = worker_evaluate({'v': 'gir-pair003-ir@1', 'purpose': 'ir_case',
                                         'source': source, 'case': None}, config)
            self.assertEqual(admission['candidate_case_executions'], 0)
            for case in scope.cases[task]:
                boot = {'v': 'gir-pair003-ir@1', 'purpose': 'ir_case', 'source': source,
                        'case': {k: case[k] for k in ('id', 'initial', 'seed', 'steps')}}
                result = worker_evaluate(boot, config)
                self.assertEqual(result['observed'], case['expected_ticks'])
                self.assertTrue(result['replay_match'])
            boot['case']['seed'] = True
            with self.assertRaises(EvaluationError):
                worker_evaluate(boot, config)
            with self.assertRaises(EvaluationError):
                worker_evaluate({**boot, 'source': source + '\n'}, config)

    def test_worker_compile_abi_rejection_and_payload_never_bake_candidate_text(self):
        scope = s.TrustedPilotControl('M1', 'gameir_text')
        sid = scope._slot['slot_id']
        config = ir_config(scope, sid)
        for source, status in [('{', 'FAIL_SYNTAX'), ('{}', 'FAIL_ABI')]:
            custom = {**config, 'source_sha256': hashlib.sha256(source.encode()).hexdigest()}
            r = worker_evaluate({'v': 'gir-pair003-ir@1', 'purpose': 'ir_case', 'source': source, 'case': None}, custom)
            self.assertEqual(r['admission_code'], status)
            self.assertEqual(r['candidate_case_executions'], 0)
        with patch.object(b, 'image_payload', return_value={'gir-ir-worker.py': b'old'}), \
                patch.object(b, 'require_public_package'):
            payload = pilot_payload(scope, sid, {})
        self.assertNotIn(scope.source(sid).encode(), payload.values())
        self.assertNotIn('expected_ticks', payload['gir-pilot24-config.json'].decode())

    def test_public_runtime_package_rejects_content_addition_and_omission(self):
        files = {'public-python': b'public runtime', 'worker': b'public worker'}
        expected = {n: hashlib.sha256(v).hexdigest() for n, v in files.items()}
        with patch.object(b, 'read', return_value={'files_sha256': expected}):
            b.require_public_package(files)
            for changed in ({**files, 'worker': b'drift'}, {**files, 'extra': b'extra'},
                            {'worker': files['worker']}):
                with self.assertRaisesRegex(EvaluationError, 'PUBLIC_RUNTIME_PACKAGE_DRIFT'):
                    b.require_public_package(changed)

    def test_callback_admission_failure_is_infrastructure_block_without_worker(self):
        scope = s.TrustedPilotControl('M1', 'js_callback')
        sid = scope._slot['slot_id']
        backend = object.__new__(NodeBackend)
        backend.disabled, backend.worker_image, backend.worker_hash = False, 'mock', 'mock'
        backend.invocation = Mock()
        proof = s.FreshPilotIsolation(scope, backend, {'checks': {n: 'PASS' for n in s.PROFILE}},
                                     [{'fixture': n, 'status': 'PASS'} for n in s.JS_FAULTS], 'js_callback')
        with patch.object(s.FreshPilotIsolation, 'require',
                          side_effect=[None, EvaluationError('P24_FRESH_ISOLATION_STALE')]), \
                patch.object(b, 'parse_status', return_value='PARSED'):
            with self.assertRaisesRegex(EvaluationError, 'JS_INFRA_OR_CLEANUP_FAILED'):
                e.evaluate_js(scope, sid, backend, proof, {}, scope.cases['M1'][0])
        backend.invocation.assert_not_called()

    def test_callback_prebootstrap_faults_are_infrastructure_blocks(self):
        for code in ('JS_TIMEOUT', 'JS_EXITED', 'JS_DISCONNECTED'):
            with self.subTest(code=code):
                scope = s.TrustedPilotControl('M1', 'js_callback')
                sid = scope._slot['slot_id']
                backend = object.__new__(NodeBackend)
                backend.disabled, backend.worker_image, backend.worker_hash = False, 'mock', 'mock'
                worker = SimpleNamespace(audit={'cleanup_errors': [], 'surviving_owned_processes': 0},
                    open=Mock(side_effect=Fault(code, phase='callback')), write=Mock(), close=Mock())
                backend.invocation = Mock(return_value=worker)
                proof = s.FreshPilotIsolation(scope, backend, {'checks': {n: 'PASS' for n in s.PROFILE}},
                    [{'fixture': n, 'status': 'PASS'} for n in s.JS_FAULTS], 'js_callback')
                with patch.object(b, 'parse_status', return_value='PARSED'):
                    with self.assertRaisesRegex(EvaluationError, 'JS_INFRA_OR_CLEANUP_FAILED'):
                        e.evaluate_js(scope, sid, backend, proof, {}, scope.cases['M1'][0])
                worker.write.assert_not_called()
                worker.close.assert_called_once()

    def test_cli_failure_after_atomic_claim_reports_unknown_and_preserves_claim(self):
        scope = self.scope()
        approval_path = SimpleNamespace(is_file=lambda: True,
                                        relative_to=lambda root: Path('public-mock-receipt'))
        with tempfile.TemporaryDirectory() as root, patch.object(e, 'CLAIM_ROOT', Path(root) / 'claims'), \
                patch.object(e, 'APPROVAL', approval_path), \
                patch.object(e, 'read', return_value=memory_mock_approval(scope.manifest)):
            def late_failure(sc, output):
                claim = e.ExecutionClaim(sc, output)
                claim.event('PUBLIC_MOCK_DISPATCH_COMPLETED', dispatches=1)
                raise OSError('PUBLIC MOCK FINAL PERSISTENCE FAILURE')
            with patch.object(e, 'run', side_effect=late_failure), patch('sys.stdout', new=io.StringIO()) as stdout:
                self.assertEqual(e.main(['--output', str(Path(root) / 'out'), '--execute-approved-pilot24']), 2)
                report = json.loads(stdout.getvalue())
            self.assertIsNone(report['candidate_executions'])
            self.assertEqual(report['execution_count_status'], 'UNKNOWN_READ_RETAINED_EVIDENCE')
            self.assertTrue(report['claim_consumed'])
            self.assertEqual(report['state'], 'EXECUTION_INCOMPLETE_DO_NOT_RETRY')
            self.assertTrue((Path(report['claim_directory']) / 'event-0001.json').is_file())
            with self.assertRaisesRegex(EvaluationError, 'BATCH_ALREADY_CLAIMED'):
                e.ExecutionClaim(scope, Path(root) / 'other')

    def test_callback_close_oserror_and_timeout_are_blocks_with_retained_audit(self):
        for failure in (OSError('PUBLIC MOCK CLOSE ERROR'), subprocess.TimeoutExpired('public-mock', 1)):
            with self.subTest(failure=type(failure).__name__):
                scope = s.TrustedPilotControl('M1', 'js_callback')
                sid = scope._slot['slot_id']
                backend = object.__new__(NodeBackend)
                backend.disabled, backend.worker_image, backend.worker_hash = False, 'mock', 'mock'
                worker = SimpleNamespace(audit={'cleanup_errors': [], 'surviving_owned_processes': 0},
                    open=Mock(), write=Mock(), read_line=Mock(side_effect=Fault('JS_TIMEOUT', phase='callback')),
                    close=Mock(side_effect=failure))
                backend.invocation = Mock(return_value=worker)
                proof = s.FreshPilotIsolation(scope, backend, {'checks': {n: 'PASS' for n in s.PROFILE}},
                    [{'fixture': n, 'status': 'PASS'} for n in s.JS_FAULTS], 'js_callback')
                with patch.object(b, 'parse_status', return_value='PARSED'):
                    runner = Pilot24JSRunner(scope, sid, backend, proof, {})
                    with patch.object(e, 'Pilot24JSRunner', return_value=runner):
                        with self.assertRaisesRegex(EvaluationError, 'JS_INFRA_OR_CLEANUP_FAILED'):
                            e.evaluate_js(scope, sid, backend, proof, {}, scope.cases['M1'][0])
                self.assertEqual(runner.infrastructure_failure, 'P24_CALLBACK_CLEANUP_FAILED')
                self.assertEqual(len(runner.audits), 1)
                self.assertEqual(runner.audits[0]['terminal'], 'ISOLATION_CLEANUP_FAILED')
                self.assertEqual(runner.audits[0]['reviewed_source_executions'], 1)
                worker.close.assert_called_once()

    def mock_run(self, root, scope, fail_at=None, persist_failure=None):
        calls = []
        fake = SimpleNamespace(_ctl=lambda *a: '[]', worker_image='mock', worker_hash='mock')
        proof = SimpleNamespace(created=time.monotonic(), require=lambda b: None)
        original_write = e.write_json
        def result(sc, sid, *args):
            if len(calls) == fail_at:
                raise EvaluationError('PUBLIC MOCK FAILURE')
            case = args[-1]
            calls.append((sid, case['id']))
            rep = sc.slot(sid)['representation']
            return {'status': 'PASS', 'primary_case_executions': 1, 'candidate_executions': 1,
                    'IR_case_replays': int(rep == 'gameir_text'),
                    'JS_transcript_replays': int(rep == 'js_callback')}
        def write(path, value):
            if persist_failure and persist_failure(path):
                raise OSError('PUBLIC MOCK WRITE FAILURE')
            return original_write(path, value)
        with patch.object(e, 'CLAIM_ROOT', Path(root) / 'claims'), \
                patch.object(e, 'NodeBackend', return_value=fake), patch.object(e, 'Pilot24IRBackend', return_value=fake), \
                patch.object(e, 'fresh', return_value=proof), patch.object(e, 'CandidateParserBackend'), \
                patch.object(e, 'parse_module', return_value={}), patch.object(e, 'parse_status', return_value='PARSED'), \
                patch.object(e, 'ir_call', return_value={'result': {'admission': 'PARSED'}}), \
                patch.object(e, 'evaluate_ir', side_effect=result), patch.object(e, 'evaluate_js', side_effect=result), \
                patch.object(e, 'write_json', side_effect=write), \
                patch.object(s.ApprovedPilot24Scope, 'require'):
            summary = e.run(scope, Path(root) / 'out')
        return summary, calls

    def test_mock_full_276_runs_keeps_24_generation_slots_and_12_static_notrun(self):
        scope = self.scope()
        with tempfile.TemporaryDirectory() as root:
            summary, calls = self.mock_run(root, scope)
            self.assertEqual(len(calls), 276)
            self.assertEqual(summary['scoring']['primary_case_counts'], {'PASS': 276, 'FAIL': 0, 'NOTRUN': 12})
            self.assertEqual(summary['scoring']['generation_counts'], {'PASS': 23, 'FAIL': 1, 'ERROR': 0, 'NOTRUN': 0})
            self.assertEqual(summary['IR_case_replays'], 132)
            self.assertEqual(summary['JS_transcript_replays'], 144)
            self.assertEqual(len(summary['scoring']['slots']), 24)
            self.assertEqual(summary['state'], 'COMPLETE')

    def test_mock_midrun_failure_keeps_partial_results_unknown_and_failstop(self):
        with tempfile.TemporaryDirectory() as root:
            summary, calls = self.mock_run(root, self.scope(), fail_at=2)
            self.assertEqual(len(calls), 2)
            self.assertEqual(summary['unknown_primary_records'], 1)
            self.assertEqual(summary['primary_runs_lower'], 2)
            self.assertEqual(summary['primary_runs_upper'], 3)
            self.assertIsNone(summary['IR_case_replays'])
            self.assertEqual(summary['known_IR_case_replays'], 2)
            self.assertEqual(summary['unknown_replay_records'], 1)
            self.assertEqual(summary['state'], 'STOPPED_ON_BLOCK')
            self.assertEqual(summary['scoring']['primary_case_counts'], {'PASS': 2, 'FAIL': 0, 'NOTRUN': 286})
            with patch.object(e, 'CLAIM_ROOT', Path(root) / 'claims'), patch.object(e, 'NodeBackend') as node:
                with self.assertRaisesRegex(EvaluationError, 'BATCH_ALREADY_CLAIMED'):
                    e.run(self.scope(), Path(root) / 'other')
                node.assert_not_called()

    def test_reservation_write_failure_never_calls_candidate_and_result_loss_is_unknown(self):
        for trigger, expected_calls, unknown in [('reservation-M1', 0, 0), ('result-M1', 1, 1)]:
            with tempfile.TemporaryDirectory() as root:
                summary, calls = self.mock_run(root, self.scope(),
                    persist_failure=lambda p: p.name.startswith(trigger))
                self.assertEqual(len(calls), expected_calls)
                self.assertEqual(summary['unknown_primary_records'], unknown)
                self.assertEqual(summary['state'], 'STOPPED_ON_BLOCK')

    def test_concurrent_atomic_batch_claim_one_winner_across_outputs(self):
        scope = self.scope()
        barrier = Barrier(2)
        with tempfile.TemporaryDirectory() as root, patch.object(e, 'CLAIM_ROOT', Path(root) / 'claims'):
            def take(n):
                barrier.wait()
                try:
                    return e.ExecutionClaim(scope, Path(root) / str(n)).path
                except EvaluationError as exc:
                    return str(exc)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(take, (1, 2)))
            self.assertEqual(sum(isinstance(x, Path) for x in results), 1)
            self.assertIn('P24_BATCH_ALREADY_CLAIMED', results)

    def test_claim_write_failure_still_consumes_batch_and_existing_output_is_preserved(self):
        scope = self.scope()
        with tempfile.TemporaryDirectory() as root, patch.object(e, 'CLAIM_ROOT', Path(root) / 'claims'):
            out = Path(root) / 'existing'
            out.mkdir()
            marker = out / 'keep'
            marker.write_text('preserve')
            with self.assertRaises(FileExistsError):
                e.run(scope, out)
            self.assertFalse(e.CLAIM_ROOT.exists())
            with patch.object(e, 'write_json', side_effect=OSError('PUBLIC MOCK FAILURE')):
                with self.assertRaises(OSError):
                    e.ExecutionClaim(scope, Path(root) / 'one')
            with self.assertRaisesRegex(EvaluationError, 'BATCH_ALREADY_CLAIMED'):
                e.ExecutionClaim(scope, Path(root) / 'two')
            self.assertEqual(marker.read_text(), 'preserve')

    def test_batch_key_remains_same_if_only_adapter_scope_changes(self):
        a = s.make_manifest()
        with patch.object(s, 'FILES', ()):
            b = s.make_manifest()
        self.assertNotEqual(digest(a), digest(b))
        self.assertEqual(a['batch_key'], b['batch_key'])


if __name__ == '__main__':
    unittest.main()
