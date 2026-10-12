"""Pre-execution gates only. No candidate engine run or real backend creation."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ark_eval.adapter import EvaluationError
from ark_eval.pair003_eval import authorization,validate_authorization,load_candidates,main,HASHES,FreshIRIsolation,evaluate_ir


class Pair003EvalTests(unittest.TestCase):
    def test_exact_hashes_and_raw_fences_preserved_without_execution(self):
        sources=load_candidates()
        self.assertEqual({k:hashlib.sha256(v.encode()).hexdigest() for k,v in sources.items()},HASHES)
        self.assertTrue(sources['js_callback'].startswith('```js\n'))
        self.assertTrue(sources['js_callback'].endswith('\n```'))

    def test_authorization_binds_current_runner_and_blocks_any_JS_evaluation(self):
        manifest=authorization()
        self.assertFalse(manifest['JS_evaluation_authorized']);self.assertFalse(manifest['fence_stripping_authorized'])
        self.assertIn('ark_eval/pair003_ir_worker.py',manifest['bindings'])
        self.assertEqual(validate_authorization(manifest),load_candidates())
        for field,value in [('generation_protocol_digest','0'*64),('candidate_sha256',{'gameir_text':'0'*64}),('JS_evaluation_authorized',True)]:
            changed=copy.deepcopy(manifest);changed[field]=value
            with self.assertRaisesRegex(EvaluationError,'AUTHORIZATION_DRIFT'):validate_authorization(changed)

    def test_default_prepares_manifest_without_backend_or_source_execution(self):
        with tempfile.TemporaryDirectory() as root,patch('ark_eval.pair003_eval.NodeBackend') as node,patch('ark_eval.pair003_eval.Pair003IRBackend') as ir,patch('sys.stdout',new=io.StringIO()):
            output=Path(root)/'prepared';self.assertEqual(main(['--output',str(output)]),0)
            node.assert_not_called();ir.assert_not_called()
            self.assertEqual(json.loads((output/'authorization-manifest.json').read_text())['candidate_sha256'],HASHES)

    def test_unverified_backend_and_receipt_cannot_run_candidate(self):
        with self.assertRaisesRegex(EvaluationError,'FRESH_IR_ISOLATION_REJECTED'):FreshIRIsolation(object(),{'checks':{}},[])
        with self.assertRaisesRegex(EvaluationError,'IR_BACKEND_OR_RECEIPT_REJECTED'):evaluate_ir({},object(),object(),{})


if __name__=='__main__':unittest.main()
