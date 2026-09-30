"""Only handwritten fixtures are executed, never generated model code."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from schema_validation import load_schema, validate_schema
from validate_graph import validate
from graphdsl_nodes import compile_graph, symbol, check_node_source, node_demonstrations
from graphir_core import canonicalize_graph
from retrieve_demonstrations import load_catalog
from run_qwen_pipeline import parse_graph


class CompactProfile(unittest.TestCase):
    def setUp(self):
        self.schema = load_schema(ROOT / 'schemas/graphir-compact.schema.json')
        self.graph = json.loads((ROOT / 'examples/core_branch.graph.json').read_text())

    def test_catalog_matches_generation_schema(self):
        for demo in load_catalog(ROOT / 'demonstrations/catalog.json'):
            self.assertEqual(validate_schema(demo['graph'], self.schema), [], demo['id'])
            self.assertEqual(validate(demo['graph']), [], demo['id'])

    def test_extended_kinds_rejected_even_inside_branch(self):
        for kind in ['Loop', 'Literal', 'Call', 'Resource', 'Context', 'Effect', 'Assert',
                     'Test', 'SourceArtifact', 'Locate', 'Edit', 'AddArtifact', 'DeleteArtifact', 'Patch']:
            graph = copy.deepcopy(self.graph)
            graph['nodes'][2]['branches'][0]['body']['nodes'][0]['kind'] = kind
            self.assertTrue(parse_graph(json.dumps(graph), self.schema)[1], kind)
        graph = copy.deepcopy(self.graph)
        graph['interface']['mode'] = 'repository_patch'
        self.assertTrue(validate_schema(graph, self.schema))

    def test_branch_runs_only_selected_path_with_internal_iteration(self):
        bodies = {
            'choose': "rid = 'choose.branch_0' if inputs['use_product'] else 'choose.branch_1'\nreturn {'value': regions[rid]({rid + '.__input.values': inputs['values']})[rid + '.__output.value']}",
            'choose.branch_0.reduce': "total = 1\nfor value in inputs['values']:\n    total *= value\nreturn {'value': total}",
            'choose.branch_1.reduce': "return {'value': sum(inputs['values'])}",
        }
        impl = {nid: f'def {symbol(nid)}(inputs, regions):\n' + '\n'.join('    '+s for s in body.splitlines())
                for nid, body in bodies.items()}
        graph = canonicalize_graph(self.graph)
        for node in graph['nodes']:
            if node['id'] in impl:
                self.assertEqual(check_node_source(impl[node['id']], node, graph), [])
        scope = {}
        exec(compile_graph(self.graph, impl), scope)
        fn = scope['combine_values']
        self.assertEqual(fn([2, 3, 4], True), 24)
        self.assertEqual(fn([2, 3, 4], False), 9)
        self.assertEqual(fn([], True), 1)
        self.assertEqual(fn([], False), 0)
        impl['choose.branch_1.reduce'] = f'def {symbol("choose.branch_1.reduce")}(inputs, regions):\n    raise RuntimeError("unselected")'
        exec(compile_graph(self.graph, impl), scope)
        self.assertEqual(scope['combine_values']([2, 3], True), 6)
        with self.assertRaisesRegex(RuntimeError, 'unselected'):
            scope['combine_values']([2, 3], False)

    def test_branch_demonstration_matches_runtime_abi(self):
        graph = canonicalize_graph(self.graph)
        owner = next(n for n in graph['nodes'] if n['id'] == 'choose')
        messages, ids = node_demonstrations(owner, 1, dialect='core-0.2')
        self.assertEqual(ids, ['node-core-branch'])
        code = messages[-1]['content']
        self.assertEqual(check_node_source(code, owner, graph), [])
        scope = {}
        exec(code, scope)
        fn = scope[symbol('choose')]
        for condition, index in [(True, 0), (False, 1)]:
            rid = f'choose.branch_{index}'
            def selected(bindings):
                self.assertEqual(bindings, {rid + '.__input.values': [2, 3]})
                return {rid + '.__output.value': 7}
            self.assertEqual(fn({'use_product': condition, 'values': [2, 3]}, {rid: selected}), {'value': 7})

    def test_branch_semantic_checks_remain_enabled(self):
        graph = copy.deepcopy(self.graph)
        graph['nodes'][2]['branches'][0]['when'] = 'len(values) > 0'
        self.assertTrue(parse_graph(json.dumps(graph), self.schema)[2])
        graph = copy.deepcopy(self.graph)
        graph['nodes'][2]['branches'][0]['body']['outputs']['value'] = 'str'
        self.assertTrue(parse_graph(json.dumps(graph), self.schema)[2])


if __name__ == '__main__':
    unittest.main()
