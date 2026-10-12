"""Independent evaluator-authored ABI examples; no model candidate execution."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from headless_v01.compiler import compile_program, digest
from headless_v01.runtime import Job, Transaction
from headless_v01.types import Fault, SAFE_INT
from ark_eval.adapter import EvaluationError
from ark_eval.create_pair import load_freeze, load_legacy_freeze, import_contract, prepared_requests
from ark_eval.contract_v3 import (API, FACTS, ROOT, node, check_consistency, public_arms,
    current_review, load_review, load_previous_review, independent_examples, write_review_bundle, review_diff)
from ark_eval.host import HostRuntime, TrustedContext, ScriptedMockRunner

TARGET=['probe',-1,'genesis',0]

def probe(body):
    return {'version':'core-v0.1','fields':{'score':{'type':'int','authority':'IR','policy':'exclusive','range':[0,10]}},
            'events':{'Tick':{'record':{}},'Probe':{'record':{'target':'EntityID'}},'Sample':{'record':{'value':'int'}}},
            'handlers':[{'id':'probe','event':'Probe','priority':0,'body':body}], 'services':{},'extensions':{}}

def expr_body(expression):
    return [node('let',name='result',value=expression)]

def trusted_runtime(body,values=None):
    return HostRuntime(probe(body),[{'id':TARGET,'values':{'score':7} if values is None else values}])

def step(runtime):
    return runtime.step(inputs=[{'name':'Probe','payload':{'target':TARGET}}])

def ctx(values=None):
    runtime=trusted_runtime([],values);txn=Transaction(runtime)
    txn.job='probe';txn.current_event={'id':'probe:0','name':'Probe','payload':{'target':TARGET}}
    job=Job(txn,runtime.core['handlers'][0],txn.current_event,copy.deepcopy(runtime.world))
    return TrustedContext(job),job,runtime

class ContractV3Tests(unittest.TestCase):
    def test_review_is_separate_frozen_disabled_and_legacy_transport_rejects_it(self):
        v3=load_review();v2=load_freeze();v1=load_legacy_freeze()
        self.assertFalse(v3['send_enabled'])
        self.assertEqual(v3['review_status'],'AWAITING_PARENT_FAIRNESS_REVIEW')
        self.assertNotEqual(digest(v3),digest(v2));self.assertNotEqual(digest(v3),digest(v1))
        for validate in (import_contract,prepared_requests):
            with self.assertRaises(EvaluationError):validate(v3)
        self.assertEqual(v3['previous_protocol_digest'],digest(v2))
        self.assertFalse(v3['oracle_holdout'])
        self.assertIn('not independent holdout',v3['revision_provenance']['reason'])
        self.assertEqual(v3['supersedes_review_digest'],digest(load_previous_review()))

    def test_budget_route_model_oracle_and_thinking_unchanged(self):
        v3=load_review();diff=review_diff(v3);v2=load_freeze()
        for key in diff['unchanged_fields']:self.assertEqual(v3[key],v2[key])
        self.assertEqual(v3['budget_per_arm'],{'prompt_units':24000,'output_tokens':4096,'attempts':1,'repairs':0,'tool_calls':0})
        self.assertTrue(v3['endpoint'].startswith('https://ark.cn-beijing.volces.com/api/plan/v3/'))

    def test_semantic_facts_and_necessary_information_are_in_both_arms(self):
        arms=public_arms();self.assertEqual(arms[0]['messages'][0],arms[1]['messages'][0])
        for arm in arms:
            system=arm['messages'][0]['content'];user=arm['messages'][1]['content']
            for code,title,text,_ in FACTS:self.assertIn(f'[{code} {title}] {text}',system)
            for op,phase,args,meaning,ir,js,_ in API:
                self.assertIn(meaning,user)
                self.assertIn(ir if arm['representation']=='gameir_text' else js,user)
            for example in independent_examples():self.assertIn(example['meaning'],user)
            self.assertLessEqual(arm['prompt_units_estimate'],24000)

    def test_original_v2_starters_and_output_formats_are_restored(self):
        ir,js=public_arms();starter=json.loads(ir['starter']);v2=load_freeze()
        self.assertEqual(starter['handlers'],[])
        self.assertEqual(ir['starter'],v2['public_arms'][0]['starter'])
        for key in ('version','fields','events','services','extensions'):
            self.assertEqual(starter[key],json.loads(v2['public_arms'][0]['starter'])[key])
        self.assertEqual(js['starter'],v2['public_arms'][1]['starter'])
        compile_program(starter)

    def test_initial_payload_variables_and_native_js_conditions_are_explicit(self):
        ir,js=public_arms()
        self.assertIn('player 与 door 是事件 payload 自动绑定的初始变量',ir['messages'][1]['content'])
        self.assertIn('允许 int truthiness',js['messages'][1]['content'])
        self.assertNotIn('value 必须已是 bool',js['messages'][1]['content'])
        self.assertNotIn('只补空 handler',ir['messages'][1]['content'])
        self.assertIn('冗余但无害',ir['messages'][0]['content'])
        compile_program(probe(expr_body(node('resolve',entity=node('var',name='target')))))

    def test_reserved_emit_is_documented_and_rejected_by_actual_compiler_and_ctx(self):
        for arm in public_arms():self.assertIn('不能主动发出 Tick',arm['messages'][1]['content'])
        with self.assertRaises(Fault) as caught:
            compile_program(probe([node('emit',event='Tick',payload=node('record',items={}))]))
        self.assertEqual(caught.exception.code,'TypeError')
        c,_,_=ctx()
        with self.assertRaises(Fault) as caught:c.call('emit',{'event':'Tick','payload':{}},0)
        self.assertEqual(caught.exception.code,'TypeError')

    def test_prompt_builder_does_not_load_oracle_reference_or_original_candidates(self):
        with patch('ark_eval.oracle.final_cases') as cases,patch('ark_eval.reference.control_program') as reference,patch('ark_eval.candidate_review.load_candidates') as candidates:
            arms=public_arms()
        cases.assert_not_called();reference.assert_not_called();candidates.assert_not_called()
        for arm in arms:
            text=arm['messages'][1]['content']
            for word in ('control_program','final_tests.json','candidate.txt','ctx.write(player,','ctx.emit(\'Opened\''):
                self.assertNotIn(word,text)
        for example in independent_examples():
            text=json.dumps(example,ensure_ascii=False)
            for word in ('player','door','keys','Opened','MissingKey'):self.assertNotIn(word,text)

    def test_static_table_checks_fail_on_actual_compiler_or_ctx_shape_drift(self):
        self.assertEqual(check_consistency()['IR_documented_operations'],16)
        from ark_eval.contract_v3 import compiler_operation_shapes
        shapes=compiler_operation_shapes();shapes['expr']['require']={'option'}
        with patch('ark_eval.contract_v3.compiler_operation_shapes',return_value=shapes):
            with self.assertRaisesRegex(EvaluationError,'COMPILER_SHAPE_MISMATCH_require'):check_consistency()
        from ark_eval.host import ARGS
        args=copy.deepcopy(ARGS);args['read']={'entity'}
        with patch('ark_eval.host.ARGS',args):
            with self.assertRaisesRegex(EvaluationError,'CTX_SHAPE_MISMATCH_read'):check_consistency()
        rows=list(API);row=list(rows[2]);row[5]='ctx.unsupported(entity)';rows[2]=tuple(row)
        with patch('ark_eval.contract_v3.API',tuple(rows)):
            with self.assertRaisesRegex(EvaluationError,'CTX_DOCUMENTED_METHOD_MISMATCH'):check_consistency()

    def test_frozen_review_rejects_prompt_implementation_or_extra_field_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'freeze.json';original=load_review()
            for key,value in [('send_enabled',True),('schema','approved'),('extra','injected')]:
                mutated=copy.deepcopy(original);mutated[key]=value;p.write_text(json.dumps(mutated),encoding='utf-8')
                with self.assertRaises(EvaluationError):load_review(p)

    def test_review_bundle_contains_exact_prompts_and_notrun_without_credential_or_network(self):
        with tempfile.TemporaryDirectory() as directory,patch('ark_eval.manual_pair._https_once') as network,patch('ark_eval.manual_pair.getpass.getpass') as credential:
            output=Path(directory)/'review';receipt=write_review_bundle(output)
            self.assertEqual(receipt['status'],'REVIEW_ONLY_NOT_SENT')
            self.assertEqual(json.loads((output/'protocol.json').read_text(encoding='utf-8')),load_review())
            for arm in public_arms():
                self.assertEqual(json.loads((output/arm['representation']/'request.json').read_text(encoding='utf-8')),arm['request_body'])
            notrun=json.loads((output/'notrun.json').read_text(encoding='utf-8'))
            self.assertEqual(notrun['request_attempts'],0);self.assertEqual(notrun['candidate_executions'],0)
            for rep in ('gameir_text','js_callback'):
                report=json.loads((output/rep/'contract-not-checked.json').read_text(encoding='utf-8'))
                self.assertEqual(report['contract_status'],'NOT_CHECKED')
                self.assertEqual(report['checked_rule_ids'],[])
            with self.assertRaises(FileExistsError):write_review_bundle(output)
        network.assert_not_called();credential.assert_not_called()

    def test_public_positive_fragments_compile_and_missing_optional_cell_finishes(self):
        examples=independent_examples()
        for ex in examples[:3]:compile_program(probe(ex['IR'] if isinstance(ex['IR'],list) else expr_body(ex['IR'])))
        for values in ({'score':0},{}):
            r=trusted_runtime(examples[0]['IR'],values);result=step(r)
            self.assertEqual(result['status'],'COMMITTED');self.assertEqual(result['effects'],[])

    def test_short_circuit_and_empty_branches_skip_actual_host_access(self):
        ex=independent_examples()[2]
        r=trusted_runtime(expr_body(ex['IR']),{});self.assertEqual(step(r)['status'],'COMMITTED')
        self.assertEqual(r.execution_history[0]['host_operations_total'],0)
        target=node('var',name='target')
        body=[node('if',condition=node('literal',type='bool',value=False),then=expr_body(node('require',value=node('read',entity=target,field='score'))),**{'else':[]})]
        r=trusted_runtime(body,{});self.assertEqual(step(r)['status'],'COMMITTED')
        self.assertEqual(r.execution_history[0]['host_operations_total'],0)

    def test_option_entity_and_condition_negative_fragments_are_real_compile_rejections(self):
        examples=independent_examples()
        for index in (3,4,6):
            expr=examples[index]['IR'];body=[expr] if expr['op'] in ('if','return') else expr_body(expr)
            with self.assertRaises(Fault) as caught:compile_program(probe(body))
            self.assertEqual(caught.exception.code,'TypeError')
        self.assertEqual(caught.exception.phase,'parse')
        self.assertEqual(caught.exception.details,{'op':'return'})

    def test_resolve_read_require_types_false_zero_and_none_in_actual_ctx(self):
        c,j,r=ctx({'score':0})
        resolved=c.call('resolve',{'entity':TARGET},0);self.assertEqual(resolved,{'some':TARGET})
        self.assertEqual(c.call('read',{'entity':TARGET,'field':'score'},1),{'some':0})
        for value in (0,False):self.assertEqual(c.call('require',{'option':{'some':value}},2),value)
        with self.assertRaises(Fault) as caught:c.call('require',{'option':{'none':True}},3)
        self.assertEqual(caught.exception.code,'InvalidEntity')
        with self.assertRaises(Fault) as caught:c.call('read',{'entity':resolved,'field':'score'},4)
        self.assertEqual(caught.exception.code,'TypeError')
        missing=['probe',-1,'genesis',1]
        self.assertEqual(c.call('read',{'entity':missing,'field':'score'},5),{'none':True})

    def test_checked_sub_in_ctx_is_safe_int_and_overflow_is_not_silent(self):
        c,_,_=ctx()
        self.assertEqual(c.call('int_sub',{'a':7,'b':2},0),5)
        for args,code in [({'a':True,'b':1},'TypeError'),({'a':SAFE_INT,'b':-1},'NumericOverflow')]:
            with self.assertRaises(Fault) as caught:c.call('int_sub',args,1)
            self.assertEqual(caught.exception.code,code)

    def test_each_documented_ir_operation_accepts_independent_typed_use(self):
        target=node('var',name='target');n=node('var',name='n')
        body=[node('let',name='id',value=node('require',value=node('resolve',entity=target))),
              node('let',name='cell',value=node('read',entity=node('var',name='id'),field='score')),
              node('if',condition=node('and',a=node('is_some',value=node('var',name='cell')),b=node('not',value=node('literal',type='bool',value=False))),
                   then=[node('let',name='n',value=node('require',value=node('var',name='cell'))),
                         node('if',condition=node('and',a=node('ge',a=n,b=node('literal',type='int',value=0)),b=node('le',a=n,b=node('literal',type='int',value=10))),
                              then=[node('write',entity=target,field='score',value=node('sub',a=n,b=node('literal',type='int',value=0))),node('emit',event='Sample',payload=node('record',items={'value':n}))],**{'else':[]})],**{'else':[]})]
        seen=set()
        def walk(value):
            if isinstance(value,dict):
                if 'op'in value:seen.add(value['op'])
                for child in value.values():walk(child)
            elif isinstance(value,list):
                for child in value:walk(child)
        walk(body);self.assertEqual(seen,{row[0] for row in API})
        r=trusted_runtime(body);result=step(r)
        self.assertEqual(result['status'],'COMMITTED')
        self.assertEqual([e['op'] for e in result['effects']],['write','emit'])

    def test_actual_overlay_visible_but_effects_are_not_committed_until_success(self):
        c,j,r=ctx()
        c.call('write',{'entity':TARGET,'field':'score','value':4},0)
        self.assertEqual(c.call('read',{'entity':TARGET,'field':'score'},1),{'some':4})
        self.assertEqual(r.snapshot()['entities'][0]['values']['score'],7)
        c.call('emit',{'event':'Sample','payload':{'value':4}},2)
        self.assertEqual([e['op'] for e in j.effects],['write','emit'])
        with self.assertRaises(Fault) as caught:c.call('write',{'entity':TARGET,'field':'score','value':11},3)
        self.assertEqual(caught.exception.code,'InvariantViolation')
        self.assertEqual(r.snapshot()['entities'][0]['values']['score'],7)

    def test_write_and_emit_then_missing_require_roll_back_in_both_paths(self):
        target=node('var',name='target')
        body=[node('write',entity=target,field='score',value=node('literal',type='int',value=4)),
              node('emit',event='Sample',payload=node('record',items={'value':node('literal',type='int',value=4)})),
              node('let',name='fail',value=node('require',value=node('resolve',entity=node('literal',type='EntityID',value=['probe',-1,'genesis',1]))))]
        ir=trusted_runtime(body)
        frames=({'op':'write','args':{'entity':TARGET,'field':'score','value':4}},
                {'op':'emit','args':{'event':'Sample','payload':{'value':4}}},
                {'op':'require','args':{'option':{'none':True}}})
        js=HostRuntime(probe([]),[{'id':TARGET,'values':{'score':7}}],runners={'probe':ScriptedMockRunner(frames)})
        for r in (ir,js):
            before=r.snapshot();result=step(r)
            self.assertEqual(result['status'],'ABORTED');self.assertEqual(result['error']['code'],'InvalidEntity')
            self.assertEqual(r.snapshot(),before)
            self.assertFalse(any(e['name']=='Sample' for e in result['events']))

    def test_independent_local_name_and_type_errors_are_enforced(self):
        false=node('literal',type='bool',value=False)
        body=[node('if',condition=false,then=[node('let',name='inside',value=false)],**{'else':[]}),node('let',name='outside',value=node('var',name='inside'))]
        with self.assertRaises(Fault):compile_program(probe(body))
        for expression in [node('not',value=node('literal',type='int',value=0)),node('sub',a=false,b=node('literal',type='int',value=1))]:
            with self.assertRaises(Fault):compile_program(probe(expr_body(expression)))

if __name__=='__main__':unittest.main()
