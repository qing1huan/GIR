"""Schema-only evaluator fixtures: no model evidence, source or execution."""
import copy
import unittest
from ark_eval.adapter import EvaluationError
from ark_eval.contract_assessment import RULES,assessment_policy,make_assessment

SOURCE='1'*64
PROTOCOL='2'*64

def receipt(status='PASS',coverage='FULL_RULE'):
    # Explicit synthetic verified-evaluator record, not a real model finding.
    return {'status':status,'method':'STATIC_REVIEW','coverage':coverage,
        'evidence':[{'reference':'synthetic-evaluator-review.json','sha256':'3'*64}],
        'origin':'EVALUATOR_VERIFIED','representation':'js_callback',
        'source_sha256':SOURCE,'protocol_digest':PROTOCOL}

def report(receipts=None,gameplay='PASS'):
    return make_assessment(gameplay,representation='js_callback',source_sha256=SOURCE,
                           protocol_digest=PROTOCOL,receipts=receipts)

class ContractAssessmentTests(unittest.TestCase):
    def test_gameplay_pass_never_implies_checked_contract(self):
        result=report()
        self.assertEqual(result['gameplay_status'],'PASS')
        self.assertEqual(result['contract_status'],'NOT_CHECKED')
        self.assertEqual(result['checked_rule_ids'],[])
        self.assertTrue(all(r['status']=='NOT_CHECKED' for r in result['rules']))

    def test_evidenced_counterexample_fails_even_when_other_rules_unchecked(self):
        result=report({'branch_error_semantics':receipt('FAIL','COUNTEREXAMPLE')})
        self.assertEqual(result['gameplay_status'],'PASS')
        self.assertEqual(result['contract_status'],'FAIL')
        self.assertEqual(result['checked_rule_ids'],['branch_error_semantics'])

    def test_model_claim_empty_or_unverified_evidence_never_becomes_pass(self):
        for field,value in [('origin','MODEL_CLAIM'),('origin','UNVERIFIED'),('evidence',[]),('method',None)]:
            r=receipt();r[field]=value
            for status in ('PASS','FAIL'):
                r['status']=status
                result=report({'source_abi':r})
                self.assertEqual(result['rules'][0]['status'],'NOT_CHECKED')
                self.assertEqual(result['contract_status'],'NOT_CHECKED')

    def test_partial_positive_scope_remains_not_checked(self):
        result=report({'source_abi':receipt('PASS','PARTIAL')})
        self.assertEqual(result['rules'][0]['status'],'NOT_CHECKED')
        self.assertEqual(result['rules'][0]['coverage'],'PARTIAL')
        self.assertEqual(result['contract_status'],'NOT_CHECKED')

    def test_checked_positive_one_rule_does_not_hide_remaining_unknowns(self):
        result=report({'source_abi':receipt()})
        self.assertEqual(result['checked_rule_ids'],['source_abi'])
        self.assertEqual(result['contract_status'],'NOT_CHECKED')

    def test_complete_trusted_receipt_set_is_serialized_without_changing_gameplay(self):
        result=report({rule['id']:receipt() for rule in RULES},gameplay='FAIL')
        self.assertEqual(result['contract_status'],'PASS')
        self.assertEqual(result['gameplay_status'],'FAIL')
        self.assertTrue(result['schema_only'])
        self.assertIn('NO_AUTOMATIC_FACT_CHECK',result['evidence_verification'])

    def test_binding_unknown_rules_status_and_descriptor_errors_fail_closed(self):
        for field,value in [('protocol_digest','4'*64),('source_sha256','4'*64),('representation','gameir_text')]:
            r=receipt();r[field]=value
            with self.assertRaisesRegex(EvaluationError,'BINDING_MISMATCH'):report({'source_abi':r})
        with self.assertRaisesRegex(EvaluationError,'UNKNOWN_RULE'):report({'redundant_resolve':receipt('FAIL')})
        for field,value in [('status','CHECKED'),('method','AUTO_FROM_ORACLE_PASS'),('evidence',[{'reference':'fixture','sha256':'invalid'}])]:
            r=receipt();r[field]=value
            with self.assertRaises(EvaluationError):report({'source_abi':r})

    def test_unsent_source_has_no_evidence_and_no_pass(self):
        result=make_assessment(representation='gameir_text',protocol_digest=PROTOCOL)
        self.assertIsNone(result['source_sha256'])
        self.assertEqual(result['gameplay_status'],'NOTRUN')
        self.assertEqual(result['contract_status'],'NOT_CHECKED')

    def test_policy_is_causal_not_exact_trace_or_truthiness_ban(self):
        p=assessment_policy()
        self.assertFalse(p['checkers_implemented'])
        self.assertIn('Redundant safe',p['style'])
        self.assertIn('native JS int truthiness is allowed',p['style'])
        result=report()
        self.assertFalse(result['style_observations_affect_gameplay'])
        self.assertTrue(result['native_JS_truthiness_allowed'])

if __name__=='__main__':unittest.main()
