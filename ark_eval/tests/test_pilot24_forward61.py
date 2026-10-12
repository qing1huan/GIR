"""Forward binding and fail-stop mocks only; no collected candidate execution."""
import copy
import io
import json
import subprocess
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ark_eval.adapter import EvaluationError,digest
from ark_eval import pilot24_recovery_scope as s, pilot24_recovery as r
from ark_eval import candidate_eval as c, pilot24_execution_eval as e
from ark_eval.js_runner import IsolatedJSRunner
from ark_eval.node_backend import NodeBackend
from headless_v01.types import Fault


class Forward61Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest=s.load_forward_manifest()
        cls.cases,cls.preserved,_=s.forward_reconciliation()
        cls.execution=s.forward_execution_manifest(cls.manifest)

    def scope(self):
        # Public memory mock receipt; never write an actual authorization.
        permit={**s.forward_fields(self.manifest),'human_statement':'PUBLIC IN-MEMORY MOCK ONLY'}
        with patch.object(s,'load_forward_manifest',return_value=self.manifest), \
             patch.object(s,'forward_reconciliation',return_value=(self.cases,self.preserved,self.manifest['allowed_rows'])):
            return s.ApprovedZeroStartRecovery(self.manifest,permit)

    def test_exact_61_preserves_214_unknown_and_static(self):
        self.assertEqual([(x['slot_id'],x['case_id']) for x in self.manifest['allowed_rows']],s.expected_ids()[21:])
        self.assertEqual(self.manifest['maximum_primary_runs'],61)
        self.assertEqual(self.manifest['preserved_durable_rows'],214)
        self.assertEqual(r.accounting(self.preserved)['counts'],{'PASS':212,'FAIL':2,'NOTRUN':73,'HARNESS_ERROR':1})
        self.assertNotEqual(self.manifest['recovery_key'],s.RECOVERY_KEY)
        self.assertFalse(s.FORWARD_PERMIT.exists())

    def test_historical_registry_missing_added_or_mutated_blocks(self):
        original=s.read
        for kind in ('missing','added','mutated'):
            def altered(n):
                v=original(n)
                if n==s.CONTINUED_REGISTRY:
                    v=copy.deepcopy(v)
                    if kind=='missing':v['production_files'].pop(next(iter(v['production_files'])))
                    elif kind=='added':v['production_files']['results/fake.json']='0'*64
                    else:v['production_files'][next(iter(v['production_files']))]='0'*64
                return v
            with self.subTest(kind=kind),patch.object(s,'read',side_effect=altered):
                with self.assertRaisesRegex(EvaluationError,'CONTINUED_ANCHOR'):s.forward_reconciliation()

    def test_undeclared_runtime_case_and_node_drift_blocks(self):
        original=s.sha
        static=s.read(s.parent.STATIC)
        path=next(n for n in static['runtime_bindings'] if n not in s.INTEGRATION_BASE and n!='ark_eval/node_backend.py')
        for n in (path,'ark_eval/node_backend.py',static['task_cases']['C1']['path']):
            with self.subTest(path=n),patch.object(s,'sha',side_effect=lambda x:'0'*64 if x==n else original(x)):
                with self.assertRaises(EvaluationError):s.make_forward_manifest()

    def test_old_permit_numeric_boolean_wrong_budget_and_range_rejected(self):
        valid={**s.forward_fields(self.manifest),'human_statement':'PUBLIC IN-MEMORY MOCK ONLY'}
        bad=[{**s.zero_start_fields(s.read(s.FREEZE)),'human_statement':'PUBLIC MOCK'}]
        bad.extend({**valid,k:v} for k,v in [('human_explicit_approval',1),('maximum_primary_runs',82),
            ('scope_digest','0'*64),('recovery_key',s.RECOVERY_KEY),('allowed_rows_digest','0'*64)])
        for permit in bad:
            with self.assertRaises(EvaluationError):s.ApprovedZeroStartRecovery(self.manifest,permit)

    def test_base_scope_cannot_execute_completed_unknown_static_or_changed_case(self):
        scope=self.scope()
        with patch.object(s.parent.ApprovedPilot24Scope,'require'):
            for sid,cid in (s.UNKNOWN,('C1-r2-js_callback','R03'),('M1-r1-js_callback','N01'),('R1-r1-gameir_text','N01')):
                case=next(c for c in self.cases[sid[:2]] if c['id']==cid)
                with self.assertRaises(EvaluationError):scope.base.case(sid,case)
            case=copy.deepcopy(self.cases['C1'][-1]);scope.base.case('C1-r2-js_callback',case)
            case['expected_ticks']=[]
            with self.assertRaises(EvaluationError):scope.base.case('C1-r2-js_callback',case)

    def test_scope_and_preserved_mutation_blocks(self):
        for field in ('manifest','preserved'):
            scope=self.scope()
            if field=='manifest':scope.manifest['allowed_rows'].pop()
            else:scope.preserved[0]['status']='FAIL'
            with patch.object(s,'load_forward_manifest',return_value=self.manifest):
                with self.assertRaisesRegex(EvaluationError,'LIVE_DRIFT'):scope.require()

    def test_logical_key_independent_of_code_bytes_and_old_default_still_rejects(self):
        with patch.object(s,'FORWARD_FILES',()):changed=s.make_forward_manifest()
        self.assertEqual(changed['recovery_key'],self.manifest['recovery_key'])
        self.assertNotEqual(digest(changed),digest(self.manifest))
        with self.assertRaises(EvaluationError):s.load_manifest()
        with self.assertRaises(EvaluationError):s.parent.load_manifest()

    def test_prepare_and_status_never_take_claim_write_permit_or_construct_backend(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(r,'ROOT',Path(folder)), \
             patch.object(r,'output_path',side_effect=lambda p:p),patch.object(s,'load_forward_manifest',return_value=self.manifest), \
             patch.object(r.old,'NodeBackend') as backend,patch.object(r,'ZeroStartClaim') as claim, \
             patch('sys.stdout',new=io.StringIO()) as output:
            self.assertEqual(r.main(['--forward61','--output',str(Path(folder)/'prepared')]),0)
            receipt=json.loads(output.getvalue());self.assertEqual(receipt['rows'],61)
            self.assertFalse(receipt['approval_written']);self.assertFalse(receipt['claim_taken'])
            backend.assert_not_called();claim.assert_not_called()
        self.assertFalse(s.FORWARD_PERMIT.exists())
        with patch('sys.stdout',new=io.StringIO()),patch.object(r.old,'NodeBackend') as backend:
            self.assertEqual(r.main(['--forward61','--status']),0);backend.assert_not_called()

    def mock_run(self,folder,*,failure=None,precheck=False,write_failure=None):
        scope=self.scope();root=Path(folder);(root/'results').mkdir(exist_ok=True)
        fake=SimpleNamespace(_ctl=lambda *a:'[]');calls=[]
        proof=SimpleNamespace(created=time.monotonic(),require=lambda backend:None)
        def evaluate(base,sid,*args):
            case=args[-1];calls.append((sid,case['id']))
            if failure is not None:raise failure
            rep=base.slots[sid]['representation']
            return {'status':'PASS','actual_ticks':case['expected_ticks'],'expected_ticks':case['expected_ticks'],
                'cleanup_verified':True,'replay_match':True,'primary_case_executions':1,'candidate_executions':1,
                'IR_case_replays':int(rep=='gameir_text'),'JS_transcript_replays':int(rep=='js_callback')}
        original=r.old.write_json
        def write(p,v):
            if write_failure and p.name.startswith(write_failure):raise OSError('PUBLIC MOCK')
            original(p,v)
        with ExitStack() as stack:
            for owner,name,value in [(r,'ROOT',root),(r,'ZERO_CLAIMS',root/'results/claims'),
                (r,'ZERO_LAUNCHES',root/'results/launches')]:stack.enter_context(patch.object(owner,name,value))
            stack.enter_context(patch.object(r,'process_identity',return_value={'pid':123,'start_ticks':'456'}))
            stack.enter_context(patch.object(s.ApprovedZeroStartRecovery,'require'))
            stack.enter_context(patch.object(s.parent.ApprovedPilot24Scope,'require'))
            for name in ('NodeBackend','Pilot24IRBackend'):stack.enter_context(patch.object(r.old,name,return_value=fake))
            stack.enter_context(patch.object(r.old,'fresh',side_effect=EvaluationError('PUBLIC MOCK') if precheck else lambda *a:proof))
            stack.enter_context(patch.object(r.old,'CandidateParserBackend'))
            stack.enter_context(patch.object(r.old,'parse_module',return_value={}))
            stack.enter_context(patch.object(r.old,'parse_status',return_value='PARSED'))
            stack.enter_context(patch.object(r.old,'ir_call',return_value={'result':{'admission':'PARSED'}}))
            for name in ('evaluate_js','evaluate_ir'):stack.enter_context(patch.object(r.old,name,side_effect=evaluate))
            stack.enter_context(patch.object(r.old,'write_json',side_effect=write))
            summary=r.run_controller(scope,root/'results/pilot24-recovery-score-mock')
            with self.assertRaisesRegex(EvaluationError,'PERMIT_ALREADY_CONSUMED'):
                r.ZeroStartClaim(scope,root/'results/pilot24-recovery-score-other')
            claim=json.loads((root/'results/claims'/self.manifest['recovery_key']/'claim.json').read_text())
            self.assertEqual(claim['maximum_primary_runs'],61)
            self.assertEqual(claim['prior_evidence_digest'],s.CONTINUED_DIGEST)
        return summary,calls

    def test_mock_full61_preserves_fixed288(self):
        with tempfile.TemporaryDirectory() as folder:
            summary,calls=self.mock_run(folder)
        self.assertEqual(calls,s.expected_ids()[21:])
        self.assertEqual(summary['combined_fixed_288']['counts'],{'PASS':273,'FAIL':2,'NOTRUN':12,'HARNESS_ERROR':1})
        self.assertEqual(summary['remainder']['counts'],{'PASS':61,'FAIL':0,'NOTRUN':0,'HARNESS_ERROR':0})
        self.assertEqual(summary['model_calls'],0)

    def test_forward_launcher_passes_version_and_new_intent_is_one_use(self):
        scope=self.scope()
        with tempfile.TemporaryDirectory() as folder,patch.object(r,'ROOT',Path(folder)), \
             patch.object(r,'ZERO_LAUNCHES',Path(folder)/'launches'), \
             patch.object(s.ApprovedZeroStartRecovery,'require'),patch.object(r,'direct_preflight',return_value={}) as preflight, \
             patch.object(r,'process_identity',return_value={'pid':123,'start_ticks':'456'}), \
             patch.object(r.subprocess,'Popen',return_value=SimpleNamespace(pid=123)) as popen:
            result=r.launch_zero_start(scope,Path(folder)/'results/pilot24-recovery-score-mock')
            self.assertEqual(result['state'],'DIRECT_PROCESS_LAUNCH_ACCEPTED')
            self.assertIn('--forward61',popen.call_args.args[0]);preflight.assert_called_once_with(scope.manifest)
            with self.assertRaisesRegex(EvaluationError,'LAUNCH_ALREADY_RESERVED'):
                r.launch_zero_start(scope,Path(folder)/'results/pilot24-recovery-score-other')
            self.assertEqual(popen.call_count,1)

    def test_forward_cli_missing_actual_permit_blocks_without_backend_or_claim(self):
        self.assertFalse(s.FORWARD_PERMIT.exists())
        with patch.object(s,'load_forward_manifest',return_value=self.manifest), \
             patch.object(r.old,'NodeBackend') as backend,patch.object(r,'ZeroStartClaim') as claim, \
             patch.object(r,'launch_zero_start') as launch,patch('sys.stdout',new=io.StringIO()):
            self.assertEqual(r.main(['--forward61','--execute-authorized-zero-start',
                                    '--output','results/pilot24-recovery-score-unused']),2)
            backend.assert_not_called();claim.assert_not_called();launch.assert_not_called()

    def test_prior_controller_missing_bad_alive_or_unreadable_blocks(self):
        good={'pid':231995,'start_ticks':'4145786'}
        with patch.object(s,'load_forward_manifest',return_value=self.manifest):
            for identity in (None,{}, {'pid':True,'start_ticks':'1'}, {'pid':1,'start_ticks':'bad'}):
                with patch.object(r,'read',return_value={'controller_identity':identity}), \
                     patch.object(r,'process_alive') as alive:
                    with self.assertRaisesRegex(EvaluationError,'IDENTITY_UNVERIFIED'):
                        r.forward_prior_controller_stopped(self.manifest)
                    alive.assert_not_called()
            for failure in (True,PermissionError('PUBLIC MOCK'),OSError('PUBLIC MOCK')):
                with patch.object(r,'read',return_value={'controller_identity':good}), \
                     patch.object(r,'process_alive',side_effect=failure if isinstance(failure,Exception) else None,
                                  return_value=failure if type(failure) is bool else None):
                    with self.assertRaises((EvaluationError,OSError)):r.forward_prior_controller_stopped(self.manifest)
            with patch.object(r,'read',return_value={'controller_identity':good}),patch.object(r,'process_alive',return_value=False):
                r.forward_prior_controller_stopped(self.manifest)

    def test_precheck_failure_no_reservation_or_dispatch(self):
        with tempfile.TemporaryDirectory() as folder:
            summary,calls=self.mock_run(folder,precheck=True)
            self.assertEqual(calls,[])
            self.assertEqual(summary['remainder']['primary_runs_upper'],0)
            self.assertFalse(list((Path(folder)/'results/claims').rglob('case-*.json')))

    def test_postreservation_close_failures_are_unknown_and_stop(self):
        for failure in (OSError('PUBLIC MOCK'),subprocess.TimeoutExpired('public-mock',1)):
            with tempfile.TemporaryDirectory() as folder:
                summary,calls=self.mock_run(folder,failure=failure)
                self.assertEqual(len(calls),1)
                self.assertEqual(summary['remainder']['counts'],{'PASS':0,'FAIL':0,'NOTRUN':60,'HARNESS_ERROR':1})
                self.assertEqual(summary['remainder']['primary_runs_lower'],0)
                self.assertEqual(summary['remainder']['primary_runs_upper'],1)

    def test_persistence_failure_retains_consumed_case_without_false_zero(self):
        for prefix,count in [('reservation-',0),('result-',1)]:
            with tempfile.TemporaryDirectory() as folder:
                summary,calls=self.mock_run(folder,write_failure=prefix)
                self.assertEqual(len(calls),count)
                self.assertEqual(summary['remainder']['primary_runs_upper'],1)
                self.assertEqual(summary['remainder']['counts']['PASS'],0)


class FixtureDiagnosticsTests(unittest.TestCase):
    def backend(self,worker=None,error=None):
        backend=object.__new__(NodeBackend);backend.disabled=False
        backend.invocation=Mock(side_effect=error,return_value=worker)
        return backend

    def session(self):
        return SimpleNamespace(invocation={},failed=None,receive=Mock(),closed=False)

    def test_invocation_failure_retains_first_exception_and_unknown_audit(self):
        backend=self.backend(error=OSError('DO NOT STORE RAW'))
        runner=IsolatedJSRunner('disconnect',backend)
        with patch('ark_eval.js_runner.MockClient'):
            runner.run(self.session(),{})
        self.assertTrue(backend.disabled);self.assertEqual(len(runner.audits),1)
        self.assertEqual(runner.first_infrastructure_exception,{'stage':'invocation_or_transport',
            'exception_type':'OSError','raw_message_saved':False})
        self.assertEqual(runner.audits[0]['surviving_owned_processes'],'UNKNOWN')
        self.assertNotIn('DO NOT STORE RAW',json.dumps(runner.audits))

    def test_cleanup_oserror_timeout_retains_audit_and_first_execution_exception(self):
        for close in (OSError('RAW CLOSE'),subprocess.TimeoutExpired('RAW CLOSE',1)):
            worker=SimpleNamespace(audit={'cleanup_errors':[],'surviving_owned_processes':0},
                open=Mock(side_effect=ValueError('RAW FIRST')),close=Mock(side_effect=close))
            backend=self.backend(worker);runner=IsolatedJSRunner('disconnect',backend)
            with patch('ark_eval.js_runner.MockClient'):runner.run(self.session(),{})
            self.assertTrue(backend.disabled)
            self.assertEqual(runner.first_infrastructure_exception['exception_type'],'ValueError')
            self.assertEqual(runner.audits[0]['terminal'],'ISOLATION_CLEANUP_FAILED')
            self.assertEqual(runner.audits[0]['surviving_owned_processes'],'UNKNOWN')
            self.assertNotIn('RAW',json.dumps(runner.audits))

    def test_cleanup_exception_without_prior_infrastructure_retains_cleanup_type(self):
        worker=SimpleNamespace(audit={'cleanup_errors':[],'surviving_owned_processes':0},
            open=Mock(side_effect=Fault('JS_DISCONNECTED',phase='callback')),close=Mock(side_effect=OSError('RAW')))
        backend=self.backend(worker);runner=IsolatedJSRunner('disconnect',backend)
        with patch('ark_eval.js_runner.MockClient'):runner.run(self.session(),{})
        self.assertEqual(runner.first_infrastructure_exception['stage'],'cleanup');self.assertTrue(backend.disabled)

    def test_common_collector_keeps_all12_and_pilot_avoids_legacy_pair_receipt(self):
        backend=SimpleNamespace(disabled=False)
        with tempfile.TemporaryDirectory() as folder,patch.object(c,'profile_probe',return_value={'checks':{}}), \
             patch.object(c,'fault_case',side_effect=lambda b,n:{'fixture':n,'status':'PASS'}) as fault, \
             patch.object(c,'FreshIsolation',side_effect=EvaluationError('PAIR_CONTRACT_DRIFT')), \
             patch('sys.stdout',new=io.StringIO()):
            profile,faults=c.collect_isolation_evidence(backend,Path(folder))
            self.assertEqual(len(faults),12);self.assertEqual(fault.call_count,12)
        with tempfile.TemporaryDirectory() as folder,patch.object(e,'collect_isolation_evidence',return_value=(profile,faults)), \
             patch.object(e,'FreshPilotIsolation',return_value='PUBLIC MOCK') as receipt:
            self.assertEqual(e.fresh(None,backend,'js_callback',Path(folder)/'fresh'),'PUBLIC MOCK')
            receipt.assert_called_once()

    def test_missing_audit_reports_gap_instead_of_masking_first_exception(self):
        from ark_eval import js_verify as v
        backend=self.backend()
        runner=IsolatedJSRunner('disconnect',backend)
        # Keep real rollback/replay; the mock runner refuses through the host without an audit.
        def no_audit(session,payload):
            from ark_eval.host import MockClient
            MockClient(session).fault('JS_DISCONNECTED')
        runner.run=no_audit
        with patch.object(v,'IsolatedJSRunner',return_value=runner):
            row=v.fault_case(backend,'disconnect')
        self.assertEqual(row['status'],'HARNESS_ERROR')
        self.assertEqual(row['diagnostic_gap'],'FIRST_EXCEPTION_NOT_RECORDED_AND_WORKER_AUDIT_UNAVAILABLE')
        self.assertIsNone(row['first_infrastructure_exception']);self.assertTrue(backend.disabled)

    def test_fixture_preserves_runtime_first_exception_with_no_audit(self):
        from ark_eval import js_verify as v
        backend=self.backend();runner=IsolatedJSRunner('disconnect',backend)
        with patch.object(v,'IsolatedJSRunner',return_value=runner), \
             patch.object(v.HostRuntime,'step',side_effect=OSError('RAW MUST NOT LEAK')):
            row=v.fault_case(backend,'disconnect')
        self.assertEqual(row['status'],'HARNESS_ERROR')
        self.assertEqual(row['first_infrastructure_exception']['stage'],'fixture_runtime')
        self.assertEqual(row['diagnostic_gap'],'WORKER_AUDIT_UNAVAILABLE')
        self.assertNotIn('RAW MUST NOT LEAK',json.dumps(row))

    def test_oom_none_resource_fields_never_mask_first_execution_or_cleanup_exception(self):
        from ark_eval import js_verify as v
        for close in (None,OSError('RAW CLOSE'),subprocess.TimeoutExpired('RAW CLOSE',1)):
            worker=SimpleNamespace(audit={'cleanup_errors':[],'surviving_owned_processes':0,
                'initial_memory_events':None,'final_memory_events':None},
                open=Mock(side_effect=OSError('RAW FIRST')),close=Mock(side_effect=close))
            backend=self.backend(worker)
            row=v.fault_case(backend,'oom_after_write')
            self.assertEqual(row['status'],'HARNESS_ERROR')
            self.assertEqual(row['first_infrastructure_exception'],{'stage':'invocation_or_transport',
                'exception_type':'OSError','raw_message_saved':False})
            self.assertTrue(backend.disabled);self.assertNotIn('RAW FIRST',json.dumps(row))


if __name__=='__main__':unittest.main()
