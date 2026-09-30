"""Regression tests use only handwritten fixtures, never model-produced code."""
import copy
import json
import sys
import unittest
import tempfile
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from graphir_core import canonicalize_graph, normalize_type
from graphdsl_nodes import compile_graph, symbol, check_node_source
from validate_graph import validate, CORE_SCHEMA
from schema_validation import load_schema
from run_qwen_pipeline import parse_graph
import run_qwen_pipeline


def node(nid, kind, inputs, outputs, **extra):
    return dict(id=nid, kind=kind, description='Handwritten test contract',
                inputs=inputs, outputs=outputs, **extra)


def edge(a, b):
    return {'from': a, 'to': b}


def nested_graph():
    yes = dict(inputs={'item': 'int', 'total': 'int'}, outputs={'total': 'int'},
        nodes=[node('add', 'Compute', {'item': 'int', 'total': 'int'}, {'total': 'int'})],
        edges=[edge('$input.item', 'add.item'), edge('$input.total', 'add.total'), edge('add.total', '$output.total')])
    no = dict(inputs={'total': 'int'}, outputs={'total': 'int'}, nodes=[],
              edges=[edge('$input.total', '$output.total')])
    body = dict(inputs={'item': 'int', 'total': 'int'}, outputs={'total': 'int'}, nodes=[
        node('positive', 'Compute', {'item': 'int'}, {'yes': 'bool'}),
        node('choose', 'Branch', {'yes': 'bool', 'item': 'int', 'total': 'int'}, {'total': 'int'},
             branches=[{'when': 'yes', 'body': yes}, {'when': None, 'body': no}])],
        edges=[edge('$input.item', 'positive.item'), edge('positive.yes', 'choose.yes'),
               edge('$input.item', 'choose.item'), edge('$input.total', 'choose.total'),
               edge('choose.total', '$output.total')])
    return dict(graphir_version='0.2.0', interface=dict(mode='function', entrypoint='positive_sum', signature='(values)'),
        nodes=[node('values', 'Input', {}, {'value': 'list[int]'}),
               node('zero', 'Literal', {}, {'value': 'int'}, config={'value': 0}),
               node('scan', 'Loop', {'items': 'list[int]', 'total': 'int'}, {'total': 'int'},
                    control={'for_each': 'items', 'item': 'item', 'state': ['total']}, body=body),
               node('result', 'Output', {'value': 'int'}, {})],
        edges=[edge('values.value', 'scan.items'), edge('zero.value', 'scan.total'), edge('scan.total', 'result.value')])


def implementations():
    bodies = {
        'scan': """total = inputs['total']
for item in inputs['items']:
    total = regions['scan.body']({'scan.body.__input.item': item, 'scan.body.__input.total': total})['scan.body.__output.total']
return {'total': total}""",
        'scan.body.positive': "return {'yes': inputs['item'] > 0}",
        'scan.body.choose': """rid = 'scan.body.choose.branch_0' if inputs['yes'] else 'scan.body.choose.branch_1'
bindings = {rid + '.__input.total': inputs['total']}
if inputs['yes']:
    bindings[rid + '.__input.item'] = inputs['item']
return {'total': regions[rid](bindings)[rid + '.__output.total']}""",
        'scan.body.choose.branch_0.add': "assert inputs['item'] > 0\nreturn {'total': inputs['total'] + inputs['item']}",
    }
    return {nid: f'def {symbol(nid)}(inputs, regions):\n' + '\n'.join('    '+line for line in body.splitlines())
            for nid, body in bodies.items()}


class CoreRegressions(unittest.TestCase):
    def test_invalid_iterable_annotation_is_validation_error(self):
        graph = nested_graph()
        graph['nodes'][2]['inputs']['items'] = 'list['
        self.assertTrue(any('invalid iterable type annotation' in e for e in validate(graph)))

    def test_nested_pipeline_records_original_and_lowered_graph(self):
        graph, code = nested_graph(), implementations()
        with tempfile.TemporaryDirectory() as tmp:
            request, output = Path(tmp)/'requests.jsonl', Path(tmp)/'results.jsonl'
            request.write_text(json.dumps({'custom_id': 'fixture', 'metadata': {'benchmark': 'fixture'},
                'messages': [{'role': 'system', 'content': 'fixture-planner'},
                             {'role': 'user', 'content': '{"task":"positive sum"}'}]})+'\n')
            def complete(client, messages, *args, **kwargs):
                if messages[0]['content'] == 'fixture-planner':
                    return json.dumps(graph), {'usage': {'total_tokens': 1}}
                payload = json.loads(messages[-1]['content'])
                return code[payload['node']['id']], {'usage': {'total_tokens': 1}}
            argv = ['pipeline', '--input', str(request), '--output', str(output), '--model', 'fixture',
                    '--num-code-demonstrations', '0']
            with patch.object(sys, 'argv', argv), patch.object(run_qwen_pipeline.Client, 'complete', autospec=True, side_effect=complete):
                run_qwen_pipeline.main()
            result = json.loads(output.read_text())
            self.assertTrue(result['artifact_valid'])
            self.assertEqual(result['graph'], graph)
            self.assertEqual(set(result['node_results']), set(code))
            self.assertEqual(result['llm_calls'], 5)
            self.assertEqual(len(result['graph_canonical']['regions']), 4)

    def test_nested_branch_loop_and_local_replacement(self):
        graph = nested_graph()
        self.assertEqual(validate(graph), [])
        parsed, shape, semantic = parse_graph(json.dumps(graph), load_schema(CORE_SCHEMA))
        self.assertEqual(shape + semantic, [])
        namespace = {}
        code = implementations()
        exec(compile_graph(parsed, code), namespace)
        self.assertEqual(namespace['positive_sum']([-3, 2, 4]), 6)
        self.assertEqual(namespace['positive_sum']([]), 0)
        changed = dict(code)
        nid = 'scan.body.choose.branch_0.add'
        changed[nid] = changed[nid].replace("+ inputs['item']", "+ inputs['item'] ** 2")
        exec(compile_graph(graph, changed), namespace)
        self.assertEqual(namespace['positive_sum']([-3, 2, 4]), 20)
        self.assertEqual(changed['scan'], code['scan'])

    def test_bad_nested_contracts_are_rejected(self):
        for mutate in [
            lambda g: g['nodes'][2]['body']['outputs'].update(total='float'),
            lambda g: g['nodes'][2]['control'].update(item='total'),
            lambda g: g['nodes'][2]['body']['nodes'][1]['branches'].pop(),
            lambda g: g['nodes'][2]['body']['edges'].append(edge('$input.item', '$output.total')),
            lambda g: g['nodes'][2]['body']['inputs'].update(item='float'),
        ]:
            graph = nested_graph()
            mutate(graph)
            self.assertTrue(validate(graph))

    def test_loop_inside_loop_keeps_scopes_and_state(self):
        graph = nested_graph()
        inner = copy.deepcopy(graph['nodes'][2])
        inner['id'] = 'inner'
        outer = graph['nodes'][2]
        graph['nodes'][0]['outputs']['value'] = 'list[list[int]]'
        outer['inputs']['items'] = 'list[list[int]]'
        outer['body'] = dict(inputs={'item': 'list[int]', 'total': 'int'}, outputs={'total': 'int'},
            nodes=[inner], edges=[edge('$input.item', 'inner.items'), edge('$input.total', 'inner.total'),
                                 edge('inner.total', '$output.total')])
        self.assertEqual(validate(graph), [])
        inner_code = {nid.replace('scan', 'scan.body.inner'): source.replace(symbol(nid), symbol(nid.replace('scan', 'scan.body.inner'))).replace("'scan.body", "'scan.body.inner.body")
                      for nid, source in implementations().items()}
        inner_code['scan'] = implementations()['scan']
        namespace = {}
        exec(compile_graph(graph, inner_code), namespace)
        self.assertEqual(namespace['positive_sum']([[1, -2], [], [3, 4]]), 8)
        self.assertEqual(namespace['positive_sum']([]), 0)

    def test_core_defaults_and_validator_api_agree(self):
        graph = json.loads((ROOT/'examples/core_function_basic.graph.json').read_text())
        graph['target'] = {'language': 'python'}
        self.assertEqual(validate(graph), [])
        self.assertEqual(validate(graph, load_schema(CORE_SCHEMA)), [])
        self.assertEqual(canonicalize_graph(graph)['target']['python_version'], '>=3.10')

    def test_helper_return_has_its_own_contract(self):
        n = {'id': 'test', 'outputs': [{'id': 'result', 'type': 'int'}]}
        source = f"def {symbol('test')}(inputs, regions):\n    def helper():\n        return {{'count': 1}}\n    return {{'result': helper()['count']}}"
        self.assertEqual(check_node_source(source, n), [])
        self.assertTrue(check_node_source(source.replace("{'result':", "{'wrong':"), n))

    def test_typing_aliases_whitespace_optional(self):
        for a, b in [('Tuple[int,float]', 'tuple[int, float]'),
                     ('typing.List[int]', 'list[int]'), ('Optional[int]', 'int | None')]:
            self.assertEqual(normalize_type(a), normalize_type(b))
        self.assertIn('typing.List', normalize_type('Literal["typing.List"]'))

    def test_legacy_validation_stays_strict(self):
        graph = json.loads((ROOT/'examples/function_basic.graph.json').read_text())
        graph['nodes'][2]['outputs'][0]['id'] = 'a'
        graph['edges'][-1]['from']['port'] = 'a'
        self.assertTrue(any('both input and output' in e for e in validate(graph)))
        graph = json.loads((ROOT/'examples/function_loop.graph.json').read_text())
        graph['metadata']['graphir_core_version'] = '0.2.0'
        graph['nodes'][2]['config'] = {}
        self.assertTrue(validate(graph))


if __name__ == '__main__':
    unittest.main()
