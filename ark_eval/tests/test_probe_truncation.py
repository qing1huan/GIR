import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ark_eval.manual_probe import MODEL,save_bundle,send_once
from ark_eval.probe_diagnose import diagnose_bundle,main
from ark_eval.tests.test_manual_probe import CANARY,reply


def fixture(content='',finish='length',tokens=16,model=MODEL):
    data=json.loads(reply(content=content,model=model,tokens=tokens))
    data['choices'][0]['finish_reason']=finish
    data['choices'][0]['message']['reasoning_content']='fixture reasoning never exported'
    return json.dumps(data).encode()


class ProbeTruncationTests(unittest.TestCase):
    def report(self,body,legacy_failure=False):
        with patch('ark_eval.manual_probe._https_once',return_value=(200,body)) as network:
            audit,response=send_once(CANARY,16)
        self.assertEqual(network.call_count,1)
        if legacy_failure:
            audit['schema']='gir-user-local-probe@2'
            audit.pop('response_observations')
            audit['failure']='UNEXPECTED_CONNECTIVITY_RESPONSE'
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'bundle';save_bundle(path,audit,response)
            before=(path/'audit.json').read_bytes()
            with patch('ark_eval.manual_probe._https_once') as no_network:
                report=diagnose_bundle(path)
            no_network.assert_not_called()
            self.assertEqual(before,(path/'audit.json').read_bytes())
            self.assertNotIn('fixture reasoning never exported',json.dumps(report))
        return audit,report

    def test_legacy_empty_length_failure_is_diagnosed_without_promoting_to_pass(self):
        audit,r=self.report(fixture(),legacy_failure=True)
        self.assertEqual(r['original_failure'],'UNEXPECTED_CONNECTIVITY_RESPONSE')
        self.assertEqual(r['exact_OK_check'],'FAIL')
        self.assertEqual(r['diagnosis'],'OUTPUT_LENGTH_LIMIT_WITH_EMPTY_CONTENT_REPORTED')
        self.assertTrue(r['completion_tokens_equal_output_cap'])
        self.assertTrue(r['content_is_exact_empty_string_by_digest'])
        self.assertEqual(r['reported_real_model_calls'],1)
        self.assertEqual(r['reasoning_allocation'],'UNKNOWN_NOT_SAVED')
        self.assertEqual(r['benchmark_status'],'NOTRUN')

    def test_future_empty_length_has_specific_failure(self):
        audit,r=self.report(fixture())
        self.assertEqual(audit['failure'],'TRUNCATED')
        self.assertEqual(r['exact_OK_check'],'FAIL')

    def test_truncated_OK_is_not_a_complete_reply_pass(self):
        audit,r=self.report(fixture(content='OK'))
        self.assertEqual(audit['failure'],'TRUNCATED')
        self.assertEqual(r['exact_OK_check'],'FAIL')

    def test_empty_stop_has_specific_failure_and_no_inferred_reasoning(self):
        audit,r=self.report(fixture(finish='stop',tokens=0))
        self.assertEqual(audit['failure'],'EMPTY_CONNECTIVITY_CONTENT')
        self.assertEqual(r['diagnosis'],'EMPTY_CONTENT_REPORTED')
        self.assertFalse(r['completion_tokens_equal_output_cap'])

    def test_complete_exact_OK_remains_pass(self):
        audit,r=self.report(reply())
        self.assertEqual(audit['status'],'RESPONSE_IMPORTED')
        self.assertEqual(r['exact_OK_check'],'PASS')
        self.assertEqual(r['diagnosis'],'COMPLETE_OK_RESPONSE_IMPORTED')

    def test_http200_wrong_model_budget_or_reply_is_not_promoted(self):
        for body in [fixture(model='deepseek-v4.1-flash'),fixture(tokens=17),reply(content='different answer')]:
            audit,r=self.report(body)
            self.assertNotEqual(r['exact_OK_check'],'PASS')
            self.assertEqual(audit['status'],'FAIL')

    def test_http401_keeps_unknown_generation_and_no_output_observation(self):
        with patch('ark_eval.manual_probe._https_once',return_value=(401,b'')):
            audit,response=send_once(CANARY)
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'bundle';save_bundle(path,audit,response)
            r=diagnose_bundle(path)
        self.assertEqual(r['exact_model_usage_envelope'],'NOT_ESTABLISHED')
        self.assertEqual(r['exact_OK_check'],'NOTRUN')
        self.assertIsNone(r['reported_real_model_calls'])
        self.assertIsNone(r['content_is_exact_empty_string_by_digest'])

    def test_cli_is_offline_and_existing_directory_is_preserved(self):
        with patch('ark_eval.manual_probe._https_once',return_value=(200,fixture())):
            audit,response=send_once(CANARY)
        with tempfile.TemporaryDirectory() as root,patch('ark_eval.manual_probe._https_once') as network,patch('ark_eval.manual_probe.getpass.getpass') as key,patch('sys.stdout',new=io.StringIO()),patch('sys.stderr',new=io.StringIO()):
            bundle=Path(root)/'bundle';save_bundle(bundle,audit,response)
            output=Path(root)/'diagnosis';argv=['--bundle',str(bundle),'--output',str(output)]
            self.assertEqual(main(argv),0)
            saved=(output/'diagnosis.json').read_bytes()
            with self.assertRaises(SystemExit):main(argv)
            self.assertEqual(saved,(output/'diagnosis.json').read_bytes())
            network.assert_not_called();key.assert_not_called()
