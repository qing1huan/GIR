"""Only mocked admission/transport and public engineering fixtures; no workers."""
import copy,hashlib,io,json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from ark_eval.adapter import EvaluationError
from ark_eval import v4_scope as s,v4_eval as entry
from ark_eval.v4_backend import V4IRBackend,v4_payload
from ark_eval.node_backend import NodeBackend
from ark_eval.v4_runner import V4JSRunner
from ark_eval.container_profile import image_lock
from headless_v01.compiler import digest

def approved(manifest):
    a=s.approval_template(manifest);a.update(decision='APPROVE_EXACT_V4_136',human_explicit_approval=True,human_statement='PUBLIC MOCK APPROVAL ONLY')
    return a

class V4ExecutionTests(unittest.TestCase):
    def scope(self):
        m=s.load_manifest();return s.ApprovedV4Scope(m,approved(m))

    def test_exact_sources_order_and_independent_cost_oracles(self):
        m=s.load_manifest();p=s.proposal();cases=s.case_sets()
        self.assertEqual(digest(p),s.PROPOSAL_DIGEST)
        self.assertEqual(m['order'],[list(k) for k in s.ORDER]);self.assertEqual(len(m['source_sha256']),4)
        self.assertEqual(sum(len(v) for v in cases.values()),136)
        for task,rep in s.ORDER:
            self.assertEqual([g for g,c in cases[task,rep]].count('legacy26'),26)
            self.assertEqual([g for g,c in cases[task,rep]].count('diagnostic8'),8)
            self.assertEqual({c['seed'] for g,c in cases[task,rep]},{1,123})
            self.assertEqual(digest(cases[task,rep]),m['case_digests'][task+'/'+rep])
        # Fixed predeclared expected states differ by task, never derive from candidates.
        modify=[c for g,c in cases['P-C02-modify','gameir_text'] if g=='legacy26']
        debug=[c for g,c in cases['P-C02-debug','gameir_text'] if g=='legacy26']
        self.assertNotEqual([c['expected'] for c in modify],[c['expected'] for c in debug])

    def test_pending_and_old_authorizations_cannot_authorize(self):
        m=s.load_manifest()
        for a in (s.approval_template(m),{}, {'human_approval_received':True,'schema':'gir-exact-candidate-execution-scope@1'}):
            with self.assertRaises(EvaluationError):s.ApprovedV4Scope(m,a)
        for field,value in [('scope_digest','0'*64),('decision','APPROVE_REPAIR001'),('source_sha256',{}),('human_explicit_approval',1)]:
            a=approved(m);a[field]=value
            with self.assertRaises(EvaluationError):s.ApprovedV4Scope(m,a)

    def test_boolean_integer_substitution_in_freeze_and_case_rejected(self):
        scope=self.scope();task,rep=s.ORDER[0]
        group,case=copy.deepcopy(scope.cases[task,rep][0]);self.assertEqual(case['seed'],1)
        case['seed']=True
        with self.assertRaisesRegex(EvaluationError,'UNKNOWN_CASE'):scope.case(task,rep,group,case)
        bad=copy.deepcopy(scope.manifest);bad['requires_explicit_human_approval']=1
        original=s.read
        with patch.object(s,'read',side_effect=lambda p:bad if p==s.FREEZE else original(p)):
            with self.assertRaisesRegex(EvaluationError,'BINDING_DRIFT'):s.load_manifest()

    def test_scope_code_bundle_cases_and_approval_drift_block(self):
        scope=self.scope();scope.require()
        with patch.object(s,'bundle_hashes',return_value={}):
            with self.assertRaisesRegex(EvaluationError,'SCOPE_CHANGED'):scope.require()
        with patch.object(s,'sha',return_value='0'*64):
            with self.assertRaisesRegex(EvaluationError,'SCOPE_CHANGED'):scope.require()
        scope.cases[s.ORDER[0]][0][1]['expected']={}
        with self.assertRaisesRegex(EvaluationError,'SCOPE_CHANGED'):scope.require()
        scope=self.scope();scope.approval['human_explicit_approval']=False
        with self.assertRaisesRegex(EvaluationError,'SCOPE_CHANGED'):scope.require()

    def test_wrong_order_and_frozen_binding_changes_rejected(self):
        p=copy.deepcopy(s.proposal());p['arms'][2:]=reversed(p['arms'][2:])
        with patch.object(s,'proposal',return_value=p):
            with self.assertRaisesRegex(EvaluationError,'ORDER_DRIFT'):s.make_manifest()
        with patch.object(s,'current_bindings',return_value={}):
            with self.assertRaisesRegex(EvaluationError,'BINDING_DRIFT'):s.load_manifest()

    def test_actual_source_hash_change_rejected_without_execution(self):
        scope=self.scope()
        with patch.object(scope,'require'),patch.object(Path,'read_bytes',return_value=b'public changed fixture'):
            with self.assertRaisesRegex(EvaluationError,'SOURCE_CHANGED'):scope.source(*s.ORDER[0])
        with self.assertRaises(EvaluationError):scope.case(*s.ORDER[0],'legacy26',{})

    def test_default_prepares136_without_backend_or_approval_read(self):
        with tempfile.TemporaryDirectory() as root,patch.object(entry,'NodeBackend') as node,patch.object(entry,'V4IRBackend') as ir,patch.object(entry,'run') as run,patch('sys.stdout',new=io.StringIO()):
            out=Path(root)/'preview';self.assertEqual(entry.main(['--output',str(out)]),0)
            node.assert_not_called();ir.assert_not_called();run.assert_not_called()
            self.assertEqual(len(json.loads((out/'notrun-initial.json').read_text())),136)
            self.assertFalse(json.loads((out/'approval-template-NOT-APPROVED.json').read_text())['human_explicit_approval'])

    def test_flag_without_explicit_receipt_cannot_start(self):
        with tempfile.TemporaryDirectory() as root,patch.object(entry,'APPROVAL_FILE',Path(root)/'absent-approval.json'),patch.object(entry,'NodeBackend') as node,patch.object(entry,'V4IRBackend') as ir,patch.object(entry,'run') as run,patch('sys.stdout',new=io.StringIO()):
            self.assertEqual(entry.main(['--run-approved-v4','--output',str(Path(root)/'run')]),2)
            node.assert_not_called();ir.assert_not_called();run.assert_not_called()

    def test_existing_output_preserved_without_workers(self):
        scope=self.scope()
        with tempfile.TemporaryDirectory() as root,patch.object(entry,'CLAIM_ROOT',Path(root)/'claims'),patch.object(entry,'NodeBackend') as node:
            marker=Path(root)/'keep';marker.write_text('preserve')
            with self.assertRaises(FileExistsError):entry.run(scope,Path(root))
            self.assertEqual(marker.read_text(),'preserve');node.assert_not_called()
            self.assertFalse(entry.CLAIM_ROOT.exists())

    def proof(self,rep):
        scope=self.scope();backend=object.__new__(NodeBackend if rep=='js_callback' else V4IRBackend)
        backend.disabled=False;backend.worker_image='public-fixture-image';backend.worker_hash='public-fixture-hash'
        names=scope.manifest['isolation']['profile_checks'];profile={'checks':{n:'PASS' for n in names}}
        faults=[{'fixture':n,'status':'PASS'} for n in scope.manifest['isolation']['JS_fault_prechecks' if rep=='js_callback' else 'IR_fault_prechecks']]
        return scope,backend,profile,faults

    def test_stale_changed_backend_and_failed_isolation_block(self):
        for rep in ('gameir_text','js_callback'):
            scope,b,p,f=self.proof(rep);proof=s.FreshV4Isolation(scope,b,p,f,rep)
            proof.created-=601
            with self.assertRaisesRegex(EvaluationError,'STALE'):proof.require(b)
            proof=s.FreshV4Isolation(scope,b,p,f,rep);b.worker_image='changed'
            with self.assertRaisesRegex(EvaluationError,'STALE'):proof.require(b)
            f[0]['status']='FAIL'
            with self.assertRaisesRegex(EvaluationError,'ISOLATION_REJECTED'):s.FreshV4Isolation(scope,b,p,f,rep)
            with self.assertRaises(EvaluationError):V4JSRunner(scope,s.ORDER[0][0],b,object(),{})

    def test_hash_only_worker_configuration_and_image_cache_reused(self):
        scope=self.scope();task=s.ORDER[0][0];template=(s.ROOT/'ark_eval/pair003_ir_worker.py').read_bytes()
        old=b"SOURCE_SHA256='9cd7ea46ba86790a5413c78e5bd5846c538b75cfb65f6713c5596a47f1e87b42'"
        new=("SOURCE_SHA256='"+scope.manifest['source_sha256'][task+'/gameir_text']+"'").encode()
        with patch('ark_eval.v4_backend.image_payload',return_value={'gir-ir-worker.py':template,'public':b'unchanged'}):
            payload=v4_payload(scope,task,{})
        self.assertEqual(payload['gir-ir-worker.py'],template.replace(old,new));self.assertEqual(payload['public'],b'unchanged')
        backend=object.__new__(V4IRBackend);backend.scope=scope;backend.task=task;backend.environment={};backend.lock=image_lock()
        hashes={n:hashlib.sha256(b).hexdigest() for n,b in sorted(payload.items())}
        fp=hashlib.sha256((json.dumps(hashes,sort_keys=True)+backend.lock['repository_digest']).encode()).hexdigest()
        with patch('ark_eval.v4_backend.v4_payload',return_value=payload),patch('ark_eval.v4_backend.subprocess.run',return_value=SimpleNamespace(returncode=0)),patch.object(backend,'_ctl',return_value=json.dumps([{'Labels':{'gir.worker.source':fp},'Id':'sha256:public-mock-image'}])) as ctl:
            self.assertEqual(backend._build(),'sha256:public-mock-image')
            self.assertEqual(ctl.call_count,1);self.assertEqual(ctl.call_args.args[:2],('image','inspect'))

    def test_parser_admission_requires_exact_hash_and_kernel_proof(self):
        scope=self.scope();task='P-C02-modify'
        fp=hashlib.sha256((s.ROOT/'ark_eval/candidate_parser.mjs').read_bytes()+(s.ROOT/'ark_eval/node_supervisor.sh').read_bytes()+image_lock()['repository_digest'].encode()).hexdigest()
        e={'result':{'status':'PARSED','exports':['onOpen'],'static_imports':[],
            'source_sha256':scope.manifest['source_sha256'][task+'/js_callback'],'top_level_evaluated':False,'callback_invoked':False},
            'candidate_executions':0,'worker_audit':{'worker_source_sha256':fp,'cleanup_errors':[],
            'surviving_owned_processes':0,'network_mode':'none','readonly_rootfs':True,'user_mounts':[],
            'generated_source_executions':0,'rootless_host':{'rootless':True},
            'kernel_security':{'CapEff':'0000000000000000','NoNewPrivs':'1','Seccomp':'2'},
            'kernel_limits':{'cpu.max':'25000 100000','memory.max':'67108864','memory.swap.max':'0','pids.max':'32'}}}
        s.require_parse(scope,task,e)
        for key,value in [('source_sha256','0'*64),('top_level_evaluated',True),('exports',['extra'])]:
            bad=copy.deepcopy(e);bad['result'][key]=value
            with self.assertRaisesRegex(EvaluationError,'PARSE_REJECTED'):s.require_parse(scope,task,bad)

    def test_mock_precheck_failure_stops136_and_no_candidate_launch(self):
        scope=self.scope()
        with tempfile.TemporaryDirectory() as root,patch.object(entry,'CLAIM_ROOT',Path(root)/'claims'),patch.object(entry,'V4IRBackend',return_value=SimpleNamespace(_ctl=lambda *a:'[]')),patch.object(entry,'NodeBackend') as node,patch.object(entry,'fresh',side_effect=EvaluationError('PUBLIC MOCK FAILURE')),patch.object(entry,'evaluate_ir') as ir,patch.object(entry,'evaluate_js') as js:
            summary=entry.run(scope,Path(root)/'run')
            self.assertEqual(summary['state'],'STOPPED_ON_BLOCK');node.assert_not_called();ir.assert_not_called();js.assert_not_called()
            self.assertEqual(sum(v['NOTRUN'] for t in summary['counts'].values() for a in t.values() for v in a.values()),136)
            self.assertEqual([p['status'] for p in summary['prechecks']],['INCOMPLETE','NOTRUN','NOTRUN','NOTRUN'])
            self.assertEqual(summary['unknown_execution_records'],0)
            self.assertTrue(summary['claim_consumed'])

    def test_mock_dispatch136_order_groups_replays_and_midrun_failstop(self):
        for fail_at in (None,2):
            scope=self.scope();calls=[]
            def result(task,rep,group,case):
                if len(calls)==fail_at:raise EvaluationError('PUBLIC MOCK FAILURE')
                calls.append((task,rep,group,case['id'],case['seed']))
                return {'status':'PASS','candidate_executions':1,
                    'IR_case_replays' if rep=='gameir_text' else 'JS_transcript_replays':1}
            fake=SimpleNamespace(_ctl=lambda *a:'[]',worker_image='public-mock-image',worker_hash='public-mock-hash',payload_hashes={})
            receipt=SimpleNamespace(created=__import__('time').monotonic(),require=lambda b:None)
            with tempfile.TemporaryDirectory() as root,patch.object(entry,'CLAIM_ROOT',Path(root)/'claims'),patch.object(entry,'V4IRBackend',return_value=fake),patch.object(entry,'NodeBackend',return_value=fake),patch.object(entry,'fresh',return_value=receipt),patch.object(entry,'CandidateParserBackend'),patch.object(entry,'parse_module',return_value={}),patch.object(entry,'require_parse'),patch.object(entry,'evaluate_ir',side_effect=lambda sc,t,b,r,g,c:result(t,'gameir_text',g,c)),patch.object(entry,'evaluate_js',side_effect=lambda sc,t,b,r,p,g,c:result(t,'js_callback',g,c)):
                summary=entry.run(scope,Path(root)/'run')
                expected=[(t,r,g,c['id'],c['seed']) for t,r in s.ORDER for g,c in scope.cases[t,r]]
                self.assertEqual(calls,expected if fail_at is None else expected[:fail_at])
                self.assertEqual(summary['state'],'COMPLETE' if fail_at is None else 'STOPPED_ON_BLOCK')
                self.assertEqual(summary['completed_record_candidate_executions'],136 if fail_at is None else fail_at)
                if fail_at is None:
                    self.assertEqual(summary['IR_case_replays'],68);self.assertEqual(summary['JS_transcript_replays'],68)
                    self.assertTrue(all(p['status']=='PASS' for p in summary['prechecks']))
                else:
                    self.assertEqual(summary['unknown_execution_records'],1)
                    self.assertEqual([p['status'] for p in summary['prechecks']],['PASS','NOTRUN','NOTRUN','NOTRUN'])
                rows=json.loads((Path(root)/'run/cases.json').read_text())
                self.assertTrue(all(r['candidate_phase']=='COMPLETED' for r in rows[:len(calls)]))
                if fail_at is not None:
                    self.assertEqual(rows[fail_at]['candidate_phase'],'RESERVED_EXECUTION_UNCONFIRMED')
                    self.assertTrue(all(r['candidate_phase']=='NOT_STARTED' for r in rows[fail_at+1:]))

    def test_cross_output_repeat_rejected_after_precheck_failure(self):
        scope=self.scope()
        with tempfile.TemporaryDirectory() as root,patch.object(entry,'CLAIM_ROOT',Path(root)/'claims'),patch.object(entry,'V4IRBackend',return_value=SimpleNamespace(_ctl=lambda *a:'[]')) as backend,patch.object(entry,'fresh',side_effect=EvaluationError('PUBLIC MOCK FAILURE')):
            first=entry.run(scope,Path(root)/'one');self.assertEqual(first['state'],'STOPPED_ON_BLOCK')
            with self.assertRaisesRegex(EvaluationError,'ALREADY_CLAIMED'):entry.run(self.scope(),Path(root)/'two')
            self.assertEqual(backend.call_count,1);self.assertFalse((Path(root)/'two').exists())
            events=[json.loads(p.read_text())['state'] for p in sorted(Path(first['claim_directory']).glob('event-*.json'))]
            self.assertEqual(events,['PRECHECK_STARTED','ADMISSION_OR_PRECHECK_INCOMPLETE','STOPPED_ON_BLOCK'])

    def test_concurrent_cross_directory_claim_has_one_winner(self):
        scope=self.scope();barrier=Barrier(2)
        with tempfile.TemporaryDirectory() as root,patch.object(entry,'CLAIM_ROOT',Path(root)/'claims'),patch.object(entry,'NodeBackend') as node,patch.object(entry,'V4IRBackend') as ir:
            def claim(n):
                barrier.wait()
                try:return entry.ExecutionClaim(scope,Path(root)/str(n)).path
                except EvaluationError as e:return str(e)
            with ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(claim,(1,2)))
            self.assertEqual(sum(isinstance(o,Path) for o in outcomes),1)
            self.assertEqual(outcomes.count('V4_SCOPE_ALREADY_CLAIMED'),1)
            self.assertEqual(len(list(entry.CLAIM_ROOT.iterdir())),1);node.assert_not_called();ir.assert_not_called()

    def test_invalid_readonly_scope_or_parent_does_not_claim(self):
        scope=self.scope()
        with tempfile.TemporaryDirectory() as root,patch.object(entry,'CLAIM_ROOT',Path(root)/'claims'),patch.object(entry,'NodeBackend') as node,patch.object(entry,'V4IRBackend') as ir:
            with patch.object(scope,'require',side_effect=EvaluationError('PUBLIC MOCK DRIFT')):
                with self.assertRaises(EvaluationError):entry.run(scope,Path(root)/'one')
            file=Path(root)/'file';file.write_text('public fixture')
            with self.assertRaisesRegex(EvaluationError,'PARENT_REJECTED'):entry.run(scope,file/'out')
            self.assertFalse(entry.CLAIM_ROOT.exists());node.assert_not_called();ir.assert_not_called()

    def test_post_claim_output_write_failure_consumes_scope_without_backend(self):
        scope=self.scope();original=entry.write_json
        with tempfile.TemporaryDirectory() as root,patch.object(entry,'CLAIM_ROOT',Path(root)/'claims'),patch.object(entry,'NodeBackend') as node,patch.object(entry,'V4IRBackend') as ir:
            out=Path(root)/'one'
            def write(path,value):
                if path==out/'scope.json':raise OSError('PUBLIC MOCK WRITE FAILURE')
                return original(path,value)
            with patch.object(entry,'write_json',side_effect=write):
                with self.assertRaises(OSError):entry.run(scope,out)
            with self.assertRaisesRegex(EvaluationError,'ALREADY_CLAIMED'):entry.run(scope,Path(root)/'two')
            claim=entry.CLAIM_ROOT/digest(scope.manifest)
            self.assertEqual(json.loads((claim/'event-0001.json').read_text())['state'],'INCOMPLETE_DO_NOT_RETRY')
            node.assert_not_called();ir.assert_not_called()

    def test_failed_reservation_write_records_zero_candidate_starts(self):
        scope=self.scope();original=entry.write_json
        fake=SimpleNamespace(_ctl=lambda *a:'[]',worker_image='public-mock',worker_hash='public-mock',payload_hashes={})
        receipt=SimpleNamespace(created=__import__('time').monotonic(),require=lambda b:None)
        with tempfile.TemporaryDirectory() as root,patch.object(entry,'CLAIM_ROOT',Path(root)/'claims'),patch.object(entry,'V4IRBackend',return_value=fake),patch.object(entry,'fresh',return_value=receipt),patch.object(entry,'evaluate_ir') as candidate:
            def write(path,value):
                if path.name=='reservation-1.json':raise OSError('PUBLIC MOCK RESERVATION FAILURE')
                return original(path,value)
            with patch.object(entry,'write_json',side_effect=write):summary=entry.run(scope,Path(root)/'run')
            candidate.assert_not_called();self.assertEqual(summary['unknown_execution_records'],0)
            rows=json.loads((Path(root)/'run/cases.json').read_text())
            self.assertTrue(all(r['candidate_executions']==0 and r['candidate_phase']=='NOT_STARTED' for r in rows))

    def test_claim_record_write_failure_still_blocks_reuse(self):
        scope=self.scope();original=entry.write_json
        with tempfile.TemporaryDirectory() as root,patch.object(entry,'CLAIM_ROOT',Path(root)/'claims'),patch.object(entry,'V4IRBackend') as backend:
            def write(path,value):
                if path.name=='claim.json':raise OSError('PUBLIC MOCK CLAIM WRITE FAILURE')
                return original(path,value)
            with patch.object(entry,'write_json',side_effect=write):
                with self.assertRaises(OSError):entry.run(scope,Path(root)/'one')
            with self.assertRaisesRegex(EvaluationError,'ALREADY_CLAIMED'):entry.run(scope,Path(root)/'two')
            self.assertTrue((entry.CLAIM_ROOT/digest(scope.manifest)).is_dir());backend.assert_not_called()

if __name__=='__main__':unittest.main()
