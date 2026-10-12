import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ark_eval.adapter import EvaluationError
from ark_eval.manual_probe import (AUDIT_KEYS_V2,MODEL,import_bundle,main,outcome_axes,
                                   probe_request,save_bundle,send_once)
from ark_eval.probe_diagnose import diagnose_bundle
from ark_eval.tests.test_manual_probe import CANARY,reply


def response(content='',finish='length',tokens=128,reasoning='fixture reasoning, never saved'):
    data=json.loads(reply(content=content,tokens=tokens))
    data['choices'][0]['finish_reason']=finish
    data['choices'][0]['message']['reasoning_content']=reasoning
    return json.dumps(data).encode()


class ProbeBudgetTests(unittest.TestCase):
    def test_default_128_changes_only_max_tokens_from_historical_request(self):
        old=probe_request(16).body;new=probe_request().body
        self.assertEqual(new['max_tokens'],128)
        old.pop('max_tokens');new.pop('max_tokens')
        self.assertEqual(new,old);self.assertEqual(new['model'],MODEL)
        self.assertNotIn('thinking',new);self.assertNotIn('reasoning_effort',new)

    def test_direct_caps_outside_bound_fail_before_mock_transport(self):
        with patch('ark_eval.manual_probe._https_once') as network:
            for cap in [0,-1,257,True,128.0,'128']:
                with self.assertRaises(EvaluationError):send_once(CANARY,cap)
            network.assert_not_called()

    def test_cli_invalid_caps_fail_before_key_or_network(self):
        with tempfile.TemporaryDirectory() as root,patch('ark_eval.manual_probe.getpass.getpass') as key,patch('ark_eval.manual_probe._https_once') as network,patch('sys.stderr',new=io.StringIO()):
            for cap in ['0','-1','257','not-an-integer']:
                with self.assertRaises(SystemExit):main(['--user-local-send','--plan-status-confirmed','--max-output-tokens',cap,'--output',str(Path(root)/'unused')])
            key.assert_not_called();network.assert_not_called()

    def test_offline_explicit_cap_is_exported_without_network(self):
        with tempfile.TemporaryDirectory() as root,patch('ark_eval.manual_probe.getpass.getpass') as key,patch('ark_eval.manual_probe._https_once') as network,patch('sys.stdout',new=io.StringIO()):
            for cap in [1,128,256]:
                path=Path(root)/str(cap)
                self.assertEqual(main(['--max-output-tokens',str(cap),'--output',str(path)]),0)
                self.assertEqual(json.loads((path/'request.json').read_text())['max_tokens'],cap)
            key.assert_not_called();network.assert_not_called()

    def test_empty_length_has_separate_HTTP200_and_TRUNCATED_with_safe_metadata(self):
        with patch('ark_eval.manual_probe._https_once',return_value=(200,response())) as network:
            audit,normalized=send_once(CANARY)
        self.assertEqual(network.call_count,1);self.assertIsNone(normalized)
        self.assertEqual(audit['failure'],'TRUNCATED')
        self.assertEqual(audit['finish_reason'],'length');self.assertEqual(audit['usage']['completion_tokens'],128)
        self.assertEqual(outcome_axes(audit['http_status'],audit['status'],audit['failure']),{'transport_status':'HTTP200','application_status':'TRUNCATED'})
        self.assertEqual(audit['response_observations'],{'content_chars':0,'reasoning_content_present':True,'reasoning_content_type':'string','reasoning_content_chars':len('fixture reasoning, never saved')})
        self.assertNotIn('fixture reasoning, never saved',json.dumps(audit))
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'bundle';save_bundle(path,audit,normalized)
            imported=import_bundle(path);diagnosis=diagnose_bundle(path)
            self.assertEqual(imported['application_status'],'TRUNCATED')
            self.assertEqual(diagnosis['reasoning_allocation'],'UNKNOWN_TOKEN_ATTRIBUTION')
            self.assertEqual(diagnosis['exact_OK_check'],'FAIL')

    def test_empty_stop_is_application_empty_response_and_length_OK_is_truncated(self):
        for body,expected in [(response(finish='stop',tokens=0),'EMPTY_RESPONSE'),(response(content='OK'),'TRUNCATED')]:
            with patch('ark_eval.manual_probe._https_once',return_value=(200,body)):
                audit,normalized=send_once(CANARY)
            self.assertEqual(outcome_axes(200,audit['status'],audit['failure'])['application_status'],expected)
            self.assertEqual(audit['status'],'FAIL');self.assertIsNone(normalized)

    def test_complete_OK_roundtrip_saves_counts_but_no_reasoning_text(self):
        with patch('ark_eval.manual_probe._https_once',return_value=(200,response(content='OK',finish='stop',tokens=8))):
            audit,normalized=send_once(CANARY,128)
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'bundle';save_bundle(path,audit,normalized)
            text=(path/'audit.json').read_text()+(path/'response.json').read_text()
            self.assertNotIn('fixture reasoning, never saved',text)
            imported=import_bundle(path)
            self.assertEqual(imported['application_status'],'EXPECTED_REPLY')
            self.assertEqual(diagnose_bundle(path)['exact_OK_check'],'PASS')

    def test_secret_echo_anywhere_in_success_body_drops_all_response_metadata(self):
        raw=response(content='OK',finish='stop',tokens=8,reasoning=CANARY)
        for body in [raw,raw.replace(CANARY.encode(),''.join('\\u%04x'%ord(c) for c in CANARY).encode())]:
            with patch('ark_eval.manual_probe._https_once',return_value=(200,body)):
                audit,normalized=send_once(CANARY)
            self.assertEqual(audit['failure'],'SECRET_ECHO_REJECTED')
            self.assertIsNone(audit['response_observations']);self.assertIsNone(normalized)
            self.assertIsNone(audit['reported_real_model_calls'])
            self.assertNotIn(CANARY,json.dumps(audit))

    def test_quoted_backslash_fixture_key_echo_in_decoded_reasoning_is_rejected(self):
        credential='fixture-only-quoted"key\\not-a-secret'
        with patch('ark_eval.manual_probe._https_once',return_value=(200,response(content='OK',finish='stop',tokens=8,reasoning=credential))):
            audit,normalized=send_once(credential)
        self.assertEqual(audit['failure'],'SECRET_ECHO_REJECTED')
        self.assertIsNone(audit['response_observations']);self.assertIsNone(normalized)

    def test_metadata_types_null_absent_and_other_are_fixed_labels_only(self):
        for reasoning,kind,length in [(None,'null',None),({'private':'never exported'},'other',None),('', 'string',0)]:
            with patch('ark_eval.manual_probe._https_once',return_value=(200,response(reasoning=reasoning))):
                audit,_=send_once(CANARY)
            self.assertEqual(audit['response_observations']['reasoning_content_type'],kind)
            self.assertEqual(audit['response_observations']['reasoning_content_chars'],length)
            self.assertNotIn('never exported',json.dumps(audit))
        with patch('ark_eval.manual_probe._https_once',return_value=(200,reply())):
            audit,_=send_once(CANARY)
        self.assertEqual(audit['response_observations']['reasoning_content_type'],'absent')
        self.assertFalse(audit['response_observations']['reasoning_content_present'])

    def test_error_and_transport_axes_do_not_claim_application_or_generation_success(self):
        for status,transport in [(401,'AUTH_REJECTED'),(403,'FORBIDDEN'),(429,'HTTP_ERROR'),(302,'HTTP_ERROR'),(500,'HTTP_ERROR')]:
            with patch('ark_eval.manual_probe._https_once',return_value=(status,b'')) as network:
                audit,_=send_once(CANARY)
            self.assertEqual(network.call_count,1)
            self.assertEqual(outcome_axes(status,audit['status'],audit['failure']),{'transport_status':transport,'application_status':'NOTRUN'})
            self.assertIsNone(audit['reported_real_model_calls'])
        with patch('ark_eval.manual_probe._https_once',side_effect=OSError('fixture')):
            audit,_=send_once(CANARY)
        self.assertEqual(outcome_axes(None,'FAIL',audit['failure']),{'transport_status':'TRANSPORT_ERROR','application_status':'NOTRUN'})

    def test_v1_v2_16_token_history_and_v3_caps_roundtrip_and_tamper_reject(self):
        with tempfile.TemporaryDirectory() as root:
            for cap in [16,128,256]:
                with patch('ark_eval.manual_probe._https_once',return_value=(200,reply())):
                    audit,normalized=send_once(CANARY,cap)
                path=Path(root)/str(cap);save_bundle(path,audit,normalized)
                self.assertEqual(import_bundle(path)['max_output_tokens'],cap)
                audit['max_output_tokens']=cap+1
                (path/'audit.json').write_text(json.dumps(audit))
                with self.assertRaises(EvaluationError):import_bundle(path)
            for schema in ['gir-user-local-probe@1','gir-user-local-probe@2']:
                with patch('ark_eval.manual_probe._https_once',return_value=(401,b'')):
                    audit,normalized=send_once(CANARY,16)
                audit={k:v for k,v in audit.items() if k in AUDIT_KEYS_V2}
                audit['schema']=schema
                if schema.endswith('@1'):audit.pop('provider_error_code');audit.pop('provider_error_diagnostic')
                path=Path(root)/schema[-1];save_bundle(path,audit,normalized)
                self.assertEqual(import_bundle(path)['max_output_tokens'],16)

    def test_metadata_injection_and_inconsistent_types_are_rejected_on_import(self):
        with patch('ark_eval.manual_probe._https_once',return_value=(200,response())):
            audit,normalized=send_once(CANARY)
        mutations=[{'extra':CANARY},{'reasoning_content_chars':-1},{'reasoning_content_present':False},
                   {'reasoning_content_type':'unknown'},{'content_chars':True}]
        with tempfile.TemporaryDirectory() as root:
            for index,mutation in enumerate(mutations):
                changed=json.loads(json.dumps(audit));changed['response_observations'].update(mutation)
                path=Path(root)/str(index);save_bundle(path,changed,normalized)
                with self.assertRaises(EvaluationError):import_bundle(path)
