"""Exact unreserved 51 boundary tests; no real worker, permit, claim or candidate."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ark_eval.adapter import EvaluationError, digest
from ark_eval import pilot24_forward51_scope as s
from ark_eval import pilot24_recovery_scope as old
from ark_eval import pilot24_recovery as r


class Forward51Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = s.load_forward_manifest()
        cls.cases,cls.preserved,cls.allowed = s.forward_reconciliation()

    def test_exact_51_preserves_279_other_rows_and_9_durable_results(self):
        _,before,allowed60 = s.previous.forward_reconciliation()
        changed = {(x['slot_id'],x['case_id']) for x in allowed60[:9]}
        self.assertEqual(self.allowed,allowed60[9:])
        self.assertEqual(len(self.allowed),51)
        for a,b in zip(before,self.preserved):
            if (a['slot_id'],a['case_id']) not in changed:self.assertEqual(a,b)
        self.assertEqual(r.accounting(self.preserved)['counts'],
                         {'PASS':221,'FAIL':2,'NOTRUN':63,'HARNESS_ERROR':2})
        self.assertEqual(self.manifest['preserved_durable_rows'],223)
        self.assertEqual(self.manifest['maximum_primary_runs'],51)
        self.assertEqual(self.manifest['limits'],s.read(s.PREVIOUS_FREEZE)['limits'])

    def test_old_gates_closed_and_consumed_freeze_unchanged(self):
        self.assertEqual(s.sha(s.PREVIOUS_FREEZE),s.PREVIOUS_FREEZE_SHA)
        self.assertEqual(old.forward_provider(self.manifest),s)
        for load in (s.previous.load_forward_manifest,old.load_forward_manifest,old.load_manifest,old.parent.load_manifest):
            with self.assertRaises(EvaluationError):load()
        self.assertNotEqual(self.manifest['recovery_key'],s.PREVIOUS_KEY)

    def test_production_and_admin_drift_rejected(self):
        original = s.sha
        names = list(s.ADMIN_ANCHORS)+[s.previous.RETAINED_V4,s.previous.RETAINED_V4_1,
            s.RETAINED_V5,s.PREVIOUS_FREEZE,s.PREVIOUS_PERMIT,
            s.PREVIOUS_OUTPUT+'/result-C1-r2-gameir_text-R01.json',s.PREVIOUS_CLAIM+'/claim.json']
        for name in names:
            with self.subTest(name=name),patch.object(s,'sha',side_effect=lambda n:
                    '0'*64 if n == name else original(n)):
                with self.assertRaises(EvaluationError):s.retained_forward60()

    def test_old_and_expanded_permits_rejected(self):
        valid = {**s.forward_fields(self.manifest),'human_statement':'PUBLIC MEMORY MOCK ONLY'}
        old_permit = {**s.previous.forward_fields(s.read(s.PREVIOUS_FREEZE)),
                      'human_statement':'PUBLIC MEMORY MOCK ONLY'}
        pre_review_permit = {**s.forward_fields(s.read(s.RETAINED_V5)),
                             'human_statement':'PUBLIC MEMORY MOCK ONLY'}
        bad = [old_permit,pre_review_permit]+[{**valid,k:v} for k,v in [('maximum_primary_runs',60),
            ('human_explicit_approval',1),('recovery_key',s.PREVIOUS_KEY),
            ('allowed_rows_digest','0'*64),('excluded_unknown_rows',[])]]
        with patch.object(s,'load_forward_manifest',return_value=self.manifest):
            s.validate_forward_permit(self.manifest,valid)
            for permit in bad:
                with self.assertRaises(EvaluationError):s.validate_forward_permit(self.manifest,permit)

    def test_base_excludes_completed_unknown_and_static_and_changed_cases(self):
        permit = {**s.forward_fields(self.manifest),'human_statement':'PUBLIC MEMORY MOCK ONLY'}
        execution = s.forward_execution_manifest(self.manifest)
        with patch.object(s,'load_forward_manifest',return_value=self.manifest), \
             patch.object(s,'forward_execution_manifest',return_value=execution), \
             patch.object(s,'forward_static_manifest',return_value=old.read(old.parent.STATIC)):
            scope=old.parent.ApprovedPilot24Scope(execution,permit,forward_manifest=self.manifest)
        with patch.object(old.parent.ApprovedPilot24Scope,'require'):
            excluded = (old.UNKNOWN,s.previous.NEW_UNKNOWN,('C1-r2-gameir_text','R01'),
                        ('M1-r1-js_callback','N01'),('R1-r1-gameir_text','N01'))
            for sid,cid in excluded:
                case=next(c for c in self.cases[sid[:2]] if c['id']==cid)
                with self.assertRaises(EvaluationError):scope.case(sid,case)
            row=self.allowed[0];case=copy.deepcopy(next(c for c in self.cases[row['slot_id'][:2]] if c['id']==row['case_id']))
            scope.case(row['slot_id'],case)
            case['expected_ticks']=[]
            with self.assertRaises(EvaluationError):scope.case(row['slot_id'],case)

    def test_code_binding_changes_do_not_create_an_execution_opportunity(self):
        changed=copy.deepcopy(self.manifest);changed['bindings']['PUBLIC_MOCK']='0'*64
        identity={k:changed[k] for k in ('parent_scope','parent_batch_key','retained_evidence_digest',
            'consumed_scope','consumed_evidence_digest','consumed_recovery_key','allowed_rows')}
        self.assertEqual(digest(identity),self.manifest['recovery_key'])
        self.assertNotEqual(digest(changed),digest(self.manifest))

    def test_live_or_malformed_old_controller_identity_rejects(self):
        identity={'pid':291176,'start_ticks':'8144959'}
        with patch.object(s,'load_forward_manifest',return_value=self.manifest), \
             patch.object(r,'read',return_value={'controller_identity':identity}) as read, \
             patch.object(r,'process_alive',return_value=False) as alive:
            r.forward_prior_controller_stopped(self.manifest)
            read.assert_called_once_with(s.PREVIOUS_CLAIM+'/claim.json')
            alive.assert_called_once_with(identity)
            alive.return_value=True
            with self.assertRaisesRegex(EvaluationError,'NOT_STOPPED'):r.forward_prior_controller_stopped(self.manifest)
        with patch.object(s,'load_forward_manifest',return_value=self.manifest):
            for value in ({},{'controller_identity':{'pid':True,'start_ticks':'1'}},
                          {'controller_identity':{'pid':1,'start_ticks':'bad'}}):
                with patch.object(r,'read',return_value=value):
                    with self.assertRaises(EvaluationError):r.forward_prior_controller_stopped(self.manifest)

    def test_launcher_flag_and_single_use_across_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'results').mkdir()
            scope=object.__new__(old.ApprovedZeroStartRecovery)
            scope.manifest=self.manifest;scope.approval_digest='0'*64;scope.require=lambda:None
            with patch.object(r,'ROOT',root), \
                 patch.object(r,'ZERO_LAUNCHES',root/'results/launches'), \
                 patch.object(r,'ZERO_CLAIMS',root/'results/claims'), \
                 patch.object(r,'direct_preflight',return_value={'PUBLIC':'MOCK'}), \
                 patch.object(r,'process_identity',return_value={'pid':123,'start_ticks':'456'}), \
                 patch.object(r.subprocess,'Popen',return_value=SimpleNamespace(pid=123)) as child:
                output=root/'results/pilot24-recovery-score-public-mock-001'
                receipt=r.launch_zero_start(scope,output)
                self.assertEqual(receipt['state'],'DIRECT_PROCESS_LAUNCH_ACCEPTED')
                self.assertIn('--forward51',child.call_args.args[0])
                self.assertNotIn('--forward60',child.call_args.args[0])
                with self.assertRaisesRegex(EvaluationError,'LAUNCH_ALREADY_RESERVED'):
                    r.launch_zero_start(scope,root/'results/pilot24-recovery-score-public-mock-002')
                r.ZeroStartClaim(scope,output)
                with self.assertRaisesRegex(EvaluationError,'PERMIT_ALREADY_CONSUMED'):
                    r.ZeroStartClaim(scope,root/'results/pilot24-recovery-score-public-mock-003')
                child.assert_called_once()


if __name__ == '__main__':unittest.main()
