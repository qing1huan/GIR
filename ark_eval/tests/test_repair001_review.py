"""Static compilation/import and denial tests only, no candidate execution."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ark_eval.adapter import EvaluationError
from ark_eval.repair001_review import static_findings,approval_request,validate_pending_request,require_execution_approval,main,HASHES


class Repair001ReviewTests(unittest.TestCase):
    def test_static_review_does_not_create_runtime_parser_or_model_transport(self):
        with patch('headless_v01.runtime.Runtime.step',side_effect=AssertionError('No execution')),patch('ark_eval.candidate_parse_backend.parse_module',side_effect=AssertionError('No parser')),patch('ark_eval.manual_probe._https_once',side_effect=AssertionError('No network')):
            report=static_findings()
        self.assertEqual(report['candidate_sha256'],HASHES)
        self.assertEqual(report['gameir_text']['static_compile'],'PASS')
        self.assertEqual(report['js_callback']['branch_error_semantics'],'FAIL_STATIC_COUNTEREXAMPLE')
        self.assertEqual(report['candidate_executions'],0)

    def test_combined_manifest_preserves_hashes_and_denies_pending_execution(self):
        manifest=approval_request();self.assertTrue(validate_pending_request(manifest))
        self.assertEqual(manifest['requested']['gameir_text']['fixed_cases'],26)
        self.assertFalse(manifest['requested']['js_callback']['runtime_requested'])
        with self.assertRaisesRegex(EvaluationError,'HUMAN_APPROVAL_PENDING'):require_execution_approval(manifest)
        for key,value in [('human_approval_received',True),('execution_authorized',True),('candidate_sha256',{'gameir_text':'0'*64})]:
            changed=copy.deepcopy(manifest);changed[key]=value
            with self.assertRaisesRegex(EvaluationError,'REQUEST_DRIFT'):require_execution_approval(changed)

    def test_bundle_source_or_audit_drift_cannot_prepare_manifest(self):
        original=Path.read_bytes
        def changed(path):
            raw=original(path)
            if path.as_posix().endswith('user-local-create-repair-001/js_callback/candidate.txt'):return raw+b'\n'
            return raw
        with patch.object(Path,'read_bytes',changed):
            with self.assertRaisesRegex(EvaluationError,'ARM_BINDING_REJECTED'):approval_request()

    def test_report_output_does_not_overwrite_or_execute(self):
        with tempfile.TemporaryDirectory() as root,patch('sys.stdout',new=io.StringIO()):
            output=Path(root)/'review';self.assertEqual(main(['--output',str(output)]),0)
            manifest=json.loads((output/'approval-request-manifest.json').read_text())
            self.assertFalse(manifest['execution_authorized']);self.assertEqual(manifest['candidate_executions'],0)
            with self.assertRaises(SystemExit):main(['--output',str(output)])


if __name__=='__main__':unittest.main()
