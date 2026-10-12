"""Human-entry tests with non-secret fixture input and mocked HTTP only."""
import copy
import getpass
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ark_eval.adapter import EvaluationError
from ark_eval.contract_v3 import load_review
from ark_eval.create_pair import import_contract,load_freeze
from ark_eval.manual_pair_v3_1 import PROTOCOL_DIGEST,load_review_requests,main,run_pair
from ark_eval.tests.test_create_pair import body

FIXTURE='public-fixture-only-not-a-key'


class ManualPairV31Tests(unittest.TestCase):
    def test_actual_sent_mock_bodies_are_exact_reviewed_package_and_inert(self):
        contract,requests=load_review_requests()
        with tempfile.TemporaryDirectory() as root,patch('ark_eval.manual_pair._https_once',side_effect=[(200,body('IR inert fixture')),(200,body('JS inert fixture'))]) as network:
            path=Path(root)/'pair003';summary=run_pair(path,FIXTURE)
            self.assertEqual(network.call_count,2)
            for call,arm,(_,request) in zip(network.call_args_list,contract['public_arms'],requests):
                self.assertEqual(call.args[0].body,arm['request_body'])
                self.assertEqual(call.args[0],request);self.assertEqual(call.kwargs,{'timeout':120})
                rep=arm['representation'];audit=json.loads((path/rep/'audit.json').read_text(encoding='utf-8'))
                report=json.loads((path/rep/'contract-assessment.json').read_text(encoding='utf-8'))
                self.assertEqual(audit['execution_status'],'BLOCKED');self.assertEqual(audit['generated_source_executions'],0)
                self.assertEqual(report['source_sha256'],audit['candidate_sha256'])
                self.assertEqual(report['contract_status'],'NOT_CHECKED');self.assertEqual(report['gameplay_status'],'NOTRUN')
                self.assertEqual(report['checked_rule_ids'],[])
                self.assertEqual(json.loads((path/rep/'contract-initial.json').read_text())['source_sha256'],None)
            self.assertEqual(summary['protocol_digest'],PROTOCOL_DIGEST)
            self.assertEqual(summary['request_attempts'],2);self.assertEqual(summary['reported_model_calls'],2)
            self.assertEqual(json.loads((path/'protocol.json').read_text(encoding='utf-8')),contract)
            for p in path.rglob('*'):
                if p.is_file():self.assertNotIn(FIXTURE.encode(),p.read_bytes())

    def test_default_preparation_does_not_read_key_or_send(self):
        with tempfile.TemporaryDirectory() as root,patch('ark_eval.manual_pair._https_once') as net,patch('getpass.getpass') as key,patch('builtins.input') as consent,patch('sys.stdout',new=io.StringIO()):
            path=Path(root)/'prepared';self.assertEqual(main(['--output',str(path)]),0)
            net.assert_not_called();key.assert_not_called();consent.assert_not_called()
            summary=json.loads((path/'summary.json').read_text())
            self.assertEqual(summary['state'],'PREPARED_NOT_SENT');self.assertEqual(summary['request_attempts'],0)

    def test_auth_quota_redirect_transport_truncation_empty_and_model_drift_stop_second_arm(self):
        fixtures=[((401,b''),'AUTH'),((429,b''),'QUOTA_OR_RATE_LIMIT'),((302,b''),'REDIRECT_REJECTED'),
                  (OSError('public fixture'),'TRANSPORT'),((200,body('partial',4096,'length')),'TRUNCATED'),
                  ((200,body('',0)),'EMPTY_RESPONSE'),((200,body(model='deepseek-v4.1-flash')),'MODEL_MISMATCH'),
                  ((200,body(reasoning='unexpected reasoning')),'THINKING_MISMATCH'),
                  ((200,body(tokens=4097)),'USAGE_EXCEEDS_RESERVATION')]
        for response,failure in fixtures:
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as root,patch('ark_eval.manual_pair._https_once',side_effect=[response]) as net:
                path=Path(root)/'pair';summary=run_pair(path,FIXTURE)
                self.assertEqual(net.call_count,1);self.assertEqual(summary['arms'][0]['failure'],failure)
                self.assertEqual(summary['state'],'STOPPED_ON_FAILURE')
                self.assertEqual(summary['arms'][1]['status'],'NOTRUN')
                self.assertFalse((path/'js_callback'/'reservation.json').exists())
                self.assertEqual(summary['automatic_retries'],0);self.assertFalse(summary['paid_fallback'])
                self.assertEqual(summary['reset_cards_used'],0)

    def test_echo_never_written_and_halts(self):
        with tempfile.TemporaryDirectory() as root,patch('ark_eval.manual_pair._https_once',return_value=(200,body(reasoning=FIXTURE))) as net:
            path=Path(root)/'pair';summary=run_pair(path,FIXTURE)
            self.assertEqual(net.call_count,1);self.assertEqual(summary['arms'][0]['failure'],'SECRET_ECHO_REJECTED')
            for p in path.rglob('*'):
                if p.is_file():self.assertNotIn(FIXTURE.encode(),p.read_bytes())

    def test_review_or_request_drift_rejected_before_network(self):
        contract=load_review();changed=copy.deepcopy(contract);changed['public_arms'][0]['request_body']['max_tokens']=4095
        with patch('ark_eval.manual_pair_v3_1.load_review',return_value=changed),patch('ark_eval.manual_pair._https_once') as net:
            with self.assertRaisesRegex(EvaluationError,'DIGEST_MISMATCH'):load_review_requests()
            net.assert_not_called()
        original=Path.read_text
        def corrupted(path,*args,**kwargs):
            value=original(path,*args,**kwargs)
            if path.as_posix().endswith('/package/gameir_text/request.json'):
                d=json.loads(value);d['max_tokens']=4095;return json.dumps(d)
            return value
        with patch.object(Path,'read_text',corrupted):
            with self.assertRaisesRegex(EvaluationError,'REQUEST_DRIFT'):load_review_requests()

    def test_mid_pair_drift_stops_and_records_notrun(self):
        original=load_review_requests()
        with tempfile.TemporaryDirectory() as root,patch('ark_eval.manual_pair_v3_1.load_review_requests',side_effect=[original,original,EvaluationError('FIXTURE_DRIFT')]),patch('ark_eval.manual_pair._https_once',return_value=(200,body())) as net:
            summary=run_pair(Path(root)/'pair',FIXTURE)
            self.assertEqual(net.call_count,1);self.assertEqual(summary['arms'][1]['status'],'NOTRUN')

    def test_existing_output_never_overwritten_or_sent(self):
        with tempfile.TemporaryDirectory() as root,patch('ark_eval.manual_pair._https_once') as net:
            with self.assertRaises(FileExistsError):run_pair(Path(root),FIXTURE)
            net.assert_not_called()

    def test_old_entry_stays_v2_and_rejects_review_contract(self):
        self.assertEqual(load_freeze()['schema'],'gir-create-pair-freeze@2')
        with self.assertRaisesRegex(EvaluationError,'CONTRACT_DRIFT'):import_contract(load_review())

    def test_user_plan_and_private_terminal_required_before_hidden_input(self):
        for confirmed in (False,True):
            with tempfile.TemporaryDirectory() as root,patch('sys.stdout',new=io.StringIO()),patch('getpass.getpass') as key,patch('ark_eval.manual_pair._https_once') as net:
                argv=['--user-local-send-pair','--output',str(Path(root)/'pair')]
                if confirmed:argv.append('--plan-status-confirmed')
                self.assertEqual(main(argv),2);key.assert_not_called();net.assert_not_called()

    def test_hidden_input_fallback_and_cancelled_consent_never_send(self):
        for key_result,consent in [(getpass.GetPassWarning('fixture'),'SEND_PAIR003'),(FIXTURE,'cancel')]:
            with tempfile.TemporaryDirectory() as root,patch('sys.stdin.isatty',return_value=True),patch('sys.stdout',new=io.StringIO()) as stdout,patch.object(stdout,'isatty',return_value=True),patch('getpass.getpass',side_effect=key_result if isinstance(key_result,Exception) else None,return_value=key_result),patch('builtins.input',return_value=consent),patch('ark_eval.manual_pair._https_once') as net:
                path=Path(root)/'pair'
                self.assertEqual(main(['--user-local-send-pair','--plan-status-confirmed','--output',str(path)]),2)
                net.assert_not_called();self.assertFalse(path.exists());self.assertNotIn(FIXTURE,stdout.getvalue())

    def test_explicit_user_confirmation_reaches_two_mock_requests(self):
        with tempfile.TemporaryDirectory() as root,patch('sys.stdin.isatty',return_value=True),patch('sys.stdout',new=io.StringIO()) as stdout,patch.object(stdout,'isatty',return_value=True),patch('getpass.getpass',return_value=FIXTURE),patch('builtins.input',return_value='SEND_PAIR003'),patch('ark_eval.manual_pair._https_once',side_effect=[(200,body()),(200,body())]) as net:
            self.assertEqual(main(['--user-local-send-pair','--plan-status-confirmed','--output',str(Path(root)/'pair')]),0)
            self.assertEqual(net.call_count,2);self.assertNotIn(FIXTURE,stdout.getvalue())


if __name__=='__main__':unittest.main()
