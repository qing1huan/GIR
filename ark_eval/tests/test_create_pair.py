import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,patch

from ark_eval.adapter import ACCOUNT_MODEL,MODEL as LEGACY,EvaluationError,PlanConfig,import_response,prepare,validate_prepared
from ark_eval.bridge import execute_candidate
from ark_eval.budget import Ledger,verify_chain
from ark_eval.create_pair import ARMS,OUTPUT_TOKENS,arm_budget,load_freeze,public_arms,load_legacy_freeze,prepared_requests,LEGACY_DIGEST
from ark_eval.manual_probe import _https_once
from headless_v01.compiler import digest
from ark_eval.js_runner import execute_candidate_source
from ark_eval.manual_pair import import_pair,main,run_pair
from ark_eval.tests.test_manual_probe import CANARY,reply


def body(content='fixture inert text',tokens=20,finish='stop',model=ACCOUNT_MODEL,reasoning=None):
    data=json.loads(reply(content=content,tokens=tokens,model=model))
    data['choices'][0]['finish_reason']=finish
    if reasoning is not None:data['choices'][0]['message']['reasoning_content']=reasoning
    return json.dumps(data).encode()


class CreatePairTests(unittest.TestCase):
    def pair(self,path,responses):
        with patch('ark_eval.manual_pair._https_once',side_effect=responses) as network:
            summary=run_pair(path,CANARY)
        return summary,network

    def test_frozen_equal_semantics_budget_and_public_only_inputs(self):
        contract=load_freeze();arms=public_arms()
        self.assertEqual(arms[0]['messages'][0],arms[1]['messages'][0])
        self.assertEqual(contract['model'],ACCOUNT_MODEL)
        self.assertEqual(contract['budget_per_arm'],{'prompt_units':24000,'output_tokens':4096,'attempts':1,'repairs':0,'tool_calls':0})
        for arm in arms:
            text=json.dumps(arm['messages'],ensure_ascii=False)
            for forbidden in ['final_tests.json','"expected"','"case_id"','CORRECT_CREATE','env.txt','ctx.write(player,']:
                self.assertNotIn(forbidden,text)
            self.assertEqual(arm['request_body']['max_tokens'],4096)
            self.assertEqual(arm['request_body']['thinking'],{'type':'disabled'})
            self.assertNotIn('reasoning_effort',arm['request_body'])
        self.assertEqual(json.loads(arms[0]['starter'])['handlers'],[])
        self.assertIn('// TODO',arms[1]['starter'])

    def test_prompt_construction_does_not_use_reference_or_final_cases(self):
        with patch('ark_eval.callback_contract.callback_pair') as old_pair,patch('ark_eval.oracle.final_cases') as cases:
            public_arms()
        old_pair.assert_not_called();cases.assert_not_called()

    def test_default_prepare_is_offline_and_contains_two_matched_requests(self):
        with tempfile.TemporaryDirectory() as root,patch('ark_eval.manual_pair._https_once') as network,patch('ark_eval.manual_pair.getpass.getpass') as key,patch('sys.stdout',new=io.StringIO()):
            path=Path(root)/'new';self.assertEqual(main(['--output',str(path)]),0)
            network.assert_not_called();key.assert_not_called()
            imported=import_pair(path)
            self.assertEqual(imported['summary']['request_attempts'],0)
            self.assertEqual([a['status'] for a in imported['summary']['arms']],['NOTRUN','NOTRUN'])

    def test_two_successful_envelopes_are_inert_unreviewed_not_gameplay_pass(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'pair';summary,network=self.pair(path,[(200,body('IR fixture')),(200,body('JS fixture'))])
            self.assertEqual(network.call_count,2)
            self.assertTrue(all(c.kwargs=={'timeout':120} for c in network.call_args_list))
            self.assertEqual(summary['reported_model_calls'],2)
            self.assertEqual([a['representation'] for a in summary['arms']],list(ARMS))
            for rep in ARMS:
                audit=json.loads((path/rep/'audit.json').read_text())
                self.assertEqual(audit['status'],'CANDIDATE_RECORDED_UNREVIEWED')
                self.assertEqual(audit['execution_status'],'BLOCKED')
                self.assertTrue(verify_chain(audit['ledger_events']))
                self.assertEqual(audit['repairs'],0);self.assertEqual(audit['tool_calls'],0)
            self.assertEqual(import_pair(path)['gameplay_tests'],'NOTRUN')

    def test_auth_halts_second_arm_and_preserves_unknown_generation(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'pair';summary,network=self.pair(path,[(401,b'')])
            self.assertEqual(network.call_count,1)
            self.assertEqual([a['status'] for a in summary['arms']],['FAIL','NOTRUN'])
            self.assertIsNone(summary['reported_model_calls'])
            self.assertEqual(summary['reported_model_calls_lower_bound'],0)
            self.assertEqual(import_pair(path)['summary']['request_attempts'],1)

    def test_truncation_retains_partial_text_and_still_attempts_other_arm_once(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'pair';summary,network=self.pair(path,[(200,body('partial inert text',4096,'length')),(200,body())])
            self.assertEqual(network.call_count,2);self.assertEqual(summary['arms'][0]['failure'],'TRUNCATED')
            self.assertEqual((path/ARMS[0]/'candidate.txt').read_text(),'partial inert text')
            self.assertEqual(import_pair(path)['candidate_execution'],'BLOCKED')

    def test_empty_reply_is_failure_but_retains_two_arm_denominator(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'pair';summary,network=self.pair(path,[(200,body('',0)),(200,body())])
            self.assertEqual(network.call_count,2);self.assertEqual(summary['arms'][0]['failure'],'EMPTY_RESPONSE')
            self.assertFalse((path/ARMS[0]/'candidate.txt').exists())
            self.assertEqual(import_pair(path)['summary']['planned_arms'],2)

    def test_model_drift_stops_pair_without_saving_drifted_candidate(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'pair';summary,network=self.pair(path,[(200,body(model=LEGACY))])
            self.assertEqual(network.call_count,1);self.assertEqual(summary['arms'][0]['failure'],'MODEL_MISMATCH')
            self.assertFalse((path/ARMS[0]/'candidate.txt').exists())
            self.assertEqual(import_pair(path)['summary']['reported_model_calls'],1)

    def test_quota_transport_and_redirect_never_retry_or_send_second_arm(self):
        for response,expected in [((429,b''),'QUOTA_OR_RATE_LIMIT'),((302,b''),'REDIRECT_REJECTED'),(OSError('fixture'),'TRANSPORT')]:
            with tempfile.TemporaryDirectory() as root:
                path=Path(root)/'pair';summary,network=self.pair(path,[response])
                self.assertEqual(network.call_count,1);self.assertEqual(summary['arms'][0]['failure'],expected)
                self.assertEqual(summary['automatic_retries'],0);self.assertFalse(summary['paid_fallback'])
                self.assertIsNone(import_pair(path)['summary']['reported_model_calls'])

    def test_usage_above_fixed_reservation_stops_and_records_charge(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'pair';summary,network=self.pair(path,[(200,body(tokens=4097))])
            self.assertEqual(network.call_count,1);self.assertEqual(summary['arms'][0]['failure'],'USAGE_EXCEEDS_RESERVATION')
            audit=json.loads((path/ARMS[0]/'audit.json').read_text())
            self.assertEqual(audit['ledger_charged']['output_tokens'],4097)
            self.assertEqual(import_pair(path)['candidate_execution'],'BLOCKED')

    def test_secret_echo_anywhere_literal_or_escaped_is_never_written(self):
        raw=body(reasoning=CANARY)
        for response in [raw,raw.replace(CANARY.encode(),''.join('\\u%04x'%ord(c) for c in CANARY).encode())]:
            with tempfile.TemporaryDirectory() as root:
                path=Path(root)/'pair';summary,network=self.pair(path,[(200,response)])
                self.assertEqual(network.call_count,1);self.assertEqual(summary['arms'][0]['failure'],'SECRET_ECHO_REJECTED')
                for file in path.rglob('*'):
                    if file.is_file():self.assertNotIn(CANARY.encode(),file.read_bytes())
                self.assertIsNone(import_pair(path)['summary']['reported_model_calls'])

    def test_quoted_backslash_fixture_key_echo_in_reasoning_never_reaches_candidate_file(self):
        credential='fixture-only-quoted"key\\not-a-secret'
        with tempfile.TemporaryDirectory() as root,patch('ark_eval.manual_pair._https_once',return_value=(200,body(reasoning=credential))) as network:
            path=Path(root)/'pair';summary=run_pair(path,credential)
            self.assertEqual(network.call_count,1)
            self.assertEqual(summary['arms'][0]['failure'],'SECRET_ECHO_REJECTED')
            self.assertFalse(any(path.rglob('candidate.txt')))
            self.assertEqual(import_pair(path)['candidate_execution'],'BLOCKED')

    def test_source_too_large_not_saved_and_other_arm_still_has_one_attempt(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'pair';summary,network=self.pair(path,[(200,body('x'*16385)),(200,body())])
            self.assertEqual(network.call_count,2);self.assertEqual(summary['arms'][0]['failure'],'SOURCE_TOO_LARGE')
            self.assertFalse((path/ARMS[0]/'candidate.txt').exists())
            self.assertEqual(import_pair(path)['summary']['request_attempts'],2)

    def test_existing_output_rejected_before_key_prompt_or_transport(self):
        with tempfile.TemporaryDirectory() as root,patch('ark_eval.manual_pair.getpass.getpass') as key,patch('ark_eval.manual_pair._https_once') as network,patch('sys.stderr',new=io.StringIO()):
            with self.assertRaises(SystemExit):main(['--user-local-send-pair','--plan-status-confirmed','--output',root])
            key.assert_not_called();network.assert_not_called()

    def test_permission_terminal_and_contract_drift_gates_precede_secret_prompt(self):
        with tempfile.TemporaryDirectory() as root,patch('sys.stdout',new=io.StringIO()),patch('sys.stdin.isatty',return_value=False),patch('ark_eval.manual_pair.getpass.getpass') as key:
            argv=['--user-local-send-pair','--plan-status-confirmed','--output',str(Path(root)/'unused')]
            self.assertEqual(main(argv),2)
            with patch('ark_eval.manual_pair.load_freeze',side_effect=EvaluationError('PAIR_CONTRACT_DRIFT')):
                self.assertEqual(main(argv),2)
            key.assert_not_called()

    def test_private_human_mock_enters_fixture_key_once_for_whole_pair(self):
        with tempfile.TemporaryDirectory() as root,patch('sys.stdin.isatty',return_value=True),patch('sys.stdout.isatty',return_value=True),patch('ark_eval.manual_pair.getpass.getpass',return_value=CANARY) as key,patch('ark_eval.manual_pair._https_once',side_effect=[(200,body()),(200,body())]) as network,patch('builtins.print'):
            path=Path(root)/'pair'
            self.assertEqual(main(['--user-local-send-pair','--plan-status-confirmed','--output',str(path)]),0)
            self.assertEqual(key.call_count,1);self.assertEqual(network.call_count,2)

    def test_contract_drift_between_arms_records_notrun_without_second_request(self):
        contract=load_freeze()
        with tempfile.TemporaryDirectory() as root:
            with patch('ark_eval.manual_pair.load_freeze',side_effect=[contract,contract,EvaluationError('PAIR_CONTRACT_DRIFT')]):
                path=Path(root)/'pair';summary,network=self.pair(path,[(200,body())])
                self.assertEqual(network.call_count,1);self.assertEqual(summary['arms'][1]['status'],'NOTRUN')
            self.assertEqual(import_pair(path)['summary']['reported_model_calls'],1)

    def test_candidate_text_is_never_compiled_executed_or_added_to_catalog(self):
        text="process.exit(19); // fixture only, never executed"
        with tempfile.TemporaryDirectory() as root,patch('ark_eval.bridge.execute_candidate') as ir_exec,patch('ark_eval.js_runner.execute_candidate_source') as js_exec:
            path=Path(root)/'pair';self.pair(path,[(200,body(text)),(200,body(text))])
            import_pair(path)
            ir_exec.assert_not_called();js_exec.assert_not_called()
            self.assertEqual({p.suffix for p in path.rglob('candidate*')},{'.txt'})
        for gate in [execute_candidate,execute_candidate_source]:
            with self.assertRaises(EvaluationError):gate(text)

    def test_closed_import_rejects_usage_injection_execution_flag_and_candidate_path(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'pair';self.pair(path,[(200,body()),(200,body())])
            file=path/ARMS[0]/'audit.json';original=json.loads(file.read_text())
            for field,value in [('usage',{'private':CANARY}),('execution_status','ALLOWED'),('candidate_file','../env.txt'),
                                ('ledger_events',['invalid closed event']),('request_attempts',True),('generated_source_executions',False)]:
                mutated=copy.deepcopy(original);mutated[field]=value;file.write_text(json.dumps(mutated))
                with self.assertRaises(EvaluationError):import_pair(path)
            file.write_text(json.dumps(original));(path/ARMS[0]/'candidate.txt').write_text('tampered')
            with self.assertRaises(EvaluationError):import_pair(path)

    def test_exact_model_ledger_reuses_reservation_and_rejects_cross_model_request(self):
        ledger=Ledger(arm_budget(),requested_model=ACCOUNT_MODEL)
        with self.assertRaises(EvaluationError):ledger.reserve(prepare([{'role':'user','content':'fixture'}],4096))
        request=prepare([{'role':'user','content':'fixture'}],4096,PlanConfig(model=ACCOUNT_MODEL))
        call=ledger.reserve(request);ledger.finish(call,response=import_response(body().decode(),requested_model=ACCOUNT_MODEL))
        self.assertEqual(ledger.export()['events'][-1]['status'],'RESPONSE_IMPORTED')

    def test_incomplete_write_ahead_bundle_cannot_be_imported_as_completed_or_retried(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'pair';self.pair(path,[(200,body()),(200,body())])
            (path/ARMS[0]/'audit.json').unlink()
            with self.assertRaises(EvaluationError):import_pair(path)
            self.assertTrue((path/ARMS[0]/'reservation.json').is_file())
            with self.assertRaises(FileExistsError):run_pair(path,CANARY)

    def test_v2_changes_request_and_protocol_digests_but_keeps_public_inputs(self):
        old=load_legacy_freeze();new=load_freeze()
        self.assertEqual(digest(old),LEGACY_DIGEST)
        self.assertNotEqual(digest(new),digest(old))
        self.assertEqual(new['schema'],'gir-create-pair-freeze@2')
        for before,after in zip(old['public_arms'],new['public_arms']):
            self.assertEqual(before['messages'],after['messages'])
            self.assertEqual(before['starter'],after['starter'])
            expected=copy.deepcopy(before['request_body']);expected['thinking']={'type':'disabled'}
            self.assertEqual(after['request_body'],expected)
            self.assertNotEqual(before['request_digest'],after['request_digest'])

    def test_fixed_disabled_setting_reaches_native_http_body_mock(self):
        conn=MagicMock();conn.getresponse.return_value.status=400
        for rep,request in prepared_requests(load_freeze()):
            with patch('ark_eval.manual_probe.http.client.HTTPSConnection',return_value=conn),patch('ark_eval.manual_probe.ssl.create_default_context',return_value='fixture'):
                _https_once(request,CANARY,timeout=120)
            sent=json.loads(conn.request.call_args.kwargs['body'])
            self.assertEqual(sent,request.body)
            self.assertEqual(sent['thinking'],{'type':'disabled'})
            self.assertEqual(sent['max_tokens'],4096)
            self.assertEqual(sent['model'],ACCOUNT_MODEL)

    def test_unsupported_parameter_http400_or422_stops_without_fallback(self):
        for status in (400,422):
            with tempfile.TemporaryDirectory() as root:
                path=Path(root)/'pair';summary,network=self.pair(path,[(status,b'{"error":{"code":"InvalidParameter"}}')])
                self.assertEqual(network.call_count,1)
                self.assertEqual(summary['arms'][0]['failure'],'HTTP_ERROR')
                self.assertEqual(summary['arms'][1]['status'],'NOTRUN')
                self.assertEqual(network.call_args.args[0].body['thinking'],{'type':'disabled'})
                self.assertEqual(summary['automatic_retries'],0)
                self.assertFalse(summary['automatic_budget_growth']);self.assertFalse(summary['paid_fallback'])
                import_pair(path)

    def test_nonempty_or_wrong_type_reasoning_halts_pair_and_never_saves_source(self):
        for reasoning in ('fixture-only private reasoning',{'unexpected':'fixture'},123):
            with tempfile.TemporaryDirectory() as root:
                path=Path(root)/'pair';summary,network=self.pair(path,[(200,body(reasoning=reasoning))])
                self.assertEqual(network.call_count,1)
                self.assertEqual(summary['reported_model_calls'],1)
                self.assertEqual(summary['arms'][0]['failure'],'THINKING_MISMATCH')
                self.assertEqual(summary['arms'][1]['status'],'NOTRUN')
                self.assertFalse(any(path.rglob('candidate.txt')))
                import_pair(path)

    def test_empty_null_or_absent_reasoning_is_permitted_without_enforcement_claim(self):
        empty=body(reasoning='');absent=body()
        null=json.loads(absent);null['choices'][0]['message']['reasoning_content']=None
        for response in (empty,absent,json.dumps(null).encode()):
            with tempfile.TemporaryDirectory() as root:
                path=Path(root)/'pair';summary,network=self.pair(path,[(200,response),(200,body())])
                self.assertEqual(network.call_count,2)
                self.assertTrue(all(a['status']=='CANDIDATE_RECORDED_UNREVIEWED' for a in summary['arms']))
                import_pair(path)

    def test_adapter_rejects_parameter_tampering_and_arbitrary_thinking(self):
        for thinking in ({'type':'enabled'},{'type':'disabled','extra':1},True,'disabled'):
            with self.assertRaises(EvaluationError):prepare([{'role':'user','content':'fixture'}],4096,thinking=thinking)
        request=prepared_requests(load_freeze())[0][1]
        self.assertTrue(validate_prepared(request))
        from dataclasses import replace
        mutated=request.body;mutated['thinking']['type']='enabled'
        with self.assertRaises(EvaluationError):validate_prepared(replace(request,body_json=json.dumps(mutated)))

    def test_historical_v1_roundtrip_preserves_reasoning_and_truncation(self):
        contract=load_legacy_freeze()
        from ark_eval.manual_pair import prepare_directory,attempt,summarize,write_json
        with tempfile.TemporaryDirectory() as root:
            path=prepare_directory(Path(root)/'v1',contract);records=[]
            for rep,request in prepared_requests(contract):
                self.assertNotIn('thinking',request.body)
                with patch('ark_eval.manual_pair._https_once',return_value=(200,body('',4096,'length',reasoning='historical fixture'))):
                    audit,candidate=attempt(rep,request,CANARY)
                self.assertIsNone(candidate)
                write_json(path/rep/'audit.json',audit);records.append(audit)
            write_json(path/'summary.json',summarize(records,contract))
            saved=import_pair(path)['summary']
            self.assertEqual(saved['protocol_digest'],LEGACY_DIGEST)
            self.assertEqual([a['failure'] for a in saved['arms']],['TRUNCATED','TRUNCATED'])

    def test_import_rejects_success_reclassified_with_nonempty_reasoning(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'v2';self.pair(path,[(200,body()),(200,body())])
            file=path/ARMS[0]/'audit.json';audit=json.loads(file.read_text())
            audit['response_observations'].update(reasoning_content_present=True,reasoning_content_type='string',reasoning_content_chars=1)
            file.write_text(json.dumps(audit))
            with self.assertRaises(EvaluationError):import_pair(path)
