"""Scope/parser/isolation denial mocks; never create real workers or execute source."""
import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ark_eval.adapter import EvaluationError
from ark_eval.repair001_scope import ExactScope,FreshScopeIsolation,approved_manifest,require_JS_parse
from ark_eval.scoped_candidate_backend import scoped_payload
from ark_eval.scoped_candidate_runner import ScopedJSRunner
from ark_eval.repair001_eval import main,evaluate_ir,load_completed,require_completed_bytes,validate_completed
from ark_eval.oracle import final_cases


class Repair001ScopeTests(unittest.TestCase):
    def test_both_hashes_approved_and_any_scope_drift_rejected(self):
        manifest=approved_manifest();scope=ExactScope(manifest);scope.require()
        self.assertEqual(manifest['representations'],['gameir_text','js_callback'])
        for key,value in [('human_approval_received',False),('candidate_sha256',{'gameir_text':'0'*64}),('case_count_per_arm',27)]:
            changed=copy.deepcopy(manifest);changed[key]=value
            with self.assertRaisesRegex(EvaluationError,'UNAUTHORIZED'):ExactScope(changed)

    def test_missing_parse_or_actual_isolation_receipt_cannot_launch(self):
        scope=ExactScope(approved_manifest())
        with self.assertRaisesRegex(EvaluationError,'PARSE_REJECTED'):require_JS_parse(scope,{})
        with self.assertRaisesRegex(EvaluationError,'FRESH_SCOPE'):FreshScopeIsolation(scope,object(),{'checks':{}},[],'gameir_text')
        with self.assertRaisesRegex(EvaluationError,'BACKEND_REJECTED'):ScopedJSRunner(scope,object(),object(),{})
        with self.assertRaisesRegex(EvaluationError,'BACKEND_REJECTED'):evaluate_ir(scope,object(),object(),{})

    def test_public_worker_template_changes_only_hash_configuration(self):
        scope=ExactScope(approved_manifest());root=Path(__file__).resolve().parents[2]
        template=(root/'ark_eval/pair003_ir_worker.py').read_bytes()
        with patch('ark_eval.scoped_candidate_backend.image_payload',return_value={'gir-ir-worker.py':template,'public-fixture':b'unchanged'}):
            payload=scoped_payload(root,{},scope)
        old=b"SOURCE_SHA256='9cd7ea46ba86790a5413c78e5bd5846c538b75cfb65f6713c5596a47f1e87b42'"
        new=("SOURCE_SHA256='"+scope.manifest['candidate_sha256']['gameir_text']+"'").encode()
        self.assertEqual(payload['gir-ir-worker.py'],template.replace(old,new));self.assertEqual(payload['public-fixture'],b'unchanged')
        self.assertEqual((root/'ark_eval/pair003_ir_worker.py').read_bytes(),template)

    def test_default_prepares_new_manifest_without_backend_or_execution(self):
        with tempfile.TemporaryDirectory() as root,patch('ark_eval.repair001_eval.NodeBackend') as node,patch('ark_eval.repair001_eval.ScopedIRBackend') as ir,patch('sys.stdout',new=io.StringIO()):
            self.assertEqual(main(['--output',str(Path(root)/'prepared')]),0)
            node.assert_not_called();ir.assert_not_called()

    def test_admission_rechecks_full_bytes_without_rebuilding_protocol(self):
        scope=ExactScope(approved_manifest())
        with patch('ark_eval.repair001_scope.approved_manifest',side_effect=AssertionError('rebuild')):
            scope.require();self.assertTrue(scope.source('js_callback'))
        changed=dict(scope.manifest['bundle15_sha256']);changed['protocol.json']='0'*64
        with patch('ark_eval.repair001_scope.artifact_hashes',return_value=changed):
            with self.assertRaisesRegex(EvaluationError,'EXACT_SCOPE_CHANGED'):scope.require()
        with patch.object(Path,'read_bytes',return_value=b'changed full code bytes'):
            with self.assertRaisesRegex(EvaluationError,'EXACT_SCOPE_CHANGED'):scope.require()
        scope.manifest['human_approval_received']=False
        with self.assertRaisesRegex(EvaluationError,'EXACT_SCOPE_CHANGED'):scope.require()

    def test_continuation_validates_fixed17_evidence_and_rejects_drift(self):
        scope=ExactScope(approved_manifest());rows,provenance=load_completed(scope)
        self.assertEqual(len(rows),17);require_completed_bytes(provenance)
        row=copy.deepcopy(rows[('js_callback','P-C02-create-0-seed1')]);row['expected']={}
        with self.assertRaisesRegex(EvaluationError,'CASE_REJECTED'):
            validate_completed(row,'js_callback',final_cases('P-C02-create')[0])
        changed=copy.deepcopy(provenance);changed['completed_file_sha256'][next(iter(changed['completed_file_sha256']))]='0'*64
        with self.assertRaisesRegex(EvaluationError,'RECORD_CHANGED'):require_completed_bytes(changed)
        changed=copy.deepcopy(provenance);changed['previous_manifest_sha256']='0'*64
        with self.assertRaisesRegex(EvaluationError,'MANIFEST_CHANGED'):require_completed_bytes(changed)


if __name__=='__main__':unittest.main()
