"""User-entry verification using public non-secret fixtures and mock HTTP only."""
import copy
import getpass
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ark_eval.adapter import EvaluationError
from ark_eval.budget import verify_chain
from ark_eval import manual_repair_pair003 as entry
from ark_eval.tests.test_create_pair import body

FIXTURE='public-fixture-only-not-a-key'


def response(content='inert repair fixture',tin=100,tout=20,**kwargs):
    data=json.loads(body(content,tokens=tout,**kwargs))
    data['usage']={'prompt_tokens':tin,'completion_tokens':tout,'total_tokens':tin+tout}
    return json.dumps(data).encode()


class ManualRepairPair003Tests(unittest.TestCase):
    def test_fixed_two_requests_independent_usage_and_inert_candidates(self):
        contract,requests=entry.load_repair_requests()
        with tempfile.TemporaryDirectory() as root,patch.object(entry,'_https_once',side_effect=[(200,response('IR inert',tin=30001)),(200,response('JS inert',tin=123))]) as net:
            output=Path(root)/'repair';summary=entry.run_repair(output,FIXTURE)
            self.assertEqual(net.call_count,2);self.assertEqual(summary['repairs'],2)
            self.assertEqual(summary['reported_model_calls'],2);self.assertFalse(summary['input_units_are_tokens'])
            self.assertEqual([a['usage']['prompt_tokens'] for a in summary['arms']],[30001,123])
            self.assertEqual(summary['state'],'RESPONSES_SAVED_UNREVIEWED')
            self.assertEqual(json.loads((output/'protocol.json').read_text(encoding='utf-8')),contract)
            for call,(rep,request) in zip(net.call_args_list,requests):
                self.assertEqual(call.args[0],request);self.assertEqual(call.kwargs,{'timeout':120})
                audit=json.loads((output/rep/'audit.json').read_text(encoding='utf-8'))
                assessment=json.loads((output/rep/'contract-assessment.json').read_text(encoding='utf-8'))
                self.assertTrue(verify_chain(audit['ledger_events']));self.assertEqual(audit['repairs'],1)
                self.assertEqual(audit['execution_status'],'BLOCKED');self.assertEqual(audit['generated_source_executions'],0)
                self.assertEqual(assessment['contract_status'],'NOT_CHECKED');self.assertEqual(assessment['gameplay_status'],'NOTRUN')
                self.assertEqual(audit['prompt_byte_units_ceiling'],24000)
            for p in output.rglob('*'):
                if p.is_file():self.assertNotIn(FIXTURE.encode(),p.read_bytes())

    def test_default_preparation_no_consent_key_or_network(self):
        with tempfile.TemporaryDirectory() as root,patch.object(entry,'_https_once') as net,patch.object(entry,'read_local_key') as keyfile,patch('getpass.getpass') as hidden,patch('builtins.input') as consent,patch('sys.stdout',new=io.StringIO()):
            output=Path(root)/'prepared';self.assertEqual(entry.main(['--output',str(output)]),0)
            for mock in (net,keyfile,hidden,consent):mock.assert_not_called()
            summary=json.loads((output/'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['request_attempts'],0);self.assertEqual(summary['repairs'],0)
            self.assertEqual(summary['state'],'PREPARED_NOT_SENT')

    def test_every_failure_stops_second_without_retry_or_reservation(self):
        invalid=json.loads(response());del invalid['usage']
        fixtures=[((401,b''),'AUTH'),((403,b''),'FORBIDDEN'),((429,b''),'QUOTA_OR_RATE_LIMIT'),
            ((302,b''),'REDIRECT_REJECTED'),((500,b''),'HTTP_ERROR'),(OSError('public fixture'),'TRANSPORT'),
            ((200,response(tout=4097)),'OUTPUT_TOKEN_LIMIT'),((200,response(finish='length',tout=4096)),'TRUNCATED'),
            ((200,response('')),'EMPTY_RESPONSE'),((200,response(model='deepseek-v4.1-flash')),'MODEL_MISMATCH'),
            ((200,response(reasoning='unexpected reasoning')),'THINKING_MISMATCH'),
            ((200,json.dumps(invalid).encode()),'INVALID_RESPONSE')]
        for answer,failure in fixtures:
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as root,patch.object(entry,'_https_once',side_effect=[answer]) as net:
                output=Path(root)/'pair';summary=entry.run_repair(output,FIXTURE)
                self.assertEqual(net.call_count,1);self.assertEqual(summary['request_attempts'],1)
                self.assertEqual(summary['arms'][0]['failure'],failure);self.assertEqual(summary['arms'][1]['status'],'NOTRUN')
                self.assertEqual(summary['state'],'STOPPED_ON_FAILURE');self.assertFalse((output/'js_callback/reservation.json').exists())
                self.assertEqual(summary['automatic_retries'],0);self.assertFalse(summary['paid_fallback']);self.assertEqual(summary['reset_cards_used'],0)
                if failure=='INVALID_RESPONSE':
                    audit=json.loads((output/'gameir_text/audit.json').read_text())
                    self.assertTrue(audit['reservation_retained']);self.assertIsNone(audit['usage'])

    def test_secret_echo_is_not_saved_and_stops_second(self):
        with tempfile.TemporaryDirectory() as root,patch.object(entry,'_https_once',return_value=(200,response(reasoning=FIXTURE))) as net:
            output=Path(root)/'pair';summary=entry.run_repair(output,FIXTURE)
            self.assertEqual(net.call_count,1);self.assertEqual(summary['arms'][0]['failure'],'SECRET_ECHO_REJECTED')
            self.assertFalse((output/'gameir_text/candidate.txt').exists())
            for p in output.rglob('*'):
                if p.is_file():self.assertNotIn(FIXTURE.encode(),p.read_bytes())

    def test_existing_output_cannot_be_overwritten_or_sent(self):
        with tempfile.TemporaryDirectory() as root,patch.object(entry,'_https_once') as net:
            marker=Path(root)/'original.txt';marker.write_text('preserve')
            with self.assertRaises(FileExistsError):entry.run_repair(Path(root),FIXTURE)
            self.assertEqual(marker.read_text(),'preserve');net.assert_not_called()

    def test_freeze_and_source_drift_rejected_before_send(self):
        contract,_=entry.load_repair_requests();bad=copy.deepcopy(contract);bad['proposal']['arms'][0]['request_body']['max_tokens']=4095
        with patch.object(entry,'bounded_json',return_value=bad):
            with self.assertRaisesRegex(EvaluationError,'DIGEST_MISMATCH'):entry.load_repair_requests()
        with patch.object(entry,'source_bindings',return_value={}):
            with self.assertRaisesRegex(EvaluationError,'BINDING_DRIFT'):entry.load_repair_requests()

    def test_mid_pair_drift_stops_second(self):
        original=entry.load_repair_requests()
        with tempfile.TemporaryDirectory() as root,patch.object(entry,'load_repair_requests',side_effect=[original,original,EvaluationError('FIXTURE_DRIFT')]),patch.object(entry,'_https_once',return_value=(200,response())) as net:
            summary=entry.run_repair(Path(root)/'pair',FIXTURE)
            self.assertEqual(net.call_count,1);self.assertEqual(summary['arms'][1]['status'],'NOTRUN')
            self.assertEqual(summary['state'],'STOPPED_ON_FAILURE')

    def test_key_file_requires_send_confirmation_and_private_terminal(self):
        for flags in ([],['--user-local-send-repair'],['--user-local-send-repair','--plan-status-confirmed']):
            with tempfile.TemporaryDirectory() as root,patch.object(entry,'read_local_key') as keyfile,patch.object(entry,'_https_once') as net,patch('sys.stdout',new=io.StringIO()):
                output=Path(root)/'pair'
                self.assertEqual(entry.main([*flags,'--key-file','D:/public-fixture-path-only/key.txt','--output',str(output)]),2)
                keyfile.assert_not_called();net.assert_not_called();self.assertFalse(output.exists())

    def test_cancel_and_hidden_input_fallback_never_read_file_or_send(self):
        for consent in ('cancel',entry.CONFIRMATION):
            with tempfile.TemporaryDirectory() as root,patch('sys.stdin.isatty',return_value=True),patch('sys.stdout',new=io.StringIO()) as stdout,patch.object(stdout,'isatty',return_value=True),patch('builtins.input',return_value=consent),patch('getpass.getpass',side_effect=getpass.GetPassWarning('public fixture')),patch.object(entry,'read_local_key') as keyfile,patch.object(entry,'_https_once') as net:
                self.assertEqual(entry.main(['--user-local-send-repair','--plan-status-confirmed','--output',str(Path(root)/'pair')]),2)
                net.assert_not_called();keyfile.assert_not_called()

    def test_explicit_confirmation_precedes_mock_key_file_read_and_two_sends(self):
        events=[]
        def consent(_):events.append('confirm');return entry.CONFIRMATION
        def keyfile(*args,**kwargs):events.append('read_file');return FIXTURE
        def send(*args,**kwargs):events.append('send');return 200,response()
        with tempfile.TemporaryDirectory() as root,patch('sys.stdin.isatty',return_value=True),patch('sys.stdout',new=io.StringIO()) as stdout,patch.object(stdout,'isatty',return_value=True),patch('builtins.input',side_effect=consent),patch.object(entry,'read_local_key',side_effect=keyfile),patch.object(entry,'_https_once',side_effect=send):
            self.assertEqual(entry.main(['--user-local-send-repair','--plan-status-confirmed','--key-file','D:/public-fixture-path-only/key.txt','--output',str(Path(root)/'pair')]),0)
            self.assertEqual(events,['confirm','read_file','send','send']);self.assertNotIn(FIXTURE,stdout.getvalue())


if __name__=='__main__':unittest.main()
