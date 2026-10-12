"""Narrow recovery guards and fail-stop mocks. No collected candidate execution."""
import copy
import io
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

from ark_eval.adapter import EvaluationError,digest
from ark_eval import pilot24_recovery_scope as s,pilot24_recovery as r


class Pilot24RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest=s.load_manifest()
        cls.parent=s.parent.load_manifest()
        cls.cases,cls.preserved,_=s.reconciliation(cls.parent)

    def scope(self):
        value=object.__new__(s.ApprovedRecovery)
        value.manifest=copy.deepcopy(self.manifest);value.snapshot=digest(value.manifest)
        value.approval_digest='PUBLIC MOCK ONLY'
        value.cases=copy.deepcopy(self.cases);value.preserved=copy.deepcopy(self.preserved)
        value.preserved_digest=digest(value.preserved)
        base=object.__new__(s.parent.ApprovedPilot24Scope)
        base.manifest=copy.deepcopy(self.parent);base._snapshot=digest(base.manifest)
        base.cases=copy.deepcopy(self.cases);base.generated=True
        base.slots={x['slot_id']:x for x in base.manifest['slots']}
        value.base=base
        return value

    def test_exact_82_bound_to_original_sources_oracles_and_1156_files(self):
        m=self.manifest
        self.assertEqual([(x['slot_id'],x['case_id']) for x in m['allowed_rows']],s.expected_ids())
        self.assertEqual(len(m['allowed_rows']),82)
        self.assertEqual(m['retained_evidence_files'],1156)
        self.assertEqual(m['parent_scope'],s.PARENT_SCOPE)
        self.assertNotIn(s.UNKNOWN,s.expected_ids())
        self.assertNotIn(('C2-r2-js_callback','N01'),s.expected_ids())
        self.assertFalse(any(x['slot_id']=='R1-r1-gameir_text' for x in m['allowed_rows']))

    def test_parent_file_byte_drift_stops_before_any_backend(self):
        with patch.object(s,'sha',return_value='0'*64),patch.object(r.old,'NodeBackend') as backend:
            with self.assertRaisesRegex(EvaluationError,'PARENT_EVIDENCE_DRIFT'):s.retained()
            backend.assert_not_called()

    def test_registry_addition_and_omission_rejected(self):
        original=s.read(s.EVIDENCE)
        for change in ('addition','omission'):
            value=copy.deepcopy(original)
            if change=='addition':value['files']['results/extra.json']='0'*64
            else:value['files'].pop(next(iter(value['files'])))
            with patch.object(s,'read',return_value=value):
                with self.assertRaisesRegex(EvaluationError,'EVIDENCE_ANCHOR'):s.retained()

    def test_old_approval_boolean_and_wrong_range_rejected(self):
        old=s.read(s.parent.APPROVAL.relative_to(s.ROOT).as_posix())
        with self.assertRaises(EvaluationError):s.ApprovedRecovery(self.manifest,old)
        approval={**s.approval_fields(self.manifest),'human_statement':'PUBLIC IN-MEMORY MOCK ONLY'}
        for key,value in (('human_explicit_approval',1),('maximum_primary_runs',83),
                          ('parent_scope','0'*64),('allowed_rows_digest','0'*64),('excluded_unknown_rows',[])):
            with self.assertRaises(EvaluationError):s.ApprovedRecovery(self.manifest,{**approval,key:value})

    def test_exact_memory_approval_does_not_write_actual_receipt(self):
        before=s.APPROVAL.exists()
        value=s.ApprovedRecovery(self.manifest,{**s.approval_fields(self.manifest),
                                  'human_statement':'PUBLIC IN-MEMORY MOCK ONLY'})
        self.assertEqual(value.snapshot,digest(self.manifest))
        self.assertEqual(s.APPROVAL.exists(),before)

    def test_scope_excludes_unknown_completed_static_and_changed_oracle(self):
        value=self.scope()
        with patch.object(s.ApprovedRecovery,'require'),patch.object(s.parent.ApprovedPilot24Scope,'require'):
            for sid,cid in (s.UNKNOWN,('C2-r2-js_callback','N01'),('M1-r1-js_callback','N03')):
                case=next(c for c in self.cases[sid[:2]] if c['id']==cid)
                with self.assertRaises(EvaluationError):value.case(sid,case)
            case=copy.deepcopy(next(c for c in self.cases['C2'] if c['id']=='N03'))
            value.case('C2-r2-js_callback',case)
            case['expected_ticks']=[]
            with self.assertRaisesRegex(EvaluationError,'CHANGED_CASE'):value.case('C2-r2-js_callback',case)

    def test_mutable_scope_or_preserved_parent_drift_rejected(self):
        for field in ('manifest','preserved'):
            value=self.scope()
            if field=='manifest':value.manifest['allowed_rows'].pop()
            else:value.preserved[0]['status']='FAIL'
            with patch.object(s,'load_manifest',return_value=self.manifest):
                with self.assertRaisesRegex(EvaluationError,'LIVE_DRIFT'):value.require()

    def test_key_is_independent_of_adapter_bindings(self):
        with patch.object(s,'FILES',()):m=s.make_manifest()
        self.assertEqual(m['recovery_key'],self.manifest['recovery_key'])
        self.assertNotEqual(digest(m),digest(self.manifest))

    def mock_run(self,folder,fail_at=None,write_failure=None,bad_result=False):
        root=Path(folder);(root/'results').mkdir(exist_ok=True)
        scope=self.scope();calls=[]
        fake=SimpleNamespace(_ctl=lambda *a:'[]')
        proof=SimpleNamespace(created=time.monotonic(),require=lambda backend:None)
        original=r.old.write_json
        def evaluate(base,sid,*args):
            case=args[-1];calls.append((sid,case['id']))
            if len(calls)-1==fail_at:raise OSError('PUBLIC MOCK DISCONNECT')
            rep=base.slots[sid]['representation']
            result={'status':'PASS','actual_ticks':case['expected_ticks'],'expected_ticks':case['expected_ticks'],
                    'replay_match':True,'cleanup_verified':True,'primary_case_executions':1,
                    'candidate_executions':1,'IR_case_replays':int(rep=='gameir_text'),
                    'JS_transcript_replays':int(rep=='js_callback')}
            if bad_result:result['cleanup_verified']=False
            return result
        def write(path,value):
            if write_failure and path.name.startswith(write_failure):raise OSError('PUBLIC MOCK PERSISTENCE FAILURE')
            return original(path,value)
        with patch.object(r,'ROOT',root),patch.object(r,'CLAIMS',root/'results/claims'), \
             patch.object(r,'LAUNCHES',root/'results/launches'),patch.object(s.ApprovedRecovery,'require'), \
             patch.object(s.parent.ApprovedPilot24Scope,'require'),patch.object(r.old,'NodeBackend',return_value=fake), \
             patch.object(r.old,'Pilot24IRBackend',return_value=fake),patch.object(r.old,'fresh',return_value=proof), \
             patch.object(r.old,'CandidateParserBackend'),patch.object(r.old,'parse_module',return_value={}), \
             patch.object(r.old,'parse_status',return_value='PARSED'), \
             patch.object(r.old,'ir_call',return_value={'result':{'admission':'PARSED'}}), \
             patch.object(r.old,'evaluate_js',side_effect=evaluate),patch.object(r.old,'evaluate_ir',side_effect=evaluate), \
             patch.object(r.old,'write_json',side_effect=write):
            summary=r.run_controller(scope,root/'results/pilot24-recovery-score-mock')
        return summary,calls

    def test_mock_exact_82_and_merged_288_preserve_old_fail_unknown_static(self):
        with tempfile.TemporaryDirectory() as folder:
            summary,calls=self.mock_run(folder)
            self.assertEqual(calls,s.expected_ids())
            self.assertEqual(summary['remainder']['counts'],{'PASS':82,'FAIL':0,'NOTRUN':0,'HARNESS_ERROR':0})
            self.assertEqual(summary['combined_fixed_288']['counts'],{'PASS':273,'FAIL':2,'NOTRUN':12,'HARNESS_ERROR':1})
            self.assertEqual(summary['combined_fixed_288']['primary_runs_lower'],275)
            self.assertEqual(summary['combined_fixed_288']['primary_runs_upper'],276)
            self.assertIsNone(summary['combined_fixed_288']['JS_transcript_replays'])
            self.assertEqual(summary['state'],'REMAINDER_COMPLETE')

    def test_mock_disconnect_unknown_and_failstop_with_no_later_dispatch(self):
        with tempfile.TemporaryDirectory() as folder:
            summary,calls=self.mock_run(folder,fail_at=2)
            self.assertEqual(len(calls),3)
            self.assertEqual(summary['remainder']['counts'],{'PASS':2,'FAIL':0,'NOTRUN':79,'HARNESS_ERROR':1})
            self.assertEqual(summary['remainder']['primary_runs_upper'],3)
            self.assertEqual(summary['combined_fixed_288']['counts']['HARNESS_ERROR'],2)
            self.assertEqual(summary['state'],'STOPPED_ON_BLOCK')

    def test_reservation_failure_no_dispatch_and_result_loss_keeps_unknown(self):
        for field,count,unknown in (('reservation-',0,0),('result-',1,1)):
            with tempfile.TemporaryDirectory() as folder:
                summary,calls=self.mock_run(folder,write_failure=field)
                self.assertEqual(len(calls),count)
                self.assertEqual(summary['remainder']['counts']['HARNESS_ERROR'],unknown)

    def test_invalid_cleanup_cannot_be_model_fail_or_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            summary,calls=self.mock_run(folder,bad_result=True)
            self.assertEqual(len(calls),1)
            self.assertEqual(summary['remainder']['counts']['HARNESS_ERROR'],1)
            self.assertEqual(summary['remainder']['counts']['FAIL'],0)

    def test_atomic_claim_one_winner_across_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'results').mkdir();scope=self.scope();barrier=Barrier(2)
            with patch.object(r,'ROOT',root),patch.object(r,'CLAIMS',root/'results/claims'), \
                 patch.object(r,'LAUNCHES',root/'results/launches'),patch.object(s.ApprovedRecovery,'require'):
                def take(i):
                    barrier.wait()
                    try:return r.RecoveryClaim(scope,root/('results/pilot24-recovery-score-'+str(i))).path
                    except EvaluationError as e:return str(e)
                with ThreadPoolExecutor(max_workers=2) as pool:values=list(pool.map(take,(1,2)))
            self.assertEqual(sum(isinstance(x,Path) for x in values),1)
            self.assertIn('P24R_ALREADY_CLAIMED',values)

    def test_failed_claim_write_remains_consumed(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'results').mkdir();scope=self.scope()
            with patch.object(r,'ROOT',root),patch.object(r,'CLAIMS',root/'results/claims'), \
                 patch.object(r,'LAUNCHES',root/'results/launches'),patch.object(s.ApprovedRecovery,'require'):
                with patch.object(r.old,'write_json',side_effect=OSError('PUBLIC MOCK')):
                    with self.assertRaises(OSError):r.RecoveryClaim(scope,root/'results/pilot24-recovery-score-one')
                with self.assertRaisesRegex(EvaluationError,'ALREADY_CLAIMED'):
                    r.RecoveryClaim(scope,root/'results/pilot24-recovery-score-two')

    def test_default_preview_no_receipt_claim_service_or_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'results').mkdir()
            with patch.object(r,'ROOT',root),patch.object(s,'load_manifest',return_value=self.manifest), \
                 patch.object(r,'linux_call') as call,patch.object(r.old,'NodeBackend') as worker, \
                 patch('sys.stdout',new=io.StringIO()) as stdout:
                rc=r.main(['--output',str(root/'results/pilot24-recovery-score-preview')])
            self.assertEqual(rc,0);call.assert_not_called();worker.assert_not_called()
            self.assertEqual(json.loads(stdout.getvalue())['candidate_executions'],0)

    def test_missing_approval_and_old_receipt_never_launch(self):
        for receipt in (None,{'schema':'gir-pilot24-explicit-execution-approval@1','human_statement':'old'}):
            with patch.object(s,'load_manifest',return_value=self.manifest), \
                 patch.object(s,'APPROVAL',SimpleNamespace(is_file=lambda:receipt is not None,
                                                         relative_to=lambda root:Path('public-mock'))), \
                 patch.object(r,'read',return_value=receipt),patch.object(r,'launch') as launch, \
                 patch('sys.stdout',new=io.StringIO()):
                self.assertEqual(r.main(['--execute-approved-remainder','--output','public-mock']),2)
                launch.assert_not_called()

    def test_detached_launch_has_restart_no_and_intent_before_service(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'results').mkdir();scope=self.scope();commands=[]
            def call(argv):
                self.assertTrue((root/'results/launches'/scope.manifest['recovery_key']/'intent.json').exists())
                commands.append(argv);return SimpleNamespace(returncode=0)
            with patch.object(r,'ROOT',root),patch.object(r,'LAUNCHES',root/'results/launches'), \
                 patch.object(s.ApprovedRecovery,'require'),patch.object(r,'services_available'), \
                 patch.object(r,'service_state',return_value={'LoadState':'not-found'}),patch.object(r,'linux_call',side_effect=call):
                result=r.launch(scope,root/'results/pilot24-recovery-score-one')
                with self.assertRaisesRegex(EvaluationError,'LAUNCH_ALREADY_RESERVED'):
                    r.launch(scope,root/'results/pilot24-recovery-score-two')
            self.assertEqual(result['state'],'DETACHED_SERVICE_LAUNCH_ACCEPTED')
            self.assertIn('--property=Restart=no',commands[0]);self.assertEqual(len(commands),1)

    def test_unknown_launch_keeps_intent_and_cannot_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'results').mkdir();scope=self.scope()
            with patch.object(r,'ROOT',root),patch.object(r,'LAUNCHES',root/'results/launches'), \
                 patch.object(s.ApprovedRecovery,'require'),patch.object(r,'services_available'), \
                 patch.object(r,'service_state',return_value={'LoadState':'not-found'}), \
                 patch.object(r,'linux_call',side_effect=TimeoutError('PUBLIC MOCK')):
                with self.assertRaisesRegex(EvaluationError,'LAUNCH_OUTCOME_UNKNOWN'):
                    r.launch(scope,root/'results/pilot24-recovery-score-one')
                with self.assertRaisesRegex(EvaluationError,'LAUNCH_ALREADY_RESERVED'):
                    r.launch(scope,root/'results/pilot24-recovery-score-two')

    def test_original_claim_and_all_1156_files_unchanged_after_mocks(self):
        m,bindings=s.retained()
        self.assertEqual(digest(bindings),s.EVIDENCE_DIGEST)
        self.assertEqual(m['batch_key'],s.PARENT_KEY)


if __name__=='__main__':unittest.main()
