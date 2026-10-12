import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,patch

from ark_eval.adapter import EvaluationError
from ark_eval.manual_probe import (_https_once,import_bundle,main,probe_request,safe_error_diagnostic,
                                   save_bundle,send_once)
from ark_eval.tests.test_manual_probe import CANARY


class ProbeDiagnosticTests(unittest.TestCase):
    def test_allowlisted_code_only_no_provider_message_or_header_export(self):
        body=json.dumps({'error':{'code':'AuthenticationError','message':'private account description'},
                         'request_headers':{'private':'not exported'}}).encode()
        with patch('ark_eval.manual_probe._https_once',return_value=(401,body)):
            audit,response=send_once(CANARY)
        self.assertEqual(audit['http_status'],401);self.assertEqual(audit['failure'],'AUTH')
        self.assertEqual(audit['provider_error_code'],'AuthenticationError')
        self.assertEqual(audit['provider_error_diagnostic'],'CODE_ALLOWLISTED')
        self.assertIsNone(response);self.assertIsNone(audit['reported_real_model_calls'])
        self.assertNotIn('private account description',json.dumps(audit))
        self.assertNotIn('not exported',json.dumps(audit))

    def test_literal_and_escaped_secret_echo_drop_diagnostics_keep_http_status(self):
        raw=json.dumps({'error':{'code':'AuthenticationError','message':CANARY}}).encode()
        for body in [raw,raw.replace(CANARY.encode(),''.join('\\u%04x'%ord(c) for c in CANARY).encode())]:
            with patch('ark_eval.manual_probe._https_once',return_value=(401,body)):
                audit,response=send_once(CANARY)
            self.assertIsNone(audit['provider_error_code'])
            self.assertEqual(audit['provider_error_diagnostic'],'SECRET_ECHO_DROPPED')
            self.assertEqual(audit['http_status'],401);self.assertEqual(audit['failure'],'AUTH')
            self.assertNotIn(CANARY,json.dumps(audit));self.assertIsNone(response)

    def test_unknown_malformed_or_large_error_body_does_not_change_http_classification(self):
        bodies=[(json.dumps({'error':{'code':'not-allowlisted'}}).encode(),'OTHER_OR_UNAVAILABLE'),
                (b'{"error":1,"error":2}','INVALID_ERROR_ENVELOPE'),(b'\xff','INVALID_ERROR_ENVELOPE'),
                (b'x'*65537,'ERROR_BODY_TOO_LARGE'),(b'','ERROR_BODY_UNAVAILABLE')]
        for body,diagnostic in bodies:
            with patch('ark_eval.manual_probe._https_once',return_value=(403,body)):
                audit,response=send_once(CANARY)
            self.assertEqual(audit['failure'],'FORBIDDEN');self.assertEqual(audit['provider_error_diagnostic'],diagnostic)
            self.assertIsNone(audit['provider_error_code']);self.assertIsNone(response)

    def test_non200_native_transport_reads_bounded_error_body_once(self):
        conn=MagicMock();conn.getresponse.return_value.status=401
        body=b'{"error":{"code":"AuthenticationError"}}'
        conn.getresponse.return_value.read.return_value=body
        with patch('ark_eval.manual_probe.ssl.create_default_context',return_value='fixture-context'),patch('ark_eval.manual_probe.http.client.HTTPSConnection',return_value=conn):
            status,returned=_https_once(probe_request(),CANARY)
        self.assertEqual((status,returned),(401,body))
        conn.getresponse.return_value.read.assert_called_once_with(65537)
        self.assertEqual(conn.request.call_count,1);conn.close.assert_called_once()

    def test_error_body_read_failure_preserves_observed_status(self):
        conn=MagicMock();conn.getresponse.return_value.status=401
        conn.getresponse.return_value.read.side_effect=OSError('private fixture')
        with patch('ark_eval.manual_probe.ssl.create_default_context',return_value='fixture-context'),patch('ark_eval.manual_probe.http.client.HTTPSConnection',return_value=conn):
            self.assertEqual(_https_once(probe_request(),CANARY),(401,b''))

    def test_old_bundle_import_remains_supported_and_new_tokens_are_closed(self):
        with patch('ark_eval.manual_probe._https_once',return_value=(401,b'')):
            old,response=send_once(CANARY,16)
        old['schema']='gir-user-local-probe@1'
        old.pop('provider_error_code');old.pop('provider_error_diagnostic')
        old.pop('response_observations')
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'old';save_bundle(path,old,response)
            imported=import_bundle(path)
            self.assertEqual(imported['http_status'],401)
            self.assertEqual(imported['provider_error_diagnostic'],'NOT_CAPTURED_IN_V1')
            self.assertIsNone(imported['reported_real_model_calls'])
            new=dict(old,schema='gir-user-local-probe@2',provider_error_code=CANARY,provider_error_diagnostic='CODE_ALLOWLISTED')
            (path/'audit.json').write_text(json.dumps(new))
            with self.assertRaises(EvaluationError):import_bundle(path)

    def test_second_same_directory_cli_stops_before_key_prompt_and_transport(self):
        with tempfile.TemporaryDirectory() as root,patch('ark_eval.manual_probe.getpass.getpass') as key,patch('ark_eval.manual_probe._https_once') as network,patch('sys.stderr',new=io.StringIO()):
            path=Path(root)/'existing';path.mkdir();marker=path/'audit.json';marker.write_text('fixture preserved')
            with self.assertRaises(SystemExit) as exit:
                main(['--user-local-send','--plan-status-confirmed','--output',str(path)])
            self.assertEqual(exit.exception.code,2);key.assert_not_called();network.assert_not_called()
            self.assertEqual(marker.read_text(),'fixture preserved')
