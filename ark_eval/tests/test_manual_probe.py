import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from ark_eval.adapter import EvaluationError, MODEL as LEGACY_MODEL, import_response
from ark_eval.manual_probe import (HOST, PATH, MODEL, _https_once, import_bundle,
                                   main, probe_request, save_bundle, send_once)

CANARY='fixture-only-not-a-credential'


def reply(content='OK',model=MODEL,tokens=1):
    return json.dumps({'model':model,'choices':[{'message':{'role':'assistant','content':content},'finish_reason':'stop'}],
                       'usage':{'prompt_tokens':12,'completion_tokens':tokens,'total_tokens':12+tokens}}).encode()


class UserLocalProbeTests(unittest.TestCase):
    def test_prepare_is_offline_and_uses_exact_user_identifier(self):
        with tempfile.TemporaryDirectory() as root, patch('ark_eval.manual_probe._https_once') as network, patch('ark_eval.manual_probe.getpass.getpass') as key, patch('sys.stdout',new=io.StringIO()):
            output=Path(root)/'new'
            self.assertEqual(main(['--output',str(output)]),0)
            self.assertEqual(json.loads((output/'request.json').read_text())['model'],MODEL)
            network.assert_not_called();key.assert_not_called()

    def test_send_requires_user_status_and_private_terminal_before_secret_prompt(self):
        with tempfile.TemporaryDirectory() as root, patch('ark_eval.manual_probe.getpass.getpass') as key, patch('sys.stdin.isatty',return_value=False), patch('sys.stdout',new=io.StringIO()):
            for flags in (['--user-local-send'],['--user-local-send','--plan-status-confirmed']):
                self.assertEqual(main(flags+['--output',str(Path(root)/'unused')]),2)
            key.assert_not_called()

    def test_native_transport_is_one_fixed_tls_host_and_path_without_redirect_or_proxy(self):
        conn=MagicMock();conn.getresponse.return_value.status=302
        with patch('ark_eval.manual_probe.ssl.create_default_context',return_value='verified-context') as tls, patch('ark_eval.manual_probe.http.client.HTTPSConnection',return_value=conn) as constructor:
            status,body=_https_once(probe_request(),CANARY)
        constructor.assert_called_once_with(HOST,443,timeout=30,context='verified-context')
        tls.assert_called_once_with();conn.set_debuglevel.assert_called_once_with(0)
        self.assertEqual(conn.request.call_count,1)
        self.assertEqual(conn.request.call_args.args[:2],('POST',PATH))
        self.assertEqual(conn.request.call_args.kwargs['headers']['Authorization'],'Bearer '+CANARY)
        conn.getresponse.return_value.read.assert_not_called();conn.close.assert_called_once()
        self.assertEqual((status,body),(302,b''))

    def test_errors_never_retry_fallback_or_export_raw_provider_body(self):
        for status,expected in ((401,'AUTH'),(403,'FORBIDDEN'),(429,'QUOTA_OR_RATE_LIMIT'),(302,'REDIRECT_REJECTED'),(500,'HTTP_ERROR')):
            with patch('ark_eval.manual_probe._https_once',return_value=(status,CANARY.encode())) as network:
                audit,response=send_once(CANARY)
            self.assertEqual(network.call_count,1);self.assertEqual(audit['failure'],expected)
            self.assertIsNone(response);self.assertIsNone(audit['reported_real_model_calls'])
            self.assertNotIn(CANARY,json.dumps(audit));self.assertFalse(audit['paid_fallback'])
        with patch('ark_eval.manual_probe._https_once',side_effect=OSError(CANARY)) as network:
            audit,response=send_once(CANARY)
        self.assertEqual(network.call_count,1);self.assertEqual(audit['failure'],'TRANSPORT')
        self.assertNotIn(CANARY,json.dumps(audit));self.assertIsNone(response)

    def test_literal_and_json_escaped_secret_echo_are_not_written(self):
        bodies=[reply(CANARY),reply(CANARY).replace(CANARY.encode(),''.join('\\u%04x'%ord(c) for c in CANARY).encode())]
        for body in bodies:
            with patch('ark_eval.manual_probe._https_once',return_value=(200,body)):
                audit,response=send_once(CANARY)
            self.assertEqual(audit['failure'],'SECRET_ECHO_REJECTED');self.assertIsNone(response)
            with tempfile.TemporaryDirectory() as root:
                output=Path(root)/'new';save_bundle(output,audit,response)
                self.assertFalse((output/'response.json').exists())
                self.assertNotIn(CANARY,(output/'audit.json').read_text())

    def test_model_budget_tools_and_malformed_envelopes_fail_closed(self):
        bad_tools=json.loads(reply());bad_tools['choices'][0]['message']['tool_calls']=[{'name':'do_not_run'}]
        for body,expected in ((reply(model=LEGACY_MODEL),'MODEL_MISMATCH'),(reply(tokens=129),'USAGE_EXCEEDS_RESERVATION'),
                              (reply(content='untrusted code must stay inert'),'UNEXPECTED_CONNECTIVITY_RESPONSE'),
                              (json.dumps(bad_tools).encode(),'INVALID_RESPONSE'),(b'{"x":1,"x":2}','INVALID_RESPONSE'),
                              (b'x'*65537,'RESPONSE_TOO_LARGE')):
            with patch('ark_eval.manual_probe._https_once',return_value=(200,body)):
                audit,response=send_once(CANARY)
            self.assertEqual(audit['failure'],expected);self.assertIsNone(response)
        imported=import_response(reply().decode(),requested_model=MODEL)
        self.assertTrue(imported.model_matches_requested)
        self.assertFalse(import_response(reply().decode()).model_matches_requested)

    def test_closed_bundle_import_is_offline_nonexecuting_and_detects_tamper(self):
        with patch('ark_eval.manual_probe._https_once',return_value=(200,reply())):
            audit,response=send_once(CANARY)
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'bundle';save_bundle(output,audit,response)
            with patch('ark_eval.manual_probe._https_once') as network:
                imported=import_bundle(output)
            network.assert_not_called()
            self.assertEqual(imported['status'],'RESPONSE_IMPORTED')
            self.assertEqual(imported['importer_real_model_calls'],0)
            self.assertEqual(imported['generated_source_executions'],0)
            response['choices'][0]['message']['content']='changed'
            (output/'response.json').write_text(json.dumps(response))
            with self.assertRaises(EvaluationError):import_bundle(output)
            audit['Authorization']=CANARY
            (output/'audit.json').write_text(json.dumps(audit))
            with self.assertRaises(EvaluationError):import_bundle(output)

    def test_invalid_key_and_existing_evidence_are_rejected_without_network(self):
        with patch('ark_eval.manual_probe._https_once') as network:
            for key in (None,'short','with whitespace','new\nline','é'*10):
                with self.assertRaises(EvaluationError):send_once(key)
        network.assert_not_called()
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'new';save_bundle(output,{},None)
            with self.assertRaises(FileExistsError):save_bundle(output,{},None)
