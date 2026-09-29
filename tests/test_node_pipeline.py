"""Compiler tests execute only hand-written fixtures, never model-generated code."""
import copy
import importlib
import io
import contextlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from graphdsl_nodes import symbol, compile_graph, node_request, check_node_source
from validate_graph import validate
from inference_journal import JournalClient
from build_qwen_eval import iter_records, normalize_interface
from official_results import load_official
import run_qwen_pipeline


class NodesTest(unittest.TestCase):
    def setUp(self):
        self.graph = json.loads((ROOT / 'examples/function_loop.graph.json').read_text())

    def implementations(self):
        return {
            'add_if_positive': f"def {symbol('add_if_positive')}(inputs, regions):\n    return {{'result': inputs['total'] + max(inputs['item'], 0) ** 2}}",
            'sum_loop': f"""def {symbol('sum_loop')}(inputs, regions):
    total = inputs['initial_total']
    for item in inputs['iterable']:
        result = regions['sum_loop.body']({{'loop_item.value': item, 'loop_total.value': total}})
        total = result['next_total.value']
    return {{'total': total}}
"""}

    def test_loop_carried_state_and_local_replacement(self):
        source = compile_graph(self.graph,self.implementations())
        namespace = {}
        exec(source,namespace)
        self.assertEqual(namespace['sum_positive_squares']([-2,3,4]),25)
        self.assertEqual(namespace['sum_positive_squares']([]),0)
        changed = self.implementations()
        changed['add_if_positive'] = changed['add_if_positive'].replace("max(inputs['item'], 0) ** 2", "max(inputs['item'], 0)")
        namespace = {}
        exec(compile_graph(self.graph,changed),namespace)
        self.assertEqual(namespace['sum_positive_squares']([-2,3,4]),7)
        self.assertEqual(changed['sum_loop'],self.implementations()['sum_loop'])

    def test_interface_class_wrapper(self):
        self.graph['interface'].update(entrypoint='Solution.solve', signature='(self, values: list[int]) -> int')
        namespace = {}
        exec(compile_graph(self.graph,self.implementations()),namespace)
        self.assertEqual(namespace['Solution']().solve([2,3]),13)

    def test_stdio_wiring_and_effect_token(self):
        graph = json.loads((ROOT/'examples/stdio.graph.json').read_text())
        implementations = {
            'parse': f"def {symbol('parse')}(inputs, regions):\n    return {{'numbers': [int(x) for x in inputs['text'].split()]}}",
            'sum': f"def {symbol('sum')}(inputs, regions):\n    return {{'total': sum(inputs['numbers'])}}",
            'format': f"def {symbol('format')}(inputs, regions):\n    return {{'text': str(inputs['total'])}}",
        }
        stdout = io.StringIO()
        with patch.object(sys,'stdin',io.StringIO('1 2 3\n')), contextlib.redirect_stdout(stdout):
            exec(compile_graph(graph,implementations),{'__name__':'__main__'})
        self.assertEqual(stdout.getvalue(),'6\n')

    def test_bad_loop_and_malformed_graph_are_terminal_errors(self):
        self.graph['nodes'][2]['config'] = {}
        self.assertTrue(validate(self.graph))
        self.graph['nodes'][0]['inputs'] = None
        self.assertTrue(validate(self.graph))

    def test_callback_prompt_never_contains_other_implementation_or_task(self):
        request = node_request(self.graph,self.graph['nodes'][2])
        self.assertNotIn('description',request)
        self.assertNotIn('implementations',request)
        self.assertIn('sum_loop.body',request['region_callbacks'])

    def test_reject_extra_function_and_signature_drift(self):
        node = self.graph['nodes'][2]
        self.assertTrue(check_node_source('def wrong():\n    pass',node))
        self.assertTrue(check_node_source(self.implementations()['sum_loop']+'\ndef extra(): pass',node))

    def test_all_lcb_starters_classified(self):
        corpus = ROOT / 'data/normalized/livecodebench.jsonl'
        if not corpus.exists():
            self.skipTest('downloaded LiveCodeBench corpus is not part of the source release')
        rows = list(iter_records(ROOT / 'data/normalized',{'livecodebench'}))
        classes = [r for r in rows if 'class Solution' in r['starter_code']]
        self.assertEqual(len(classes),444)
        self.assertTrue(all(r['interface']=='function' and r['entrypoint'].startswith('Solution.') for r in classes))

    def test_stage_cache_and_uncertain_response(self):
        class Fake:
            model = 'test'
            calls = 0
            def complete(self,*a,**k):
                self.calls += 1
                return 'value', {'usage':{'total_tokens':3}}
        with tempfile.TemporaryDirectory() as d:
            fake = Fake()
            client = JournalClient(fake,Path(d))
            for _ in range(2): client.complete([{'role':'user','content':'x'}],10,0)
            self.assertEqual(fake.calls,1)
            path = next(Path(d).glob('*.json'))
            path.write_text('{"status":"pending"}')
            with self.assertRaises(RuntimeError): client.complete([{'role':'user','content':'x'}],10,0)
            self.assertEqual(fake.calls,1)

    def test_original_parser_and_prompt_are_used(self):
        script = '''
import sys
sys.path.insert(0, 'third_party/parsel')
from graph import get_graph, strongly_connected_components
from fn import Function
root, functions = get_graph(['solve(x): Return helper(x).\\n','  helper(x): Return x plus one.\\n'])
assert 'from helpers import helper' in root.get_codex_input()
assert '# Signature: helper(x)' in root.get_codex_input()
assert len(strongly_connected_components(functions)[0]) == 1
try:
    get_graph(['a(x): A.\\n','    b(x): B.\\n','  c(x): C.\\n'])
except RuntimeError:
    pass
else:
    raise AssertionError('upstream must reject invalid dedent')
'''
        subprocess.run([sys.executable,'-c',script],cwd=ROOT,check=True)

    def test_original_codegen_payload_is_not_rewritten(self):
        script = '''
import sys, types, tempfile
from pathlib import Path
sys.path.insert(0, 'third_party/parsel')
calls = []
def complete(**kw):
    calls.append(kw)
    return {'choices':[types.SimpleNamespace(text='    return x + 1\\n')]}
sys.modules['openai'] = types.SimpleNamespace(Completion=types.SimpleNamespace(create=complete))
from codex import CodeGen
from fn import Function
from consts import CONSTS
import codex
codex.time.sleep = lambda _: None
CONSTS['num_completions'] = CONSTS['min_completions'] = CONSTS['num_completions_eval'] = 1
with tempfile.TemporaryDirectory() as d:
    key = Path(d)/'key'
    key.write_text('local:unused')
    class Adapter(CodeGen):
        def generate(self,*args,**kwargs):
            kwargs['model_name'] = 'qwen-fixture'
            return super().generate(*args,**kwargs)
    generator = Adapter(cache=str(Path(d)/'cache'),key=str(key))
    fn = Function('helper',['x'],[],'Return x plus one.',None,[])
    expected = fn.get_codex_input()
    fn.implement(generator)
    assert len(calls) == 1
    assert calls[0]['prompt'] == expected
    assert calls[0]['n'] == 1 and calls[0]['temperature'] == 0.6
    assert calls[0]['max_tokens'] == 500 and calls[0]['stop'] == ['\\ndef']
    assert fn.get_implementation_strs() == ['def helper(x):\\n    return x + 1\\n']
'''
        subprocess.run([sys.executable,'-c',script],cwd=ROOT,check=True,stdout=subprocess.PIPE)

    def test_official_single_candidate_normalization(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            (directory/'predictions_eval_results.json').write_text(json.dumps({'eval':{'BigCodeBench/0':[{'status':'pass'}]}}))
            self.assertTrue(load_official('bigcodebench',directory)[0]['passed'])
            (directory/'predictions_codegeneration_output_eval_all.json').write_text(json.dumps([
                {'platform':'leetcode','question_id':'1','graded_list':[False]}]))
            self.assertFalse(load_official('livecodebench',directory)[0]['passed'])

    def test_cli_node_pipeline_resumes_without_another_generation(self):
        with tempfile.TemporaryDirectory() as d:
            request = Path(d)/'request.jsonl'
            output = Path(d)/'result.jsonl'
            request.write_text(json.dumps({'custom_id':'test:0','metadata':{'benchmark':'humaneval','task_id':'test'},
                'messages':[{'role':'system','content':'planner'}, {'role':'user','content':json.dumps({'task':'sum positive squares'})} ]})+'\n')
            implementations = self.implementations()
            def completion(client, messages, *args, **kwargs):
                if messages[0]['content']=='planner': content = json.dumps(self.graph)
                else: content = implementations[json.loads(messages[-1]['content'])['node']['id']]
                return content,{'usage':{'total_tokens':1}}
            argv = ['run_qwen_pipeline','--input',str(request),'--output',str(output),'--model','fake',
                    '--num-code-demonstrations','0']
            with patch.object(sys,'argv',argv), patch.object(run_qwen_pipeline.Client,'complete',autospec=True,side_effect=completion) as mocked:
                run_qwen_pipeline.main()
                run_qwen_pipeline.main()
            self.assertEqual(mocked.call_count,3)
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(len(rows),1)
            self.assertTrue(rows[0]['artifact_valid'])

    def test_branch_calls_only_selected_region(self):
        graph = copy.deepcopy(self.graph)
        graph['interface'] = {'mode':'function','entrypoint':'choose','signature':'(condition, value)'}
        graph['regions'] = [{'id':'root','owner':None,'role':'root'},
                            {'id':'yes','owner':'branch','role':'then'}, {'id':'no','owner':'branch','role':'else'}]
        def port(name, typ='int'): return {'id':name,'type':typ}
        def node(nid,kind,region,ins,outs,config):
            return dict(id=nid,kind=kind,label=nid,description=nid,region=region,inputs=ins,outputs=outs,config=config)
        graph['nodes'] = [node('condition','Input','root',[],[port('v','bool')],{'parameter':'condition'}),
            node('value','Input','root',[],[port('v')],{'parameter':'value'}),
            node('branch','Branch','root',[port('condition','bool'),port('x')],[port('y')],{
                'branches':[{'region':'yes','condition_port':'condition'},{'region':'no','condition_port':None}],
                'region_bindings':{r:{'inputs':{r+'_in.v':'x'},'outputs':{'y':r+'_out.v'}} for r in ['yes','no']}}),
            node('result','Output','root',[port('v')],[],{'mode':'return'})]
        graph['edges'] = []
        def edge(a,ap,b,bp): graph['edges'].append({'from':{'node':a,'port':ap},'to':{'node':b,'port':bp}})
        edge('condition','v','branch','condition'); edge('value','v','branch','x'); edge('branch','y','result','v')
        for r in ['yes','no']:
            graph['nodes'] += [node(r+'_in','RegionInput',r,[],[port('v')],{'binding':'value'}),
                              node(r+'_out','RegionOutput',r,[port('v')],[],{'binding':'result'})]
            edge(r+'_in','v',r+'_out','v')
        implementation = f"""def {symbol('branch')}(inputs, regions):
    rid = 'yes' if inputs['condition'] else 'no'
    result = regions[rid]({{rid + '_in.v': inputs['x']}})
    return {{'y': result[rid + '_out.v']}}
"""
        namespace = {}
        exec(compile_graph(graph,{'branch':implementation}),namespace)
        self.assertEqual(namespace['choose'](True,9),9)
        self.assertEqual(namespace['choose'](False,5),5)

if __name__ == '__main__': unittest.main()
