"""V4 trusted-engine controls and denial checks, never model candidate execution."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from headless_v01.compiler import compile_program, digest
from headless_v01.runtime import Runtime, replay
from ark_eval.adapter import EvaluationError
from ark_eval.door_v4 import (ROOT,DATA,FILES,ARMS,TASKS,BUSINESS,COST,public_arms,
    diagnostic_document,read_json,make_protocol,load_freeze,verify_history,send,
    execute_candidate,write_review_bundle)
from ark_eval.oracle import observations
from ark_eval.callback_verify import callback_manifest
from ark_eval.host import HostRuntime,DoorMockRunner,replay_host


def nodes(value):
    if isinstance(value,dict):
        yield value
        for v in value.values():yield from nodes(v)
    elif isinstance(value,list):
        for v in value:yield from nodes(v)


def trusted_target(task):
    """Evaluator-authored starter edits for engine tests; never public answers."""
    program=read_json(DATA/FILES[task,'gameir_text'])
    for n in nodes(program):
        if n.get('op')=='let' and n.get('name')=='cost':n['value']['value']=COST[task]
        if n.get('op')=='le':n['op']='ge'
    return program


class DoorV4Tests(unittest.TestCase):
    def test_freeze_route_budget_order_and_both_denials(self):
        p=load_freeze();self.assertEqual(p,make_protocol())
        self.assertFalse(p['send_enabled']);self.assertFalse(p['candidate_execution_enabled'])
        self.assertEqual(p['independent_sessions'],4);self.assertEqual(p['maximum_batch_attempts'],4)
        self.assertEqual(p['budget_per_arm'],{'input_byte_units':24000,'output_tokens':4096,'attempts':1,'repairs':0,'tool_calls':0})
        self.assertEqual(p['order'][1]['arms'],['js_callback','gameir_text'])
        self.assertEqual(p['endpoint'],'https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions')
        self.assertEqual(p['model'],'deepseek-v4-1-flash-260910')
        self.assertFalse(p['input_units_are_tokens']);self.assertFalse(p['input_padding_added'])
        for call in (send,execute_candidate):
            with self.assertRaisesRegex(EvaluationError,'DISABLED'):call(object(),enabled=True)

    def test_public_requests_share_semantics_read_only_own_public_starter(self):
        actual=Path.read_text
        def public_only(path,*args,**kwargs):
            if path.parent!=DATA:raise AssertionError('private prompt read: '+str(path))
            return actual(path,*args,**kwargs)
        with patch.object(Path,'read_text',public_only),patch('ark_eval.oracle.final_cases') as oracle,patch('ark_eval.reference.control_program') as answer:
            arms=public_arms()
        oracle.assert_not_called();answer.assert_not_called()
        self.assertEqual(len(arms),4)
        self.assertEqual(len({a['messages'][0]['content'] for a in arms}),1)
        for a in arms:
            text='\n'.join(m['content'] for m in a['messages'])
            own=(DATA/FILES[a['task_id'],a['representation']]).read_text(encoding='utf-8')
            other=(DATA/FILES[a['task_id'],ARMS[1] if a['representation']==ARMS[0] else ARMS[0]]).read_text(encoding='utf-8')
            self.assertIn(own,text);self.assertNotIn(other,text)
            for forbidden in ('v4-P-C02-','D01-missing-open','final_tests.json','candidate.txt','reference.py','seed123','diagnostic-oracle-private'):
                self.assertNotIn(forbidden,text)
            self.assertIn(BUSINESS,a['messages'][0]['content'])
            self.assertLessEqual(a['prompt_units_estimate'],24000)

    def test_starters_native_single_binding_and_equivalent_single_defect(self):
        modify=read_json(DATA/'modify.ir.json');debug=read_json(DATA/'debug.ir.json')
        for program in (modify,debug):
            compile_program(program)
            cost=[n for n in nodes(program) if n.get('op')=='let' and n.get('name')=='cost']
            self.assertEqual(len(cost),1);self.assertEqual(cost[0]['value']['value'],1)
        changed=copy.deepcopy(debug);comparisons=[n for n in nodes(changed) if n.get('op')=='le']
        self.assertEqual(len(comparisons),1);comparisons[0]['op']='ge';self.assertEqual(changed,modify)
        js_modify=(DATA/'modify.js').read_text(encoding='utf-8');js_debug=(DATA/'debug.js').read_text(encoding='utf-8')
        self.assertEqual(js_debug.count('keys <= cost'),1);self.assertEqual(js_debug.replace('keys <= cost','keys >= cost'),js_modify)
        self.assertEqual(js_modify.count('const cost = 1;'),1)
        target=trusted_target(TASKS[0]);cost=[n for n in nodes(target) if n.get('op')=='let' and n.get('name')=='cost'][0]
        cost['value']['value']=1;self.assertEqual(target,modify)

    def test_independent_expected_document_and_trusted_engine_diagnostics(self):
        document=diagnostic_document();self.assertEqual(document,read_json(ROOT/'ark_eval/door_v4_diagnostics.json'))
        self.assertEqual(len(document['cases']),16)
        for task in TASKS:
            cases=[c for c in document['cases'] if c['task_id']==task]
            self.assertEqual(len(cases),8);self.assertEqual({c['seed'] for c in cases},{1,123})
            core=compile_program(trusted_target(task))
            for case in cases:
                runtime=Runtime(core,case['initial'],seed=case['seed'])
                results=[runtime.step(**step) for step in case['steps']]
                self.assertEqual(observations(runtime,results),case['expected'],case['id'])
                self.assertEqual(replay(core,case['initial'],runtime.history,seed=case['seed']).snapshot(),runtime.snapshot())
                if 'D04-' in case['id']:
                    self.assertTrue(results[0]['effects']) # Attempt evidence remains.
                    self.assertEqual(runtime.snapshot()['entities'],case['initial'])

    def test_trusted_targets_match_unchanged_legacy26_per_task(self):
        legacy=read_json(ROOT/'ark_eval/final_tests.json')
        for task in TASKS:
            cases=[c for c in legacy['cases'] if c['task_id']==task];self.assertEqual(len(cases),26)
            core=compile_program(trusted_target(task))
            for c in cases:
                runtime=Runtime(core,c['initial'],seed=c['seed']);results=[runtime.step(**step) for step in c['steps']]
                self.assertEqual(observations(runtime,results),c['expected'],c['id'])

    def test_shared_ctx_control_matches_diagnostics_and_legacy(self):
        # Trusted Python mock exercises actual bounded ctx and host semantics;
        # it does not parse/evaluate a generated JS candidate or public starter.
        legacy=read_json(ROOT/'ark_eval/final_tests.json')['cases']
        diagnostics=diagnostic_document()['cases'];program=callback_manifest()
        for task in TASKS:
            for case in [c for c in legacy+diagnostics if c['task_id']==task]:
                runtime=HostRuntime(program,case['initial'],seed=case['seed'],runners={'open':DoorMockRunner(COST[task])})
                results=[runtime.step(**s) for s in case['steps']]
                self.assertEqual(observations(runtime,results),case['expected'],case['id'])
                played=replay_host(program,case['initial'],runtime.history,runtime.execution_history,seed=case['seed'])
                self.assertEqual(played.snapshot(),runtime.snapshot())

    def test_actual_single_compare_defect_revealed_without_model_execution(self):
        task=TASKS[1];legacy=read_json(ROOT/'ark_eval/final_tests.json')
        broken=compile_program(read_json(DATA/'debug.ir.json'));fixed=compile_program(trusted_target(task))
        for index in (0,2):
            c=next(c for c in legacy['cases'] if c['id']==f'{task}-{index}-seed1')
            bad=Runtime(broken,c['initial']);good=Runtime(fixed,c['initial'])
            self.assertNotEqual(observations(bad,[bad.step(**c['steps'][0])]),observations(good,[good.step(**c['steps'][0])]))

    def test_full_history_and_freeze_tamper_rejection(self):
        self.assertGreater(len(verify_history()['sha256']),80)
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'changed.json';value=load_freeze()
            for replacement in (True,0):
                changed=copy.deepcopy(value);changed['send_enabled']=replacement;p.write_text(json.dumps(changed),encoding='utf-8')
                with self.assertRaisesRegex(EvaluationError,'FREEZE_DRIFT'):load_freeze(p)
        with patch('ark_eval.door_v4.sha',return_value='0'*64):
            with self.assertRaisesRegex(EvaluationError,'HISTORY_CHANGED'):verify_history()

    def test_review_bundle_preserves_oracles_and_all_gameplay_notrun(self):
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'review';write_review_bundle(output)
            result=read_json(output/'notrun.json')
            self.assertEqual(result['model_calls'],0);self.assertEqual(result['candidate_executions'],0)
            for task in TASKS:
                for arm in ARMS:
                    self.assertEqual(result['gameplay'][task][arm]['diagnostic8']['NOTRUN'],8)
                    self.assertEqual(result['gameplay'][task][arm]['legacy26']['NOTRUN'],26)
                    request=read_json(output/task/arm/'request.json')
                    expected=next(a for a in public_arms() if a['task_id']==task and a['representation']==arm)
                    self.assertEqual(request,expected['request_body'])
            with self.assertRaisesRegex(EvaluationError,'FRESH_OUTPUT_REQUIRED'):write_review_bundle(output)


if __name__=='__main__':unittest.main()
