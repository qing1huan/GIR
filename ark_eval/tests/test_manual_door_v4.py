"""Four-session user entry: public fixtures, mocked transport, no real keys."""
import copy
import getpass
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ark_eval import manual_door_v4 as entry
from ark_eval.adapter import EvaluationError
from ark_eval.budget import verify_chain
from ark_eval.tests.test_create_pair import body
from headless_v01.compiler import digest

FIXTURE='public-fixture-only-not-a-key'


def response(content='inert fixture',tin=100,tout=20,**kwargs):
    data=json.loads(body(content,tokens=tout,**kwargs))
    data['usage']={'prompt_tokens':tin,'completion_tokens':tout,'total_tokens':tin+tout}
    return json.dumps(data).encode()


class ManualDoorV4Tests(unittest.TestCase):
    def test_frozen_order_exact_requests_and_inert_outputs(self):
        contract,requests,bound=entry.load_requests()
        self.assertEqual(digest(contract),entry.PROTOCOL_DIGEST)
        self.assertEqual(tuple((t,r) for t,r,_ in requests),entry.ORDER)
        self.assertNotEqual(tuple((a['task_id'],a['representation']) for a in contract['public_arms']),entry.ORDER)
        with tempfile.TemporaryDirectory() as root,patch.object(entry,'_https_once',side_effect=[(200,response('inert '+str(i),tin=30001+i)) for i in range(4)]) as net:
            output=Path(root)/'batch';summary=entry.run_batch(output,FIXTURE)
            self.assertEqual(net.call_count,4);self.assertEqual(summary['request_attempts'],4)
            self.assertEqual(summary['state'],'RESPONSES_SAVED_UNREVIEWED')
            self.assertEqual(summary['maximum_batch_output_tokens'],16384)
            self.assertFalse(summary['input_units_are_tokens']);self.assertEqual(summary['repairs'],0)
            self.assertEqual([a['usage']['prompt_tokens'] for a in summary['arms']],[30001,30002,30003,30004])
            self.assertEqual(summary['generated_source_executions'],0)
            self.assertEqual(sum(v['NOTRUN'] for task in summary['gameplay'].values() for arm in task.values() for v in arm.values()),136)
            for i,(call,(task,rep,q)) in enumerate(zip(net.call_args_list,requests)):
                self.assertEqual(call.args[0],q);self.assertEqual(call.kwargs,{'timeout':120})
                self.assertEqual(len(q.body['messages']),2);self.assertEqual(q.body['thinking'],{'type':'disabled'})
                folder=output/task/rep
                self.assertEqual(json.loads((folder/'request.json').read_text()),q.body)
                self.assertEqual((folder/'candidate.txt').read_bytes(),('inert '+str(i)).encode())
                audit=json.loads((folder/'audit.json').read_text())
                self.assertTrue(verify_chain(audit['ledger_events']));self.assertEqual(audit['repairs'],0)
                self.assertEqual(audit['execution_status'],'BLOCKED');self.assertFalse(audit['input_token_upper_bound_claimed'])
                report=json.loads((folder/'contract-assessment.json').read_text(encoding='utf-8'))
                self.assertEqual(report['contract_status'],'NOT_CHECKED');self.assertEqual(report['gameplay_status'],'NOTRUN')
            self.assert_no_fixture(output)

    def assert_no_fixture(self,output):
        for p in output.rglob('*'):
            if p.is_file():self.assertNotIn(FIXTURE.encode(),p.read_bytes())

    def test_default_prepare_never_reads_key_or_sends(self):
        with tempfile.TemporaryDirectory() as root,patch.object(entry,'_https_once') as net,patch.object(entry,'read_local_key') as key,patch('getpass.getpass') as hidden,patch('builtins.input') as consent,patch('sys.stdout',new=io.StringIO()):
            output=Path(root)/'prepared';self.assertEqual(entry.main(['--output',str(output)]),0)
            for m in (net,key,hidden,consent):m.assert_not_called()
            s=json.loads((output/'summary.json').read_text())
            self.assertEqual(s['request_attempts'],0);self.assertEqual(s['state'],'PREPARED_NOT_SENT')
            self.assertTrue(all(a['status']=='NOTRUN' for a in s['arms']))

    def test_failure_in_each_position_stops_all_following(self):
        for position in range(4):
            with self.subTest(position=position),tempfile.TemporaryDirectory() as root,patch.object(entry,'_https_once',side_effect=[(200,response())]*position+[(429,b'')]) as net:
                output=Path(root)/'batch';s=entry.run_batch(output,FIXTURE)
                self.assertEqual(net.call_count,position+1);self.assertEqual(s['state'],'STOPPED_ON_FAILURE')
                self.assertEqual(s['arms'][position]['failure'],'QUOTA_OR_RATE_LIMIT')
                for i,(task,rep) in enumerate(entry.ORDER):
                    self.assertEqual((output/task/rep/'reservation.json').exists(),i<=position)
                    self.assertTrue((output/task/rep/'notrun-initial.json').exists())
                    if i>position:self.assertEqual(s['arms'][i]['status'],'NOTRUN')

    def test_failure_classes_never_retry_or_fallback(self):
        invalid=json.loads(response());del invalid['usage']
        fixtures=[((401,b''),'AUTH'),((403,b''),'FORBIDDEN'),((302,b''),'REDIRECT_REJECTED'),
            ((500,b''),'HTTP_ERROR'),(OSError(FIXTURE),'TRANSPORT'),(KeyboardInterrupt(),'CANCELLED'),
            ((200,response(tout=4097)),'OUTPUT_TOKEN_LIMIT'),((200,response(finish='length')),'TRUNCATED'),
            ((200,response('')),'EMPTY_RESPONSE'),((200,response(model='wrong-model')),'MODEL_MISMATCH'),
            ((200,response(reasoning='unexpected')),'THINKING_MISMATCH'),
            ((200,response(reasoning={})), 'THINKING_MISMATCH'),
            ((200,response('x'*16385)),'SOURCE_TOO_LARGE'),((200,b'x'*65537),'RESPONSE_TOO_LARGE'),
            ((200,json.dumps(invalid).encode()),'INVALID_RESPONSE')]
        for answer,failure in fixtures:
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as root,patch.object(entry,'_https_once',side_effect=[answer]) as net:
                output=Path(root)/'batch';s=entry.run_batch(output,FIXTURE)
                self.assertEqual(net.call_count,1);self.assertEqual(s['arms'][0]['failure'],failure)
                self.assertTrue(all(a['status']=='NOTRUN' for a in s['arms'][1:]))
                self.assertEqual(s['automatic_retries'],0);self.assertFalse(s['paid_fallback']);self.assertEqual(s['reset_cards_used'],0)
                self.assert_no_fixture(output)
                audit=json.loads((output/entry.ORDER[0][0]/entry.ORDER[0][1]/'audit.json').read_text())
                self.assertTrue(audit['reservation_retained'])
                if failure in ('TRANSPORT','INVALID_RESPONSE'):self.assertEqual(s['unknown_model_call_outcomes'],1)

    def test_plain_and_json_escaped_secret_echo_never_saved(self):
        escaped=''.join('\\u%04x'%ord(c) for c in FIXTURE)
        data=response().decode();encoded=data[:-1]+', "'+escaped+'": "fixture"}'
        for payload in (response(FIXTURE),response(reasoning=FIXTURE),encoded.encode()):
            with tempfile.TemporaryDirectory() as root,patch.object(entry,'_https_once',return_value=(200,payload)) as net:
                output=Path(root)/'batch';s=entry.run_batch(output,FIXTURE)
                self.assertEqual(net.call_count,1);self.assertEqual(s['arms'][0]['failure'],'SECRET_ECHO_REJECTED')
                self.assertFalse((output/entry.ORDER[0][0]/entry.ORDER[0][1]/'candidate.txt').exists())
                self.assert_no_fixture(output)

    def test_existing_directory_preserved_without_send(self):
        with tempfile.TemporaryDirectory() as root,patch.object(entry,'_https_once') as net:
            p=Path(root)/'marker.txt';p.write_text('preserve')
            with self.assertRaises(FileExistsError):entry.run_batch(Path(root),FIXTURE)
            self.assertEqual(p.read_text(),'preserve');net.assert_not_called()

    def test_protocol_and_entry_drift_rejected(self):
        original=entry.load_requests();bad=copy.deepcopy(original[0]);bad['model']='changed'
        with patch.object(entry,'load_freeze',return_value=bad):
            with self.assertRaisesRegex(EvaluationError,'FIXED_PROTOCOL_DIGEST'):entry.load_requests()
        with patch.object(entry,'entry_bindings',return_value={}):
            with self.assertRaisesRegex(EvaluationError,'ENTRY_BINDING_DRIFT'):entry.load_requests()

    def test_mid_batch_drift_stops_remaining(self):
        original=entry.load_requests()
        with tempfile.TemporaryDirectory() as root,patch.object(entry,'load_requests',side_effect=[original,original,EvaluationError('FIXTURE_DRIFT')]),patch.object(entry,'_https_once',return_value=(200,response())) as net:
            s=entry.run_batch(Path(root)/'batch',FIXTURE)
            self.assertEqual(net.call_count,1);self.assertTrue(all(a['status']=='NOTRUN' for a in s['arms'][1:]))
            self.assertEqual(s['request_attempts'],1);self.assertEqual(s['state'],'STOPPED_ON_FAILURE')

    def test_status_tty_and_consent_required_before_key(self):
        cases=[([],False,'SEND_V4'),(['--user-local-send-v4'],True,'SEND_V4'),
            (['--user-local-send-v4','--plan-status-confirmed'],False,'SEND_V4'),
            (['--user-local-send-v4','--plan-status-confirmed'],True,'cancel')]
        for flags,tty,consent in cases:
            with tempfile.TemporaryDirectory() as root,patch('sys.stdin.isatty',return_value=tty),patch('sys.stdout',new=io.StringIO()) as stdout,patch.object(stdout,'isatty',return_value=tty),patch('builtins.input',return_value=consent),patch.object(entry,'read_local_key') as key,patch.object(entry,'_https_once') as net:
                output=Path(root)/'batch'
                self.assertEqual(entry.main([*flags,'--key-file','D:/public-fixture-path-only/key.txt','--output',str(output)]),2)
                key.assert_not_called();net.assert_not_called();self.assertFalse(output.exists())

    def test_explicit_confirm_before_mock_key_read_and_four_mock_calls(self):
        seen=[]
        def consent(prompt):seen.append('consent');return entry.CONFIRMATION
        def read(path,project_root):self.assertEqual(seen,['consent']);seen.append('key');return FIXTURE
        with tempfile.TemporaryDirectory() as root,patch('sys.stdin.isatty',return_value=True),patch('sys.stdout',new=io.StringIO()) as stdout,patch.object(stdout,'isatty',return_value=True),patch('builtins.input',side_effect=consent),patch.object(entry,'read_local_key',side_effect=read) as key,patch.object(entry,'_https_once',return_value=(200,response())) as net:
            self.assertEqual(entry.main(['--user-local-send-v4','--plan-status-confirmed','--key-file','D:/public-fixture-path-only/key.txt','--output',str(Path(root)/'batch')]),0)
            key.assert_called_once();self.assertEqual(net.call_count,4);self.assertNotIn(FIXTURE,stdout.getvalue())

    def test_hidden_input_failure_and_cancel_no_network(self):
        for answer in ('cancel',entry.CONFIRMATION):
            with tempfile.TemporaryDirectory() as root,patch('sys.stdin.isatty',return_value=True),patch('sys.stdout',new=io.StringIO()) as stdout,patch.object(stdout,'isatty',return_value=True),patch('builtins.input',return_value=answer),patch('getpass.getpass',side_effect=getpass.GetPassWarning(FIXTURE)),patch.object(entry,'_https_once') as net:
                self.assertEqual(entry.main(['--user-local-send-v4','--plan-status-confirmed','--output',str(Path(root)/'batch')]),2)
                net.assert_not_called();self.assertNotIn(FIXTURE,stdout.getvalue())


if __name__=='__main__':unittest.main()
