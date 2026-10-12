"""Synthetic validation only; never execute a model candidate or invoke Podman."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,patch

from ark_eval.adapter import EvaluationError
from ark_eval.bridge import execute_candidate
from ark_eval.js_runner import execute_candidate_source
from ark_eval.candidate_review import HASHES,ROOT,static_review,approval_manifest,validate_approval,load_candidates
from ark_eval.candidate_parse_backend import CandidateParserBackend,parse_module
from ark_eval.candidate_runner import FreshIsolation,ApprovedCandidateJSRunner,CandidateHostRuntime
from ark_eval.node_backend import NodeBackend
from ark_eval.tasks import base_program
from ark_eval.candidate_eval import main,FAULT_NAMES


def parser_evidence():
    lock=json.loads((ROOT/'ark_eval/node_image_lock.json').read_text())
    fingerprint=hashlib.sha256((ROOT/'ark_eval/candidate_parser.mjs').read_bytes()+
        (ROOT/'ark_eval/node_supervisor.sh').read_bytes()+lock['repository_digest'].encode()).hexdigest()
    return {'result':{'status':'PARSED','exports':['onOpen'],'static_imports':[],
                     'source_sha256':HASHES['js_callback'],'top_level_evaluated':False,'callback_invoked':False},
            'candidate_executions':0,'worker_audit':{'worker_source_sha256':fingerprint,
              'cleanup_errors':[],'surviving_owned_processes':0,'network_mode':'none','readonly_rootfs':True,
              'user_mounts':[],'generated_source_executions':0,
              'kernel_security':{'CapEff':'0000000000000000','NoNewPrivs':'1','Seccomp':'2'},
              'kernel_limits':{'cpu.max':'25000 100000','memory.max':'67108864','memory.swap.max':'0','pids.max':'32'}}}


class CandidateApprovalTests(unittest.TestCase):
    def review_fixture(self):
        p=base_program();p['handlers']=[{'id':'open','event':'Open','priority':0,'body':[{'op':'return'}]}]
        return patch('ark_eval.candidate_review.load_candidates',return_value=(
            {'gameir_text':json.dumps(p),'js_callback':'fixture inert text'}, {'summary':{}}))

    def backend(self):
        b=object.__new__(NodeBackend);b.disabled=False;b.worker_image='fixture';b.worker_hash='fixture'
        return b

    def proof(self):
        return ({'checks':{k:'PASS' for k in ('rootless','network_denied','no_user_mounts','environment_scrubbed',
                    'cpu_limits','memory_limits','process_tree_termination')}},
                [{'fixture':name,'status':'PASS'} for name in FAULT_NAMES])

    def test_static_ir_rejection_does_not_repair_or_execute(self):
        with self.review_fixture(),patch('ark_eval.bridge.execute_candidate') as execute:
            review=static_review()
        execute.assert_not_called()
        self.assertEqual(review['gameir_text']['compiler_fault']['details'],{'op':'return'})
        self.assertEqual(review['candidate_executions'],0)

    def test_approval_rejects_syntax_imports_exports_evaluation_and_profile_mismatch(self):
        with self.review_fixture():
            for field,value in [('status','SYNTAX_REJECTED'),('exports',['other']),('static_imports',['node:fs']),
                                 ('top_level_evaluated',True),('callback_invoked',True),('source_sha256','0'*64)]:
                proof=parser_evidence();proof['result'][field]=value
                with self.assertRaises(EvaluationError):approval_manifest(proof)
            for field,value in [('network_mode','host'),('generated_source_executions',1),('surviving_owned_processes',1),('worker_source_sha256','0'*64)]:
                proof=parser_evidence();proof['worker_audit'][field]=value
                with self.assertRaises(EvaluationError):approval_manifest(proof)

    def test_manifest_binds_protocol_review_and_runner_versions(self):
        with self.review_fixture():
            manifest=approval_manifest(parser_evidence())
            self.assertEqual(validate_approval(manifest),'fixture inert text')
            for field,value in [('generation_protocol_digest','0'*64),('candidate_sha256',{}),('bindings',{}),
                                ('review',{'gameir_text':'APPROVED','js_callback':'APPROVED'}),('repairs',1),('source_edited',True)]:
                mutated=copy.deepcopy(manifest);mutated[field]=value
                with self.assertRaises(EvaluationError):validate_approval(mutated)
            mutated=copy.deepcopy(manifest);mutated['extra']=True
            with self.assertRaises(EvaluationError):validate_approval(mutated)

    def test_source_hash_or_symlink_change_rejects_before_approval(self):
        with tempfile.TemporaryDirectory() as root:
            bundle=Path(root)
            for rep in HASHES:
                directory=bundle/rep;directory.mkdir();(directory/'candidate.txt').write_text('unknown fixture')
            with patch('ark_eval.candidate_review.BUNDLE',bundle),patch('ark_eval.candidate_review.import_pair',
                return_value={'summary':{'protocol_digest':'a4d8a8ca3702954a170765597f84671a5fc2b45eb3e79d5754d9b5861f76bed8'}}):
                with self.assertRaises(EvaluationError):load_candidates()

    def test_fresh_receipt_rejects_missing_or_failed_fault_and_profile_checks(self):
        b=self.backend();profile,faults=self.proof()
        for p,f in [(profile,faults[:-1]),({'checks':{'invented':'PASS'}},faults),
                    (profile,[{**x,'status':'FAIL'} for x in faults])]:
            with self.assertRaises(EvaluationError):FreshIsolation(b,p,f)

    def test_fresh_receipt_rejects_backend_change_source_drift_and_expiry(self):
        b=self.backend();profile,faults=self.proof();receipt=FreshIsolation(b,profile,faults)
        with self.assertRaises(EvaluationError):receipt.require(self.backend())
        with patch('ark_eval.candidate_runner.version_bindings',return_value={}):
            with self.assertRaises(EvaluationError):receipt.require(b)
        receipt.created-=601
        with self.assertRaises(EvaluationError):receipt.require(b)

    def test_no_runner_accepts_unapproved_object_or_legacy_arbitrary_execution(self):
        with self.assertRaises(EvaluationError):ApprovedCandidateJSRunner({},self.backend(),object())
        with self.assertRaises(EvaluationError):CandidateHostRuntime(base_program(),[],object())
        for gate in (execute_candidate,execute_candidate_source):
            with self.assertRaises(EvaluationError):gate('fixture inert text')

    def test_parser_transport_verifies_hash_no_evaluation_and_cleanup(self):
        backend=object.__new__(CandidateParserBackend);worker=MagicMock()
        worker.audit={'cleanup_errors':[],'surviving_owned_processes':0}
        backend.invocation=lambda timeout:worker
        result=parser_evidence()['result'];result['source_sha256']=hashlib.sha256(b'fixture inert text').hexdigest()
        for key,value in [('source_sha256','0'*64),('top_level_evaluated',True),('callback_invoked',True)]:
            bad={**result,key:value};worker.read_line.side_effect=[json.dumps({'transport':'gir-candidate-parse-result@1','result':bad})]
            with self.assertRaises(EvaluationError):parse_module('fixture inert text',backend)
        worker.read_line.side_effect=[json.dumps({'transport':'gir-candidate-parse-result@1','result':result}),json.dumps({'transport':'gir-node-exit@1','code':0})]
        self.assertEqual(parse_module('fixture inert text',backend)['candidate_executions'],0)

    def test_default_cli_does_not_parse_or_execute(self):
        with tempfile.TemporaryDirectory() as root,self.review_fixture(),patch('ark_eval.candidate_eval.NodeBackend') as backend,patch('ark_eval.candidate_eval.parse_module') as parse,patch('sys.stdout',new=io.StringIO()):
            self.assertEqual(main(['--output',str(Path(root)/'new')]),0)
            backend.assert_not_called();parse.assert_not_called()
