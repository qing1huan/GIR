"""Zero-start continuation mocks; no actual permit, process or candidate launch."""
import copy
import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

from ark_eval.adapter import EvaluationError,digest
from ark_eval import pilot24_recovery_scope as s,pilot24_recovery as r
from ark_eval import pilot24_recovery_control as control
from ark_eval.tests import test_pilot24_recovery as legacy


class Pilot24ZeroStartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=legacy.Pilot24RecoveryTests()
        cls.fixture.manifest=s.load_manifest()
        cls.fixture.parent=s.parent.load_manifest()
        cls.fixture.cases,cls.fixture.preserved,_=s.reconciliation(cls.fixture.parent)

    def scope(self):
        legacy=self.fixture.scope()
        value=object.__new__(s.ApprovedZeroStartRecovery)
        value.__dict__.update(legacy.__dict__)
        return value

    def run_mock(self,folder,**options):
        root=Path(folder);scope=self.scope()
        with patch.object(self.fixture,'scope',return_value=scope), \
             patch.object(r,'ZERO_CLAIMS',root/'results/zero-claims'), \
             patch.object(r,'process_identity',return_value={'pid':123,'start_ticks':'42'}):
            return self.fixture.mock_run(folder,**options)

    def test_exact_failed_zero_start_bound_and_logical_key_unchanged(self):
        m=self.fixture.manifest
        self.assertEqual(m['recovery_key'],s.RECOVERY_KEY)
        self.assertEqual(m['zero_start_failure']['evidence_digest'],s.FAILED_DIGEST)
        self.assertEqual(m['zero_start_failure']['scope_digest'],s.FAILED_SCOPE)
        self.assertEqual(m['maximum_primary_runs'],82)
        self.assertEqual(s.zero_start_failure()['primary_reserved'],0)

    def test_prior_reserved_or_extra_file_blocks_before_backend(self):
        original=Path.rglob
        fake=SimpleNamespace(is_file=lambda:True,relative_to=lambda root:Path(s.FAILED_OUTPUT+'/reservation-extra.json'))
        def injected(path,*args):
            values=list(original(path,*args))
            if str(path).endswith('pilot24-recovery-score-001'):values.append(fake)
            return iter(values)
        with patch.object(Path,'rglob',injected),patch.object(r.old,'NodeBackend') as backend:
            with self.assertRaisesRegex(EvaluationError,'FAILED_FILE_SET'):s.zero_start_failure()
            backend.assert_not_called()

    def test_failed_evidence_byte_drift_rejected(self):
        with patch.object(s,'sha',return_value='0'*64):
            with self.assertRaisesRegex(EvaluationError,'FAILED_EVIDENCE_DRIFT'):s.zero_start_failure()

    def test_nonzero_or_unknown_prior_summary_rejected_even_with_mocked_bytes(self):
        original=s.read
        for group in ('known','unknown_records'):
            def read(name):
                value=original(name)
                if name==s.FAILED_OUTPUT+'/summary.json':
                    value['remainder'][group]['primary_case_executions']=1
                return value
            with patch.object(s,'read',side_effect=read):
                with self.assertRaisesRegex(EvaluationError,'NOT_ZERO_START_FAILURE'):s.zero_start_failure()

    def test_old_receipt_wrong_budget_boolean_route_or_failure_cannot_grant(self):
        m=self.fixture.manifest
        old=s.read(s.APPROVAL.relative_to(s.ROOT).as_posix())
        with self.assertRaises(EvaluationError):s.ApprovedZeroStartRecovery(m,old)
        permit={**s.zero_start_fields(m),'human_statement':'PUBLIC IN-MEMORY MOCK ONLY'}
        for key,value in (('maximum_primary_runs',83),('human_explicit_approval',1),
                          ('route','systemd'),('failed_evidence_digest','0'*64),
                          ('excluded_unknown_rows',[]),('allowed_rows_digest','0'*64)):
            with self.assertRaises(EvaluationError):s.ApprovedZeroStartRecovery(m,{**permit,key:value})
        before=s.ZERO_START_PERMIT.exists()
        s.ApprovedZeroStartRecovery(m,permit)
        self.assertEqual(s.ZERO_START_PERMIT.exists(),before)

    def test_mock_82_one_time_case_ledger_and_preserved_288(self):
        with tempfile.TemporaryDirectory() as folder:
            summary,calls=self.run_mock(folder)
            self.assertEqual(calls,s.expected_ids())
            ledger=Path(folder)/'results/zero-claims'/s.RECOVERY_KEY
            self.assertEqual(len(list(ledger.glob('case-*.json'))),82)
            self.assertEqual(summary['combined_fixed_288']['counts'],
                             {'PASS':273,'FAIL':2,'NOTRUN':12,'HARNESS_ERROR':1})
            self.assertEqual(summary['route'],'direct-wsl')

    def test_mock_disconnect_preserves_unknown_and_never_dispatches_later(self):
        with tempfile.TemporaryDirectory() as folder:
            summary,calls=self.run_mock(folder,fail_at=2)
            self.assertEqual(len(calls),3)
            self.assertEqual(summary['remainder']['counts'],{'PASS':2,'FAIL':0,'NOTRUN':79,'HARNESS_ERROR':1})
            ledger=Path(folder)/'results/zero-claims'/s.RECOVERY_KEY
            self.assertEqual(len(list(ledger.glob('case-*.json'))),3)
            self.assertFalse(summary['retry_or_resume_allowed'])

    def test_ledger_or_output_persistence_failure_cannot_hide_duplicate(self):
        for prefix,dispatch,unknown in (('case-',0,0),('reservation-',0,1),('result-',1,1)):
            with tempfile.TemporaryDirectory() as folder:
                summary,calls=self.run_mock(folder,write_failure=prefix)
                self.assertEqual(len(calls),dispatch)
                self.assertEqual(summary['remainder']['counts']['HARNESS_ERROR'],unknown)
                self.assertEqual(summary['state'],'STOPPED_ON_BLOCK')
                self.assertTrue((Path(folder)/'results/zero-claims'/s.RECOVERY_KEY).is_dir())

    def claim_context(self,root):
        return (patch.object(r,'ROOT',root),patch.object(r,'ZERO_CLAIMS',root/'results/zero'),
                patch.object(s.ApprovedRecovery,'require'),
                patch.object(r,'process_identity',return_value={'pid':123,'start_ticks':'42'}))

    def test_atomic_same_logical_claim_across_output_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'results').mkdir();scope=self.scope();barrier=Barrier(2)
            a,b,c,d=self.claim_context(root)
            with a,b,c,d:
                def take(i):
                    barrier.wait()
                    try:return r.ZeroStartClaim(scope,root/('results/pilot24-recovery-score-'+str(i))).path
                    except EvaluationError as error:return str(error)
                with ThreadPoolExecutor(max_workers=2) as pool:values=list(pool.map(take,(1,2)))
            self.assertEqual(sum(isinstance(x,Path) for x in values),1)
            self.assertIn('P24Z_PERMIT_ALREADY_CONSUMED',values)

    def test_claim_write_failure_remains_consumed_and_case_duplicate_denied(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'results').mkdir();scope=self.scope();a,b,c,d=self.claim_context(root)
            with a,b,c,d:
                with patch.object(r.old,'write_json',side_effect=OSError('PUBLIC MOCK')):
                    with self.assertRaises(OSError):r.ZeroStartClaim(scope,root/'results/pilot24-recovery-score-one')
                with self.assertRaisesRegex(EvaluationError,'PERMIT_ALREADY_CONSUMED'):
                    r.ZeroStartClaim(scope,root/'results/pilot24-recovery-score-two')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'results').mkdir();scope=self.scope();a,b,c,d=self.claim_context(root)
            with a,b,c,d:
                claim=r.ZeroStartClaim(scope,root/'results/pilot24-recovery-score-one')
                row=r.remainder_rows(scope)[0];claim.reserve(row)
                with self.assertRaises(FileExistsError):claim.reserve(row)
                row['case_id']='N02'
                with self.assertRaisesRegex(EvaluationError,'LEDGER_RANGE'):claim.reserve(row)

    def test_launch_intent_before_detached_process_no_systemd_or_delegate(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'results').mkdir();scope=self.scope();commands=[]
            def popen(argv,**kwargs):
                self.assertTrue((root/'results/launches'/s.RECOVERY_KEY/'intent.json').is_file())
                commands.append((argv,kwargs));return SimpleNamespace(pid=456)
            with patch.object(r,'ROOT',root),patch.object(r,'ZERO_LAUNCHES',root/'results/launches'), \
                 patch.object(s.ApprovedRecovery,'require'),patch.object(r,'direct_preflight',return_value={}), \
                 patch.object(r.subprocess,'Popen',side_effect=popen), \
                 patch.object(r,'process_identity',return_value={'pid':456,'start_ticks':'42'}), \
                 patch.object(r,'linux_call') as service:
                result=r.launch_zero_start(scope,root/'results/pilot24-recovery-score-one')
                with self.assertRaisesRegex(EvaluationError,'LAUNCH_ALREADY_RESERVED'):
                    r.launch_zero_start(scope,root/'results/pilot24-recovery-score-two')
                service.assert_not_called()
            self.assertEqual(result['state'],'DIRECT_PROCESS_LAUNCH_ACCEPTED')
            self.assertTrue(commands[0][1]['start_new_session']);self.assertTrue(commands[0][1]['close_fds'])
            self.assertFalse(any('systemd' in x or 'Delegate' in x for x in commands[0][0]))

    def test_uncertain_launch_cannot_retry_another_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'results').mkdir();scope=self.scope()
            with patch.object(r,'ROOT',root),patch.object(r,'ZERO_LAUNCHES',root/'results/launches'), \
                 patch.object(s.ApprovedRecovery,'require'),patch.object(r,'direct_preflight',return_value={}), \
                 patch.object(r.subprocess,'Popen',side_effect=OSError('PUBLIC MOCK')):
                with self.assertRaisesRegex(EvaluationError,'OUTCOME_UNKNOWN'):
                    r.launch_zero_start(scope,root/'results/pilot24-recovery-score-one')
                with self.assertRaisesRegex(EvaluationError,'LAUNCH_ALREADY_RESERVED'):
                    r.launch_zero_start(scope,root/'results/pilot24-recovery-score-two')

    def test_missing_permit_and_old_entry_cannot_start_process(self):
        with patch.object(s,'load_manifest',return_value=self.fixture.manifest), \
             patch.object(s,'ZERO_START_PERMIT',SimpleNamespace(is_file=lambda:False)), \
             patch.object(r.subprocess,'Popen') as process,patch('sys.stdout',new=io.StringIO()):
            self.assertEqual(r.main(['--execute-authorized-zero-start','--output','public-mock']),2)
            self.assertEqual(r.main(['--execute-approved-remainder','--output','public-mock']),2)
            process.assert_not_called()

    def test_failure_diagnostics_save_only_fixed_safe_codes(self):
        self.assertEqual(r.safe_failure(EvaluationError('ROOTLESS_RESOURCE_LIMITS_UNAVAILABLE'))['code'],
                         'ROOTLESS_RESOURCE_LIMITS_UNAVAILABLE')
        value=r.safe_failure(OSError('PUBLIC MOCK MESSAGE MUST NOT BE SAVED'))
        self.assertFalse(value['raw_message_saved']);self.assertNotIn('MESSAGE',json.dumps(value))

    def test_invalid_process_identity_never_reads_foreign_path(self):
        with patch.object(Path,'read_text') as read:
            for identity in ({'pid':'../../public-mock','start_ticks':'1'},
                             {'pid':0,'start_ticks':'1'},{'pid':123,'start_ticks':'bad'}):
                self.assertFalse(r.process_alive(identity))
            read.assert_not_called()

    def test_original_and_failed_claim_evidence_unchanged_after_mocks(self):
        _,files=s.retained();self.assertEqual(len(files),1156)
        self.assertEqual(s.zero_start_failure()['evidence_digest'],s.FAILED_DIGEST)

    def status_mock(self,folder,claim_value=None,launch_value=None,probe_error=None,broken_claim=False):
        root=Path(folder).resolve();m=self.fixture.manifest
        launches=root/'results/launches';claims=root/'results/claims'
        launch=launches/s.RECOVERY_KEY;launch.mkdir(parents=True)
        output=root/'results/pilot24-recovery-score-mock'
        intent={'scope_digest':digest(m),'recovery_key':s.RECOVERY_KEY,'permit_digest':'a'*64,
                'output':str(output),'route':'direct-wsl','retry':False}
        (launch/'intent.json').write_text(json.dumps(intent))
        if launch_value is not None:(launch/'launch-result.json').write_text(json.dumps(launch_value))
        if claim_value is not None:
            target=claims/s.RECOVERY_KEY;target.mkdir(parents=True)
            value={'schema':'gir-pilot24-zero-start-one-use@1','scope_digest':digest(m),
                'recovery_key':s.RECOVERY_KEY,'permit_digest':'a'*64,'prior_claim':s.FAILED_CLAIM,
                'prior_evidence_digest':s.FAILED_DIGEST,'output':str(output),'route':'direct-wsl',
                'maximum_primary_runs':82,'retry_or_resume_allowed':False,**claim_value}
            (target/'claim.json').write_text(json.dumps(value))
        def read(name):
            if broken_claim and name.endswith('/claim.json'):raise EvaluationError('INVALID_JSON')
            return json.loads((root/name).read_text())
        with patch.object(r,'ROOT',root),patch.object(r,'ZERO_LAUNCHES',launches), \
             patch.object(r,'ZERO_CLAIMS',claims),patch.object(r,'read',side_effect=read), \
             patch.object(r.sys,'platform','linux'), \
             patch.object(r,'process_alive',return_value=True,side_effect=probe_error) as alive:
            return r.direct_status(m),alive.call_count

    def test_missing_launch_receipt_uses_bound_claim_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            value,calls=self.status_mock(folder,{'controller_identity':{'pid':456,'start_ticks':'42'}})
        self.assertEqual(value['state'],'IN_PROGRESS');self.assertEqual(value['identity_source'],'claim')
        self.assertEqual(calls,1);self.assertFalse(value['retry_or_resume_allowed'])

    def test_missing_invalid_or_unbound_identity_reports_unknown_without_probe(self):
        for claim in (None,{'controller_identity':{'pid':0,'start_ticks':'42'}},
                      {'controller_identity':{'pid':456,'start_ticks':'42'},'permit_digest':'b'*64},
                      {'controller_identity':{'pid':456,'start_ticks':'42'},'maximum_primary_runs':True}):
            with tempfile.TemporaryDirectory() as folder:
                value,calls=self.status_mock(folder,claim)
            self.assertEqual(value['state'],'PROCESS_STATE_UNAVAILABLE');self.assertIsNone(value['running'])
            self.assertEqual(calls,0);self.assertFalse(value['retry_or_resume_allowed'])

    def test_conflicting_bound_process_identities_report_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            value,calls=self.status_mock(folder,{'controller_identity':{'pid':456,'start_ticks':'42'}},
                {'controller_identity':{'pid':789,'start_ticks':'43'},'outcome':'PROCESS_CREATED_RESULTS_UNCONFIRMED'})
        self.assertEqual(value['state'],'PROCESS_STATE_UNAVAILABLE');self.assertEqual(calls,0)

    def test_partial_identity_receipt_or_inaccessible_proc_reports_unknown(self):
        claim={'controller_identity':{'pid':456,'start_ticks':'42'}}
        with tempfile.TemporaryDirectory() as folder:
            value,calls=self.status_mock(folder,claim,broken_claim=True)
        self.assertEqual(value['state'],'PROCESS_STATE_UNAVAILABLE');self.assertEqual(calls,0)
        with tempfile.TemporaryDirectory() as folder:
            value,calls=self.status_mock(folder,claim,probe_error=PermissionError('PUBLIC MOCK'))
        self.assertEqual(value['state'],'PROCESS_STATE_UNAVAILABLE');self.assertEqual(calls,1)

    def test_owned_control_signal_uses_pidfd_and_closes_it(self):
        identity={'pid':456,'start_ticks':'42'}
        with patch.object(control.os,'pidfd_open',return_value=91,create=True) as opened, \
             patch.object(control.signal,'pidfd_send_signal',create=True) as sent, \
             patch.object(control.signal,'SIGKILL',9,create=True), \
             patch.object(control.os,'close') as closed,patch.object(control,'process_identity',return_value=identity), \
             patch.object(control,'process_alive',return_value=True),patch.object(control.os,'kill') as kill:
            control.terminate_owned(identity)
        opened.assert_called_once_with(456,0);sent.assert_called_once_with(91,9,None,0)
        closed.assert_called_once_with(91);kill.assert_not_called()

    def test_reused_pid_or_unavailable_pidfd_never_receives_signal(self):
        identity={'pid':456,'start_ticks':'42'}
        with patch.object(control.os,'pidfd_open',return_value=91,create=True), \
             patch.object(control.signal,'pidfd_send_signal',create=True) as sent, \
             patch.object(control.os,'close') as closed, \
             patch.object(control,'process_identity',return_value={'pid':456,'start_ticks':'43'}):
            with self.assertRaisesRegex(EvaluationError,'IDENTITY_DRIFT'):control.terminate_owned(identity)
        sent.assert_not_called();closed.assert_called_once_with(91)
        with patch.object(control.os,'pidfd_open',None,create=True), \
             patch.object(control.signal,'pidfd_send_signal',create=True) as sent:
            with self.assertRaisesRegex(EvaluationError,'PIDFD_UNAVAILABLE'):control.terminate_owned(identity)
        sent.assert_not_called()


if __name__=='__main__':unittest.main()
