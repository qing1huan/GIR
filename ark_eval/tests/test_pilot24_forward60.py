"""Exact remainder tests; no worker, model, actual permit or claim."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ark_eval.adapter import EvaluationError, digest
from ark_eval import pilot24_forward60_scope as s
from ark_eval import pilot24_recovery_scope as old
from ark_eval import pilot24_recovery as r


class Forward60Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = s.load_forward_manifest()
        cls.cases, cls.preserved, cls.allowed = s.forward_reconciliation()

    def test_exact_60_preserves_all_prior_outcomes(self):
        from ark_eval.pilot24_recovery import accounting
        self.assertEqual(len(self.allowed), 60)
        self.assertEqual([(r['slot_id'],r['case_id']) for r in self.allowed], old.expected_ids()[22:])
        self.assertEqual(accounting(self.preserved)['counts'],
                         {'PASS':212,'FAIL':2,'NOTRUN':72,'HARNESS_ERROR':2})
        self.assertNotIn(s.NEW_UNKNOWN, {(r['slot_id'],r['case_id']) for r in self.allowed})
        self.assertNotIn(old.UNKNOWN, {(r['slot_id'],r['case_id']) for r in self.allowed})
        self.assertEqual(self.manifest['maximum_primary_runs'], 60)
        self.assertEqual(self.manifest['preserved_durable_rows'], 214)
        self.assertEqual(self.manifest['preserved_static_NOTRUN'], 12)

    def test_old_gates_remain_closed_and_freeze_bytes_unchanged(self):
        self.assertEqual(s.sha(old.FORWARD_FREEZE), s.PREVIOUS_FREEZE_SHA)
        self.assertEqual(s.sha(s.RETAINED_V4), s.RETAINED_V4_SHA)
        self.assertEqual(s.sha(s.RETAINED_V4_1), s.RETAINED_V4_1_SHA)
        self.assertEqual(self.manifest['facility_revision'], 'IR_ADMISSION_RECEIPT_RENEWAL@1')
        for load in (old.load_manifest, old.load_forward_manifest, old.parent.load_manifest):
            with self.assertRaises(EvaluationError): load()
        self.assertNotEqual(self.manifest['recovery_key'], s.PREVIOUS_KEY)

    def test_retained_registry_and_production_mutation_rejected(self):
        original = s.sha
        for name in (s.REGISTRY, s.PREVIOUS_OUTPUT+'/summary.json', s.PREVIOUS_PERMIT,
                     s.PREVIOUS_CLAIM+'/case-'+s.NEW_UNKNOWN[0]+'-'+s.NEW_UNKNOWN[1]+'.json'):
            with self.subTest(name=name), patch.object(s,'sha',side_effect=lambda n:
                    '0'*64 if n == name else original(n)):
                with self.assertRaises(EvaluationError): s.retained_forward61()

    def test_new_backend_delta_is_finite_and_node_drift_rejected(self):
        self.assertIn('ark_eval/pilot24_execution_backend.py', old.INTEGRATION_BASE)
        original = old.sha
        with patch.object(old,'sha',side_effect=lambda n:
                '0'*64 if n == 'ark_eval/node_backend.py' else original(n)):
            with self.assertRaises(EvaluationError): s.make_forward_manifest()

    def test_old_61_or_changed_60_permit_rejected(self):
        valid = {**s.forward_fields(self.manifest),'human_statement':'PUBLIC MEMORY MOCK ONLY'}
        bad = [{**old.forward_fields(old.read(old.FORWARD_FREEZE)),
                'human_statement':'PUBLIC MEMORY MOCK ONLY'}]
        bad.append({**s.forward_fields(old.read(s.RETAINED_V4)),
                    'human_statement':'PUBLIC MEMORY MOCK ONLY'})
        bad.append({**s.forward_fields(old.read(s.RETAINED_V4_1)),
                    'human_statement':'PUBLIC MEMORY MOCK ONLY'})
        bad += [{**valid,k:v} for k,v in [('maximum_primary_runs',61),
            ('human_explicit_approval',1),('recovery_key',s.PREVIOUS_KEY),
            ('excluded_unknown_rows',[list(old.UNKNOWN)]),('allowed_rows_digest','0'*64)]]
        with patch.object(s,'load_forward_manifest',return_value=self.manifest):
            s.validate_forward_permit(self.manifest,valid)
            for permit in bad:
                with self.assertRaises(EvaluationError): s.validate_forward_permit(self.manifest,permit)

    def test_base_excludes_both_unknown_completed_and_static_cases(self):
        permit={**s.forward_fields(self.manifest),'human_statement':'PUBLIC MEMORY MOCK ONLY'}
        execution=s.forward_execution_manifest(self.manifest)
        with patch.object(s,'load_forward_manifest',return_value=self.manifest), \
             patch.object(s,'forward_execution_manifest',return_value=execution), \
             patch.object(s,'forward_static_manifest',return_value=old.read(old.parent.STATIC)):
            scope=old.parent.ApprovedPilot24Scope(execution,permit,forward_manifest=self.manifest)
        with patch.object(old.parent.ApprovedPilot24Scope,'require'):
            for sid,cid in (s.NEW_UNKNOWN,old.UNKNOWN,('M1-r1-js_callback','N01'),('R1-r1-gameir_text','N01')):
                case=next(c for c in self.cases[sid[:2]] if c['id']==cid)
                with self.assertRaises(EvaluationError): scope.case(sid,case)
            row=self.allowed[0];case=copy.deepcopy(next(c for c in self.cases[row['slot_id'][:2]]
                                                     if c['id']==row['case_id']))
            scope.case(row['slot_id'],case)
            case['expected_ticks']=[]
            with self.assertRaises(EvaluationError): scope.case(row['slot_id'],case)

    def test_logical_key_stable_across_new_code_binding_changes(self):
        changed=copy.deepcopy(self.manifest);changed['bindings']['new_mock']='0'*64
        identity={k:changed[k] for k in ('parent_scope','parent_batch_key','retained_evidence_digest',
                 'consumed_scope','consumed_evidence_digest','consumed_recovery_key','allowed_rows')}
        self.assertEqual(digest(identity),self.manifest['recovery_key'])
        self.assertNotEqual(digest(changed),digest(self.manifest))

    def test_prior_controller_checks_exact_consumed_v3_identity(self):
        identity={'pid':264770,'start_ticks':'4803805'}
        with patch.object(s,'load_forward_manifest',return_value=self.manifest), \
             patch.object(r,'read',return_value={'controller_identity':identity}) as read, \
             patch.object(r,'process_alive',return_value=False) as alive:
            r.forward_prior_controller_stopped(self.manifest)
            read.assert_called_once_with(s.PREVIOUS_CLAIM+'/claim.json')
            alive.assert_called_once_with(identity)
            alive.return_value=True
            with self.assertRaisesRegex(EvaluationError,'NOT_STOPPED'):
                r.forward_prior_controller_stopped(self.manifest)

    def test_unreadable_missing_or_malformed_prior_identity_rejects(self):
        with patch.object(s,'load_forward_manifest',return_value=self.manifest):
            for value in ({}, {'controller_identity':{'pid':True,'start_ticks':'1'}},
                          {'controller_identity':{'pid':1,'start_ticks':'bad'}}):
                with patch.object(r,'read',return_value=value):
                    with self.assertRaises(EvaluationError):r.forward_prior_controller_stopped(self.manifest)
            with patch.object(r,'read',side_effect=OSError('PUBLIC MOCK')):
                with self.assertRaises(OSError):r.forward_prior_controller_stopped(self.manifest)

    def test_launcher_child_version_and_claim_are_one_use_across_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'results').mkdir()
            scope=object.__new__(old.ApprovedZeroStartRecovery)
            scope.manifest=self.manifest;scope.approval_digest='0'*64;scope.require=lambda:None
            identity={'pid':123,'start_ticks':'456'}
            with patch.object(r,'ROOT',root), \
                 patch.object(r,'ZERO_LAUNCHES',root/'results/launches'), \
                 patch.object(r,'ZERO_CLAIMS',root/'results/claims'), \
                 patch.object(r,'direct_preflight',return_value={'PUBLIC':'MOCK'}) as preflight, \
                 patch.object(r,'process_identity',return_value=identity), \
                 patch.object(r.subprocess,'Popen',return_value=SimpleNamespace(pid=123)) as child:
                output=root/'results/pilot24-recovery-score-public-mock-001'
                receipt=r.launch_zero_start(scope,output)
                self.assertEqual(receipt['state'],'DIRECT_PROCESS_LAUNCH_ACCEPTED')
                preflight.assert_called_once_with(self.manifest)
                self.assertIn('--forward60',child.call_args.args[0])
                self.assertNotIn('--forward61',child.call_args.args[0])
                with self.assertRaisesRegex(EvaluationError,'LAUNCH_ALREADY_RESERVED'):
                    r.launch_zero_start(scope,root/'results/pilot24-recovery-score-public-mock-002')
                child.assert_called_once()
                claim=r.ZeroStartClaim(scope,output)
                with self.assertRaisesRegex(EvaluationError,'PERMIT_ALREADY_CONSUMED'):
                    r.ZeroStartClaim(scope,root/'results/pilot24-recovery-score-public-mock-003')
                self.assertTrue((claim.path/'claim.json').is_file())
                def local_read(relative):
                    return json.loads((root/relative).read_text(encoding='utf-8'))
                with patch.object(r,'read',side_effect=local_read), \
                     patch.object(r,'process_alive',return_value=False), \
                     patch.object(s,'forward_static_manifest',return_value=old.read(old.parent.STATIC)), \
                     patch.object(old,'historical_parent',return_value=old.read(old.parent.FREEZE.relative_to(old.ROOT).as_posix())), \
                     patch.object(old.parent,'static_manifest',side_effect=AssertionError('OLD ROUTE FORBIDDEN')):
                    status=r.direct_status(self.manifest)
                self.assertEqual(len(status['not_reserved']),60)
                self.assertEqual(status['durable_results'],[])
                self.assertTrue(status['claim_consumed'])
                self.assertFalse(status['retry_or_resume_allowed'])


if __name__ == '__main__': unittest.main()
