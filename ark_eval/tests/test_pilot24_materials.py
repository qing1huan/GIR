import copy
import json
import unittest
import tempfile
from pathlib import Path

from headless_v01.compiler import compile_program
from ark_eval.adapter import EvaluationError
from ark_eval.pilot24_authored import TASKS,ROOT,abi,ir
from ark_eval.pilot24_materials import preview,request,metrics,audit_preview,read,public_paths,collection_policy,candidate_check_policy,freeze
from ark_eval.pilot24_trusted import authored_source,authored_path

class MaterialTests(unittest.TestCase):
    def test_exact_72_scene_ids_and_groups(self):
        for task in TASKS:
            cases=read(ROOT/'private'/task/'cases.json')
            self.assertEqual([c['id'] for c in cases],[f'{g}{i:02}' for g in 'NBR' for i in range(1,5)])
            self.assertTrue(all(c['task_id']==task and c['seed']==1 for c in cases))

    def test_oracle_ticks_explicit_complete(self):
        for task in TASKS:
            for filename in ('cases.json','v0_cases.json'):
                for c in read(ROOT/'private'/task/filename):
                    self.assertEqual(len(c['steps']),len(c['expected_ticks']))
                    for tick in c['expected_ticks']:
                        self.assertEqual(set(tick),{'entities','status','error','events','commands'})
                        self.assertEqual(tick['commands'],[])
                        if tick['status']=='ABORTED':self.assertEqual(tick['events'],[]);self.assertEqual(tick['error'],'InvalidEntity')

    def test_all_fixed_heads_compile_and_are_identical(self):
        for t in TASKS:
            for version in (0,1):
                for mutant in ((0,) if version==0 else (0,1,2,3)):
                    program=authored_source(t,version,'gameir_text',mutant)
                    compile_program(program)
                    head=copy.deepcopy(program);head['handlers'][0]['body']=[]
                    self.assertEqual(head,read(ROOT/'public'/t/'abi.json'))
                    self.assertEqual(head,abi(t))

    def test_sources_exact_authored_only(self):
        for t in TASKS:
            for rep in ('gameir_text','js_callback'):
                for version,mutant in ((0,0),(1,0),(1,1),(1,2),(1,3)):
                    self.assertIsNotNone(authored_source(t,version,rep,mutant))
        for bad in ('../env.txt','candidate','M3'):
            with self.assertRaises(EvaluationError):authored_path(bad,1,'js_callback',0)
        with self.assertRaises(EvaluationError):authored_path('M1',0,'js_callback',1)

    def test_each_task_has_three_paired_defect_intents(self):
        for t in TASKS:
            defects=read(ROOT/'private'/t/'mutants/index.json')
            self.assertEqual([d['index'] for d in defects],[1,2,3])
            self.assertTrue(any(d['case_id'].startswith('R') for d in defects))
            for d in defects:
                self.assertTrue(authored_path(t,1,'gameir_text',d['index']).is_file())
                self.assertTrue(authored_path(t,1,'js_callback',d['index']).is_file())

    def test_starters_no_TODO_or_private_markers(self):
        for t in TASKS:
            for rep in ('gameir_text','js_callback'):
                text=public_paths(t,rep)[-1].read_text(encoding='utf-8')
                self.assertNotIn('TODO',text);self.assertNotIn('expected_ticks',text)

    def test_public_builder_only_five_explicit_paths(self):
        for t in TASKS:
            for rep in ('gameir_text','js_callback'):
                paths=public_paths(t,rep);self.assertEqual(len(paths),5)
                self.assertTrue(all(p.is_relative_to(ROOT/'public') for p in paths))
                req=request(t,rep)
                self.assertEqual(len(req.body['messages']),2)
                self.assertEqual(req.body['messages'][0]['content'],paths[0].read_text(encoding='utf-8')+'\n'+paths[1].read_text(encoding='utf-8'))

    def test_same_requirements_and_ABI_in_both_messages(self):
        for t in TASKS:
            shared=[(ROOT/'public'/t/n).read_text(encoding='utf-8') for n in ('requirements.md','abi.json')]
            for rep in ('gameir_text','js_callback'):
                text=request(t,rep).body['messages'][1]['content']
                for fact in shared:self.assertIn(fact,text)

    def test_plan_and_budget_exact(self):
        for t in TASKS:
            for rep in ('gameir_text','js_callback'):
                r=request(t,rep);self.assertLessEqual(r.prompt_units,24000)
                self.assertEqual(r.body['model'],'deepseek-v4-1-flash-260910')
                self.assertEqual(r.body['max_tokens'],4096);self.assertEqual(r.body['temperature'],0)
                self.assertEqual(r.body['stream'],False);self.assertEqual(r.body['thinking'],{'type':'disabled'})

    def test_24_slots_two_repeats_balanced_reverse(self):
        p=preview();self.assertEqual(len(p['arms']),12);self.assertEqual(len(p['slots']),24)
        pairs={(t,r):[s for s in p['slots'] if s['task_id']==t and s['repetition']==r] for t in TASKS for r in (1,2)}
        for t in TASKS:
            self.assertEqual([s['request_digest'] for s in pairs[t,1]],list(reversed([s['request_digest'] for s in pairs[t,2]])))
        for r in (1,2):self.assertEqual(sum(pairs[t,r][0]['representation']=='gameir_text' for t in TASKS),3)
        self.assertEqual([s['task_id'] for s in p['slots'][::2]],list(TASKS)+list(reversed(TASKS)))

    def test_no_send_or_execution_entry_enabled(self):
        p=preview();self.assertIs(p['send_enabled'],False);self.assertIs(p['candidate_execution_enabled'],False)
        self.assertEqual((p['repairs'],p['tool_calls'],p['automatic_retries'],p['maximum_output_tokens']),(0,0,0,98304))

    def test_current_messages_no_reference_or_counterpart_leaks(self):self.assertTrue(audit_preview(preview()))

    def test_reference_leak_fixture_rejected(self):
        p=preview();p['arms'][0]['body']['messages'][1]['content']+='\n'+(ROOT/'private/M1/control_v1.js').read_text(encoding='utf-8')
        with self.assertRaises(EvaluationError):audit_preview(p)

    def test_counterpart_leak_fixture_rejected(self):
        p=preview();p['arms'][0]['body']['messages'][1]['content']+='\n'+(ROOT/'public/M1/starter.js').read_text(encoding='utf-8')
        with self.assertRaises(EvaluationError):audit_preview(p)

    def test_private_case_leak_fixture_rejected(self):
        p=preview();p['arms'][0]['body']['messages'][1]['content']+='\nN01 expected_ticks'
        with self.assertRaises(EvaluationError):audit_preview(p)

    def test_output_material_size_and_heuristic_margin(self):
        rows=metrics();self.assertEqual(len(rows),24)
        for r in rows:
            self.assertLessEqual(r['bytes'],16384)
            self.assertEqual(r['actual_output_token_fit'],'NOT_VERIFIED')
            if r['representation']=='gameir_text':
                self.assertLessEqual(r['IR_op_nodes'],100);self.assertLessEqual(r['bytes'],10000)
                self.assertLessEqual(r['planning_output_tokens_byte_divisor_3'],3072)

    def test_preserved_oracles_same_v0_v1(self):
        for t in TASKS:
            original={c['id']:c for c in read(ROOT/'private'/t/'v0_cases.json')}
            for c in read(ROOT/'private'/t/'cases.json'):
                if c['group']=='R':self.assertEqual(original[c['id']],c)

    def mock_response(self,content='mock inert text',finish='stop',**extra):
        data={'model':'deepseek-v4-1-flash-260910','choices':[{'message':{'role':'assistant','content':content},'finish_reason':finish}],
              'usage':{'prompt_tokens':100,'completion_tokens':10,'total_tokens':110}}
        data.update(extra);return json.dumps(data)

    def test_verified_truncation_consumes_no_repair_or_execution(self):
        r=collection_policy(200,self.mock_response(finish='length'))
        self.assertEqual(r['generation_status'],'FAIL_TRUNCATED');self.assertTrue(r['continue_fixed_schedule'])
        self.assertFalse(r['retry']);self.assertFalse(r['execute_source']);self.assertEqual(r['case_statuses'],['NOTRUN']*12)

    def test_verified_empty_is_known_quality_failure(self):
        r=collection_policy(200,self.mock_response(content=''))
        self.assertEqual(r['generation_status'],'FAIL_EMPTY');self.assertTrue(r['continue_fixed_schedule'])

    def test_unknown_timeout_auth_quota_service_stop(self):
        for status in (None,401,429,500):
            r=collection_policy(status,'mock error')
            self.assertFalse(r['continue_fixed_schedule']);self.assertFalse(r['retry'])

    def test_identity_usage_or_thinking_unknown_stop(self):
        for extra in ({'model':'wrong-model'},{'usage':{'prompt_tokens':100,'completion_tokens':10,'total_tokens':111}}):
            r=collection_policy(200,self.mock_response(**extra));self.assertFalse(r['continue_fixed_schedule'])
        data=json.loads(self.mock_response());data['choices'][0]['message']['reasoning_content']='unexpected'
        self.assertFalse(collection_policy(200,json.dumps(data))['continue_fixed_schedule'])

    def test_worker_crash_not_guessed_as_candidate_defect(self):
        for reason in ('WORKER_CRASH','ISOLATION_UNKNOWN','CLEANUP_FAILURE','SYNTAX'):
            self.assertFalse(candidate_check_policy(reason)['continue_fixed_schedule'])
        self.assertTrue(candidate_check_policy('SYNTAX',True)['continue_fixed_schedule'])

    def test_valid_collection_is_not_gameplay_PASS_or_execution(self):
        r=collection_policy(200,self.mock_response())
        self.assertEqual(r['generation_status'],'COLLECTED_UNREVIEWED');self.assertFalse(r['execute_source'])

    def test_freeze_CLI_string_path_and_stale_material_rejected(self):
        existing=(ROOT/'freeze.json').read_bytes() if (ROOT/'freeze.json').exists() else None
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'synthetic-validation.json'
            p.write_text(json.dumps({'status':'PASS','real_model_calls':0,'material_sha256':{}}),encoding='utf-8')
            with self.assertRaisesRegex(EvaluationError,'PILOT24_VALIDATION_MATERIAL_DRIFT'):freeze(str(p))
        self.assertEqual((ROOT/'freeze.json').read_bytes() if (ROOT/'freeze.json').exists() else None,existing)

if __name__=='__main__':unittest.main()
