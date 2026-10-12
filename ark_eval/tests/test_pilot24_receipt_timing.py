"""Trusted catalog and virtual-clock regressions; no collected source execution."""
import time
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ark_eval.adapter import EvaluationError, digest
from ark_eval import pilot24_execution_scope as s, pilot24_execution_backend as b
from ark_eval import pilot24_recovery as r
from ark_eval.node_backend import NodeBackend
from headless_v01.types import Fault


class ReceiptTimingTests(unittest.TestCase):
    def setup_receipt(self, rep='js_callback'):
        scope = s.TrustedPilotControl('C1', rep)
        backend = object.__new__(NodeBackend if rep == 'js_callback' else b.Pilot24IRBackend)
        backend.disabled = False
        backend.worker_image = backend.worker_hash = 'public-mock'
        backend.scope, backend.slot_id = scope, scope._slot['slot_id']
        backend.invocation = Mock()
        names = s.JS_FAULTS if rep == 'js_callback' else {'spin', 'oom', 'tree'}
        def fresh():
            return s.FreshPilotIsolation(scope, backend,
                {'checks': {n: 'PASS' for n in s.PROFILE}},
                [{'fixture': n, 'status': 'PASS'} for n in names], rep)
        return scope, backend, fresh(), fresh

    def test_metadata_age_refresh_retains_full_scope_check(self):
        clock = [0.0]
        with patch.object(time, 'monotonic', side_effect=lambda: clock[0]):
            scope, backend, proof, fresh = self.setup_receipt()
            clock[0] = 580
            proof.require(backend)
            original = scope.require
            def slow():
                original(); clock[0] += 31
            refresh = Mock(side_effect=fresh)
            with patch.object(scope, 'require', side_effect=slow) as checked:
                new = s.current_isolation(proof, backend, refresh)
            self.assertIsNot(new, proof)
            self.assertGreaterEqual(checked.call_count, 3)
            refresh.assert_called_once()
            backend.invocation.assert_not_called()

    def test_age_without_refresh_still_rejects(self):
        scope, backend, proof, _ = self.setup_receipt()
        proof.created -= 601
        with self.assertRaisesRegex(EvaluationError, 'P24_FRESH_ISOLATION_STALE'):
            s.current_isolation(proof, backend)
        backend.invocation.assert_not_called()

    def test_changed_backend_is_never_refreshed(self):
        for name, value in [('disabled', True), ('worker_image', 'changed'), ('worker_hash', 'changed')]:
            with self.subTest(name=name):
                _, backend, proof, fresh = self.setup_receipt()
                proof.created -= 601
                setattr(backend, name, value)
                refresh = Mock(side_effect=fresh)
                with self.assertRaisesRegex(EvaluationError, 'BINDING_CHANGED'):
                    s.current_isolation(proof, backend, refresh)
                refresh.assert_not_called()

    def test_scope_drift_is_never_refreshed(self):
        scope, backend, proof, fresh = self.setup_receipt()
        refresh = Mock(side_effect=fresh)
        with patch.object(scope, 'require', side_effect=EvaluationError('P24_LIVE_SCOPE_DRIFT')):
            with self.assertRaisesRegex(EvaluationError, 'LIVE_SCOPE_DRIFT'):
                s.current_isolation(proof, backend, refresh)
        refresh.assert_not_called()

    def test_refresh_cannot_change_scope_or_representation(self):
        _, backend, proof, fresh = self.setup_receipt()
        proof.created -= 601
        changed = fresh()
        changed.scope = s.TrustedPilotControl('M1', 'js_callback')
        with self.assertRaisesRegex(EvaluationError, 'REFRESH_REJECTED'):
            s.current_isolation(proof, backend, lambda: changed)

    def test_replacement_age_is_checked_and_not_refreshed_twice(self):
        _, backend, proof, fresh = self.setup_receipt()
        proof.created -= 601
        stale = fresh(); stale.created -= 601
        refresh = Mock(return_value=stale)
        with self.assertRaisesRegex(EvaluationError, 'ISOLATION_STALE'):
            s.current_isolation(proof, backend, refresh)
        refresh.assert_called_once()

    def test_js_metadata_is_outside_worker_deadline(self):
        clock = [0.0]
        with patch.object(time, 'monotonic', side_effect=lambda: clock[0]):
            scope, backend, proof, fresh = self.setup_receipt()
            sid = scope._slot['slot_id']
            original = scope.slot
            def slow_slot(slot):
                value = original(slot); clock[0] += 31; return value
            worker = SimpleNamespace(audit={'cleanup_errors': [], 'surviving_owned_processes': 0},
                close=Mock(), read_line=Mock(side_effect=Fault('JS_TIMEOUT', phase='callback')))
            deadline = [None]
            def within_deadline(*args, **kwargs):
                self.assertLess(clock[0], deadline[0])
            worker.open = Mock(side_effect=within_deadline)
            worker.write = Mock(side_effect=within_deadline)
            def invocation(seconds):
                deadline[0] = clock[0] + seconds; return worker
            backend.invocation = Mock(side_effect=invocation)
            with patch.object(b, 'parse_status', return_value='PARSED'), \
                 patch.object(scope, 'slot', side_effect=slow_slot), patch.object(b, 'MockClient'):
                runner = b.Pilot24JSRunner(scope, sid, backend, proof, {}, refresh=fresh)
                runner.run(SimpleNamespace(invocation='public-mock', failed=None), {})
            worker.open.assert_called_once(); worker.write.assert_called_once()
            worker.close.assert_called_once()
            self.assertIsNone(runner.infrastructure_failure)
            self.assertEqual(worker.audit['reviewed_source_executions'], 1)
            self.assertEqual(worker.audit['generated_source_executions'], 0)
            self.assertGreater(clock[0], 31)

    def test_ir_metadata_is_outside_worker_deadline(self):
        clock = [0.0]
        with patch.object(time, 'monotonic', side_effect=lambda: clock[0]):
            scope, backend, proof, fresh = self.setup_receipt('gameir_text')
            sid = scope._slot['slot_id']
            result = {'source_sha256': scope.slot(sid)['source_sha256'],
                'abi_digest': digest(scope.abi(sid)), 'case_id': None, 'source_modified': False,
                'admission': 'REJECTED', 'admission_code': 'FAIL_COMPILE',
                'candidate_case_executions': 0, 'IR_case_replays': 0}
            original = scope.slot
            def slow_slot(slot):
                value = original(slot); clock[0] += 31; return value
            deadline = [None]
            def within_deadline(*args, **kwargs):
                self.assertLess(clock[0], deadline[0])
            worker = SimpleNamespace(audit={'cleanup_errors': [], 'surviving_owned_processes': 0},
                open=Mock(side_effect=within_deadline), write=Mock(side_effect=within_deadline),
                release=Mock(side_effect=within_deadline), close=Mock(),
                read_line=Mock(side_effect=[
                    __import__('json').dumps({'transport': 'gir-pilot24-ir-result@1', 'result': result}),
                    '{"transport":"gir-node-exit@1","code":0}']))
            def invocation(seconds):
                deadline[0] = clock[0] + seconds; return worker
            backend.invocation = Mock(side_effect=invocation)
            with patch.object(scope, 'slot', side_effect=slow_slot):
                evidence = b.ir_call(scope, sid, backend, proof, refresh=fresh)
            self.assertEqual(evidence['result'], result)
            worker.release.assert_called_once(); worker.close.assert_called_once()
            self.assertGreater(clock[0], 31)

    def test_controller_ir_admission_renews_stale_proof_before_any_case(self):
        """Virtual metadata delay, trusted source only; no real claim or worker."""
        clock = [0.0]
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(time, 'monotonic', side_effect=lambda: clock[0]):
            trusted, backend, proof, new_proof = self.setup_receipt('gameir_text')
            sid = trusted._slot['slot_id']
            result = {'source_sha256': trusted.slot(sid)['source_sha256'],
                'abi_digest': digest(trusted.abi(sid)), 'case_id': None,
                'source_modified': False, 'admission': 'REJECTED',
                'admission_code': 'FAIL_COMPILE', 'candidate_case_executions': 0,
                'IR_case_replays': 0}
            worker = SimpleNamespace(audit={'cleanup_errors': [], 'surviving_owned_processes': 0},
                open=Mock(), write=Mock(), release=Mock(), close=Mock(),
                read_line=Mock(side_effect=[json.dumps({'transport':'gir-pilot24-ir-result@1',
                    'result':result}), '{"transport":"gir-node-exit@1","code":0}']))
            backend.invocation = Mock(return_value=worker)
            backend._ctl = Mock(return_value='[]')
            row = {'slot_id':sid, 'case_id':'N01', 'source_sha256':result['source_sha256'],
                'task_id':'C1', 'representation':'gameir_text', 'status':'NOTRUN',
                'phase':'NOT_STARTED', 'reason':None, 'primary_case_executions':0,
                'candidate_executions':0, 'IR_case_replays':0, 'JS_transcript_replays':0}
            scope = object.__new__(r.s.ApprovedZeroStartRecovery)
            scope.manifest = {'allowed_rows':[row], 'recovery_key':'public-mock'}
            scope.base, scope.cases, scope.preserved = trusted, trusted.cases, [row]
            scope.require = Mock()
            def slot(_):
                clock[0] += 31
                return trusted._slot
            scope.slot = slot
            scope.case = Mock()
            claim = SimpleNamespace(event=Mock(), reserve=Mock(), path=Path(folder)/'claim')
            count = [0]
            def fresh(*args):
                count[0] += 1
                replacement = new_proof()
                if count[0] == 1: replacement.created -= 580
                return replacement
            with patch.object(r, 'ZeroStartClaim', return_value=claim), \
                 patch.object(r.old, 'Pilot24IRBackend', return_value=backend), \
                 patch.object(r.old, 'fresh', side_effect=fresh) as renew:
                summary = r.run_controller(scope, Path(folder)/'output')
            self.assertEqual(count[0], 2)
            self.assertEqual(renew.call_args.args[-1].name, 'refresh-admission-'+sid+'-0001')
            worker.write.assert_called_once()
            self.assertEqual(worker.audit['generated_source_executions'], 0)
            self.assertEqual(summary['halt'], 'ADMISSION_OR_PRECHECK_INCOMPLETE')
            self.assertEqual(summary['failure']['code'], 'P24R_PARENT_ADMISSION_CHANGED')
            claim.reserve.assert_not_called()
            scope.case.assert_not_called()

    def test_fixed_error_and_first_stage_are_retained_without_raw_text(self):
        error = EvaluationError('P24_JS_INFRA_OR_CLEANUP_FAILED')
        error.first_infrastructure_exception = {
            'code': 'P24_FRESH_ISOLATION_STALE', 'exception_type': 'EvaluationError',
            'raw_message_saved': False, 'stage': 'metadata_or_transport'}
        value = r.safe_failure(error)
        self.assertEqual(value['first_infrastructure_exception']['code'], 'P24_FRESH_ISOLATION_STALE')
        self.assertFalse(value['raw_message_saved'])
        self.assertEqual(r.safe_failure(EvaluationError('untrusted private message'))['code'],
                         'UNCLASSIFIED_INFRASTRUCTURE_ERROR')

    def test_unknown_or_injected_diagnostic_fields_are_not_saved(self):
        error = EvaluationError('P24_JS_INFRA_OR_CLEANUP_FAILED')
        error.first_infrastructure_exception = {'code': 'private', 'source': 'private'}
        self.assertNotIn('first_infrastructure_exception', r.safe_failure(error))


if __name__ == '__main__':
    unittest.main()
