"""Offline draft parity, no hidden test/reference leakage and drift rejection."""
import copy
import unittest
from unittest.mock import patch
from headless_v01.compiler import digest
from ark_eval.adapter import EvaluationError
from ark_eval.pair003_eval import load_candidates
from ark_eval.manual_pair_v3_1 import load_review_requests
from ark_eval.pair003_repair_draft import build_draft,DIAGNOSTICS,SYSTEM_REPLACEMENTS,REPAIR_STAGE_SYSTEM


class Pair003RepairDraftTests(unittest.TestCase):
    def test_budget_model_route_and_context_parity_with_original_source_exact(self):
        proposal=build_draft();sources=load_candidates();_,requests=load_review_requests()
        before={rep:req for rep,req in requests}
        for arm in proposal['arms']:
            rep=arm['representation'];body=arm['request_body']
            original=before[rep].body['messages']
            self.assertEqual(body['messages'][1:-2],original[1:])
            expected=original[0]['content']
            for old,new in SYSTEM_REPLACEMENTS:
                expected=expected.replace(old,new)
                self.assertNotIn(old,body['messages'][0]['content'])
            self.assertEqual(body['messages'][0]['content'],expected+'\n\n'+REPAIR_STAGE_SYSTEM)
            self.assertEqual(body['messages'][-2],{'role':'assistant','content':sources[rep]})
            self.assertEqual(body['model'],proposal['model']);self.assertEqual(body['thinking'],{'type':'disabled'})
            self.assertEqual(body['max_tokens'],4096);self.assertLessEqual(arm['prompt_byte_units'],24000)
            self.assertEqual(arm['request_digest'],digest(body))
            self.assertEqual([d['category'] for d in arm['feedback']['diagnostics']],['format_and_source_abi','branch_error_semantics'])
            self.assertLessEqual(arm['feedback_utf8_bytes'],2048)
        self.assertEqual(proposal['additional_model_calls'],0);self.assertEqual(proposal['candidate_executions'],0)
        self.assertTrue(proposal['token_accounting']['byte_units_are_not_tokens'])
        self.assertTrue(proposal['token_accounting']['actual_usage_may_differ'])
        self.assertFalse(proposal['token_accounting']['exact_tokenizer_required'])
        self.assertFalse(proposal['token_accounting']['padding_added'])

    def test_no_oracle_expected_values_or_reference_solution_in_feedback(self):
        proposal=build_draft()
        for arm in proposal['arms']:
            feedback=arm['request_body']['messages'][-1]['content']
            for forbidden in ('P-C02-create-','"expected":','world','genesis','"handlers":','export function','reference_source'):
                self.assertNotIn(forbidden,feedback)
        self.assertFalse(proposal['evaluation']['hidden_test_or_reference_solution_in_messages'])

    def test_evidence_mismatch_and_excess_feedback_block_offline_proposal(self):
        from ark_eval import pair003_repair_draft as module
        real=module.bounded_json
        def changed(raw):
            value=real(raw);value['JS_candidate_evaluations']=1;return value
        with patch.object(module,'bounded_json',side_effect=changed):
            with self.assertRaisesRegex(EvaluationError,'RESULT_BINDING_REJECTED'):build_draft()
        oversized=copy.deepcopy(DIAGNOSTICS);oversized['js_callback'][0]['message']='x'*513
        with patch.object(module,'DIAGNOSTICS',oversized):
            with self.assertRaisesRegex(EvaluationError,'FEEDBACK_QUOTA_EXCEEDED'):build_draft()


if __name__=='__main__':unittest.main()
