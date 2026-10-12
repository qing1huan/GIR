import contextlib
import copy
import io
import http.client
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ark_eval import manual_pilot24 as entry
from ark_eval.adapter import ACCOUNT_MODEL, EvaluationError
from headless_v01.compiler import digest

FIXTURE='PUBLIC-MOCK-ONLY-NOT-A-KEY'


def response(content='inert source', finish='stop', **changes):
    row={'model':ACCOUNT_MODEL,'choices':[{'message':{'role':'assistant','content':content},
                                         'finish_reason':finish}],
         'usage':{'prompt_tokens':100,'completion_tokens':12,'total_tokens':112}}
    row.update(changes)
    return 200,json.dumps(row).encode()


class Pilot24EntryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=entry.ROOT/'results')
        self.root=Path(self.temp.name)
        self.claim_patch=patch.object(entry,'CLAIM_ROOT',self.root/'claims')
        self.claim_patch.start()
        self.review_patch=patch.object(entry,'REVIEW',self.root/'review.json')
        self.review_patch.start()
        entry.write_new(entry.REVIEW,{'schema':'gir-pilot24-send-review@1','decision':'PASS',
            'entry_digest':digest(entry.entry_manifest()),'material_digest':entry.MATERIAL_DIGEST,
            'scope_digest':entry.scope_digest(),'candidate_execution_authorized':False})

    def tearDown(self):
        self.review_patch.stop();self.claim_patch.stop();self.temp.cleanup()

    def batch(self, effects=None):
        with patch.object(entry,'_https_once',side_effect=effects,return_value=response()) as net:
            report=entry.run_batch(self.root/'batch',FIXTURE)
        return report,net

    def test_exact_fixed_requests_order_independence_and_caps(self):
        report,net=self.batch()
        protocol,sequence,_=entry.load_requests()
        self.assertEqual(net.call_count,24)
        self.assertEqual(report['model_calls_lower'],24);self.assertEqual(report['model_calls_upper'],24)
        self.assertEqual([r['slot'] for r in report['slots']],protocol['slots'])
        for call,(_,req) in zip(net.call_args_list,sequence):
            self.assertEqual(call.args,(req,FIXTURE));self.assertEqual(call.kwargs,{'timeout':120})
            self.assertEqual([m['role'] for m in req.body['messages']],['system','user'])
            self.assertEqual(req.body['max_tokens'],4096)
            self.assertEqual(req.body['thinking'],{'type':'disabled'})
        self.assertEqual(report['maximum_output_tokens'],98304)
        self.assertEqual(report['case_counts'],{'PASS':0,'FAIL':0,'NOTRUN':288})
        self.assertEqual(report['generated_source_executions'],0)
        self.assertEqual(len(list((self.root/'batch').glob('*/candidate.txt'))),24)

    def test_same_approval_cannot_send_in_other_output(self):
        self.batch()
        with patch.object(entry,'_https_once') as net:
            with self.assertRaisesRegex(EvaluationError,'ALREADY_CONSUMED'):
                entry.run_batch(self.root/'another',FIXTURE)
            net.assert_not_called();self.assertFalse((self.root/'another').exists())

    def test_existing_output_never_overwritten(self):
        out=self.root/'batch';out.mkdir();(out/'kept').write_text('preserve')
        with patch.object(entry,'_https_once') as net,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(entry.main(['--output',str(out)]),2)
            net.assert_not_called()
        self.assertEqual((out/'kept').read_text(),'preserve')

    def test_truncated_empty_and_source_limit_continue_no_growth(self):
        report,net=self.batch([response('cut','length'),response('  '),response('x'*16385)]+[response()]*21)
        self.assertEqual(net.call_count,24)
        self.assertEqual([r['generation_status'] for r in report['slots'][:3]],
                         ['FAIL_TRUNCATED','FAIL_EMPTY','FAIL_SOURCE_LIMIT'])
        self.assertTrue((self.root/'batch'/report['slots'][0]['slot']['slot_id']/'candidate.txt').exists())
        self.assertIsNone(report['slots'][2]['candidate_file'])

    def test_provider_failures_stop_and_keep_unknown_bounds(self):
        for status in (401,403,429,302,500):
            with self.subTest(status=status), tempfile.TemporaryDirectory(dir=self.root) as temp:
                with patch.object(entry,'CLAIM_ROOT',Path(temp)/'claims'),patch.object(entry,'_https_once',return_value=(status,b'provider text '+FIXTURE.encode())) as net:
                    report=entry.run_batch(Path(temp)/'batch',FIXTURE)
                self.assertEqual(net.call_count,1);self.assertEqual(report['model_calls_lower'],0)
                self.assertEqual(report['model_calls_upper'],1)
                self.assertEqual([r['generation_status'] for r in report['slots'][1:]],['NOTRUN']*23)

    def test_unknown_timeout_not_retryable(self):
        report,net=self.batch([OSError(FIXTURE)])
        self.assertEqual(net.call_count,1);self.assertEqual(report['model_calls_upper'],1)
        self.assertEqual(report['slots'][0]['generation_status'],'OUTCOME_UNKNOWN')
        self.assertNotIn(FIXTURE,json.dumps(report))

    def test_protocol_uncertainty_halts(self):
        invalid=[response(model='wrong'), response(usage={}), response(usage={'prompt_tokens':24001,'completion_tokens':12,'total_tokens':24013}),
                 response(usage={'prompt_tokens':100,'completion_tokens':4097,'total_tokens':4197})]
        data=json.loads(response()[1]);data['choices'][0]['message']['reasoning_content']='hidden reasoning'
        invalid.append((200,json.dumps(data).encode()))
        data=copy.deepcopy(data);data['choices'][0]['message']['reasoning_content']=7
        invalid.append((200,json.dumps(data).encode()))
        data=json.loads(response()[1]);data['choices'][0]['message']['tool_calls']=[{}]
        invalid.append((200,json.dumps(data).encode()))
        invalid.extend([(200,b'not json'),(200,b'x'*(entry.RESPONSE_BYTES+1))])
        slot,req=entry.load_requests()[1][0]
        for value in invalid:
            with self.subTest(value=value[0]),patch.object(entry,'_https_once',return_value=value):
                row,candidate=entry.attempt(slot,req,FIXTURE)
                self.assertFalse(row['continue_fixed_schedule']);self.assertIsNone(candidate)
                self.assertEqual(row['model_calls_upper'],1)

    def test_secret_echoes_never_persist(self):
        for escaped in (False,True):
            with self.subTest(escaped=escaped),tempfile.TemporaryDirectory(dir=self.root) as temp:
                raw=response(FIXTURE)[1]
                if escaped:raw=raw.replace(FIXTURE.encode(),('\\u0050'+FIXTURE[1:]).encode())
                with patch.object(entry,'CLAIM_ROOT',Path(temp)/'claims'),patch.object(entry,'_https_once',return_value=(200,raw)):
                    report=entry.run_batch(Path(temp)/'batch',FIXTURE)
                self.assertIsNone(report['slots'][0]['candidate_file'])
                for p in Path(temp).rglob('*'):
                    if p.is_file():self.assertNotIn(FIXTURE,p.read_text())

    def test_crash_after_reservation_preserves_checkpoint_and_claim(self):
        with patch.object(entry,'_https_once',side_effect=SystemExit('simulated abrupt process exit')):
            with self.assertRaises(SystemExit):entry.run_batch(self.root/'batch',FIXTURE)
        checkpoint=entry.read(self.root/'batch/checkpoint-01.json')
        self.assertEqual(checkpoint['model_calls_lower'],0);self.assertEqual(checkpoint['model_calls_upper'],1)
        self.assertEqual(checkpoint['slots'][0]['generation_status'],'OUTCOME_UNKNOWN')
        self.assertEqual(checkpoint['slots'][1]['generation_status'],'NOTRUN')
        self.assertTrue((entry.CLAIM_ROOT/entry.scope_digest()/'start.json').exists())
        with patch.object(entry,'_https_once') as net:
            with self.assertRaises(EvaluationError):entry.run_batch(self.root/'other',FIXTURE)
            net.assert_not_called()

    def test_request_drift_halts_before_next_call(self):
        count=0
        def transport(*args,**kwargs):
            nonlocal count
            count+=1
            second=entry.load_requests()[1][1][0]['slot_id']
            (self.root/'batch'/second/'request.json').write_text('{}')
            return response()
        report,net=self.batch(transport)
        self.assertEqual(net.call_count,1);self.assertEqual(report['slots'][1]['generation_status'],'NOTRUN')

    def test_frozen_dependency_drift_rejected(self):
        with patch.object(entry,'sha',return_value='bad'),patch.object(entry,'_https_once') as net:
            with self.assertRaises(EvaluationError):entry.load_requests()
            net.assert_not_called()

    def test_pending_review_blocks_before_key_read(self):
        pending=entry.read(entry.REVIEW);pending['decision']='PENDING'
        entry.REVIEW.write_text(json.dumps(pending))
        with patch.object(entry,'read_local_key') as key,patch.object(entry,'_https_once') as net,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(entry.main(['--user-local-send-pilot24','--plan-status-confirmed','--key-file','PUBLIC-UNREAD-FIXTURE','--output',str(self.root/'batch')]),2)
            key.assert_not_called();net.assert_not_called()

    def test_cancellation_blocks_before_key_read(self):
        with contextlib.redirect_stdout(io.StringIO()),patch.object(entry.sys.stdin,'isatty',return_value=True),patch.object(entry.sys.stdout,'isatty',return_value=True),patch('builtins.input',return_value='CANCEL'),patch.object(entry,'read_local_key') as key,patch.object(entry,'_https_once') as net:
            self.assertEqual(entry.main(['--user-local-send-pilot24','--plan-status-confirmed','--key-file','PUBLIC-UNREAD-FIXTURE','--output',str(self.root/'batch')]),2)
            key.assert_not_called();net.assert_not_called()

    def test_read_key_only_after_interactive_exact_confirmation(self):
        order=[]
        def confirm(_):order.append('confirmation');return entry.CONFIRMATION
        def key(*args,**kwargs):order.append('key');return FIXTURE
        with contextlib.redirect_stdout(io.StringIO()) as stdout,patch.object(entry.sys.stdin,'isatty',return_value=True),patch.object(entry.sys.stdout,'isatty',return_value=True),patch('builtins.input',side_effect=confirm),patch.object(entry,'read_local_key',side_effect=key),patch.object(entry,'_https_once',return_value=response()):
            self.assertEqual(entry.main(['--user-local-send-pilot24','--plan-status-confirmed','--key-file','PUBLIC-UNREAD-FIXTURE','--output',str(self.root/'batch')]),0)
        self.assertEqual(order,['confirmation','key']);self.assertNotIn(FIXTURE,stdout.getvalue())

    def test_noninteractive_blocks_key_and_transport(self):
        with patch.object(entry.sys.stdin,'isatty',return_value=False),patch.object(entry,'read_local_key') as key,patch.object(entry,'_https_once') as net,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(entry.main(['--user-local-send-pilot24','--plan-status-confirmed','--key-file','PUBLIC-UNREAD-FIXTURE','--output',str(self.root/'batch')]),2)
            key.assert_not_called();net.assert_not_called()

    def test_prepare_only_does_not_claim_read_key_or_send(self):
        with patch.object(entry,'read_local_key') as key,patch.object(entry,'_https_once') as net:
            report=entry.prepare_only(self.root/'offline')
            key.assert_not_called();net.assert_not_called()
        self.assertFalse(entry.CLAIM_ROOT.exists());self.assertEqual(report['model_calls_upper'],0)

    def test_output_outside_results_rejected(self):
        with self.assertRaises(EvaluationError):entry.prepare_only(entry.ROOT/'not-results')

    def test_review_digest_mismatch_blocks(self):
        value=entry.read(entry.REVIEW);value['entry_digest']='wrong'
        entry.REVIEW.write_text(json.dumps(value))
        with self.assertRaisesRegex(EvaluationError,'FOCUSED_SEND_REVIEW_REQUIRED'):
            entry.load_requests(require_review=True)

    def test_plan_status_required_before_confirmation_or_key(self):
        with patch('builtins.input') as confirm,patch.object(entry,'read_local_key') as key,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(entry.main(['--user-local-send-pilot24','--key-file','PUBLIC-UNREAD-FIXTURE','--output',str(self.root/'batch')]),2)
            confirm.assert_not_called();key.assert_not_called()

    def test_file_io_failure_after_response_does_not_release_or_retry(self):
        original=entry.write_new
        def write(path,data):
            if Path(path).name=='audit.json':raise OSError('simulated disk failure')
            original(path,data)
        with patch.object(entry,'write_new',side_effect=write),patch.object(entry,'_https_once',return_value=response()) as net:
            with self.assertRaises(OSError):entry.run_batch(self.root/'batch',FIXTURE)
            self.assertEqual(net.call_count,1)
        value=entry.read(self.root/'batch/checkpoint-01.json')
        self.assertEqual(value['model_calls_upper'],1)
        receipt=entry.read(next((self.root/'batch').glob('*/response-receipt.json')))
        self.assertEqual(receipt['collection_phase'],'RESPONSE_RECEIVED_PERSISTENCE_PENDING')
        self.assertEqual(receipt['audit']['model_calls_lower'],1)
        self.assertTrue((entry.CLAIM_ROOT/entry.scope_digest()).exists())

    def check_response_exception_matrix(self, stage, known=False):
        errors=(KeyboardInterrupt,EOFError,OSError,http.client.HTTPException,EvaluationError,ValueError,RuntimeError)
        for error in errors:
            with self.subTest(stage=stage,error=error.__name__),tempfile.TemporaryDirectory(dir=self.root) as temp:
                root=Path(temp)
                def fail(*args,**kwargs):raise error(FIXTURE)
                target='collection_policy' if stage=='before_policy' else 'import_response'
                if stage=='transport':target='_https_once'
                if known:
                    from dataclasses import replace
                    imported=entry.import_response(response()[1].decode(),requested_model=ACCOUNT_MODEL)
                    class InterruptedSource(str):
                        count=0
                        def encode(self,*args,**kwargs):
                            self.count+=1
                            if self.count==2:raise error(FIXTURE)
                            return super().encode(*args,**kwargs)
                    fault=patch.object(entry,'import_response',return_value=replace(imported,candidate=InterruptedSource(imported.candidate)))
                else:fault=patch.object(entry,target,side_effect=fail)
                with patch.object(entry,'CLAIM_ROOT',root/'claims'),patch.object(entry,'_https_once',return_value=response()) as net,fault:
                    report=entry.run_batch(root/'batch',FIXTURE)
                # For transport faults the inner replacement holds the call count.
                self.assertEqual(report['request_attempts_reserved'],1)
                if stage!='transport':self.assertEqual(net.call_count,1)
                row=report['slots'][0]
                self.assertFalse(row['continue_fixed_schedule']);self.assertIsNone(row['candidate_file'])
                self.assertEqual(row['model_calls_lower'],int(known));self.assertEqual(row['model_calls_upper'],1)
                self.assertNotEqual(row['generation_status'],'COLLECTED_UNREVIEWED')
                self.assertEqual(report['state'],'STOPPED_NO_RETRY')
                self.assertEqual([r['generation_status'] for r in report['slots'][1:]],['NOTRUN']*23)
                self.assertFalse(any((root/'batch').glob('*/candidate.txt')))
                for path in root.rglob('*'):
                    if path.is_file():self.assertNotIn(FIXTURE,path.read_text())

    def test_exceptions_before_policy_halt_unknown_no_candidate(self):
        self.check_response_exception_matrix('before_policy')

    def test_exceptions_after_policy_halt_unknown_no_candidate(self):
        self.check_response_exception_matrix('after_policy')

    def test_exceptions_after_known_usage_halt_retain_known_count(self):
        self.check_response_exception_matrix('after_usage',known=True)

    def test_transport_exception_matrix_halts_no_retry(self):
        self.check_response_exception_matrix('transport')

    def test_persistence_exception_matrix_halts_preserves_receipt_and_audits(self):
        original_write=entry.write_new;original_open=Path.open
        errors=(KeyboardInterrupt,EOFError,OSError,http.client.HTTPException,EvaluationError,ValueError,RuntimeError)
        for stage in ('reservation','checkpoint','receipt','candidate','audit'):
            for error in errors:
                with self.subTest(stage=stage,error=error.__name__),tempfile.TemporaryDirectory(dir=self.root) as temp:
                    root=Path(temp)
                    def write(path,data):
                        name=Path(path).name
                        if ((stage=='reservation' and name=='reservation.json')
                            or (stage=='checkpoint' and name=='checkpoint-01.json')
                            or (stage=='receipt' and name=='response-receipt.json')
                            or (stage=='audit' and name=='audit.json')):raise error(FIXTURE)
                        original_write(path,data)
                    def opened(path,*args,**kwargs):
                        if stage=='candidate' and path.name=='candidate.txt':raise error(FIXTURE)
                        return original_open(path,*args,**kwargs)
                    with patch.object(entry,'CLAIM_ROOT',root/'claims'),patch.object(entry,'_https_once',return_value=response()) as net,patch.object(entry,'write_new',side_effect=write),patch.object(Path,'open',opened):
                        with self.assertRaises(error):entry.run_batch(root/'batch',FIXTURE)
                    self.assertEqual(net.call_count,0 if stage in ('reservation','checkpoint') else 1)
                    self.assertFalse((root/'batch/summary.json').exists())
                    self.assertTrue((root/'claims'/entry.scope_digest()/'start.json').exists())
                    for path in (root/'batch').glob('*/notrun-initial.json'):
                        self.assertEqual(entry.read(path)['generation_status'],'NOTRUN')
                    checkpoint=root/'batch/checkpoint-01.json'
                    if checkpoint.exists():
                        value=entry.read(checkpoint)
                        self.assertEqual((value['model_calls_lower'],value['model_calls_upper']),(0,1))
                    receipts=list((root/'batch').glob('*/response-receipt.json'))
                    if stage in ('candidate','audit'):
                        self.assertEqual(len(receipts),1)
                        row=entry.read(receipts[0])['audit']
                        self.assertEqual((row['model_calls_lower'],row['model_calls_upper']),(1,1))
                    for path in root.rglob('*'):
                        if path.is_file():self.assertNotIn(FIXTURE,path.read_text())
                    with patch.object(entry,'CLAIM_ROOT',root/'claims'),patch.object(entry,'_https_once') as again:
                        with self.assertRaises(EvaluationError):entry.run_batch(root/'other',FIXTURE)
                        again.assert_not_called()

    def test_outer_error_handler_never_claims_completion_or_prints_exception_text(self):
        errors=(KeyboardInterrupt,EOFError,OSError,http.client.HTTPException,EvaluationError,ValueError,RuntimeError)
        for error in errors:
            with self.subTest(error=error.__name__),contextlib.redirect_stdout(io.StringIO()) as stdout,patch.object(entry.sys.stdin,'isatty',return_value=True),patch.object(entry.sys.stdout,'isatty',return_value=True),patch('builtins.input',return_value=entry.CONFIRMATION),patch.object(entry,'read_local_key',return_value=FIXTURE),patch.object(entry,'run_batch',side_effect=error(FIXTURE)),patch.object(entry,'_https_once') as net:
                self.assertEqual(entry.main(['--user-local-send-pilot24','--plan-status-confirmed','--key-file','PUBLIC-UNREAD-FIXTURE','--output',str(self.root/'outer-error')]),2)
                net.assert_not_called()
            self.assertNotIn(FIXTURE,stdout.getvalue())
            record=json.loads(stdout.getvalue().splitlines()[-1])
            self.assertIn(record['state'],{'NOTRUN','BLOCKED','CANCELLED'})


if __name__=='__main__':unittest.main()
