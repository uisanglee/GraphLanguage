"""Static ABI checks and prompt isolation, using synthetic contracts only."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from graphdsl_nodes import check_node_source, node_request, symbol
from graphir_core import canonicalize_graph
from retrieve_demonstrations import load_catalog, retrieve
from validate_graph import validate


class NodeContracts(unittest.TestCase):
    def setUp(self):
        self.graph = canonicalize_graph(json.loads((ROOT / 'examples/core_nested.graph.json').read_text()))
        self.node = next(n for n in self.graph['nodes'] if n['kind'] == 'Compute')

    def check(self, typ, expression):
        node = dict(self.node, inputs=[{'id': 'x', 'type': typ}], outputs=[{'id': 'y', 'type': 'Any'}])
        code = f"def {symbol(node['id'])}(inputs, regions):\n    return {{'y': {expression}}}"
        return check_node_source(code, node, self.graph)

    def test_type_aware_abi_access(self):
        for typ in ('float', 'int', 'str', 'list[int]'):
            self.assertTrue(self.check(typ, "inputs['x']['value']"), typ)
        for typ in ('dict[str, float]', 'Any', 'CustomRecord'):
            self.assertEqual(self.check(typ, "inputs['x']['value']"), [], typ)
        self.assertEqual(self.check('str', "inputs['x'][0]"), [])
        self.assertEqual(self.check('float', "inputs['x']"), [])

    def test_unknown_ports_and_callbacks(self):
        self.assertTrue(self.check('int', "inputs['missing']"))
        self.assertTrue(self.check('int', "regions['neighbor']({})"))
        owner = next(n for n in self.graph['nodes'] if n['kind'] == 'Loop')
        rid = next(r['id'] for r in self.graph['regions'] if r.get('owner') == owner['id'])
        code = f"def {symbol(owner['id'])}(inputs, regions):\n    regions[{rid!r}]({{}})\n    return {{'total': inputs['total']}}"
        self.assertEqual(check_node_source(code, owner, self.graph), [])
        self.assertTrue(check_node_source(code.replace(repr(rid), "'not_owned'"), owner, self.graph))

    def test_nested_helper_scope_is_not_outer_abi(self):
        node = dict(self.node, inputs=[], outputs=[{'id': 'y', 'type': 'Any'}])
        code = f"def {symbol(node['id'])}(inputs, regions):\n    def helper(inputs):\n        return inputs['local']['value']\n    return {{'y': helper({{'local': {{'value': 2}}}})}}"
        self.assertEqual(check_node_source(code, node, self.graph), [])

    def test_prompt_contains_input_routes_but_no_downstream_contract(self):
        target = self.node
        for edge in self.graph['edges']:
            if edge['from']['node'] == target['id']:
                downstream = next(n for n in self.graph['nodes'] if n['id'] == edge['to']['node'])
                downstream['description'] = 'DOWNSTREAM_SECRET_CONTRACT'
        request = node_request(self.graph, target)
        self.assertNotIn('DOWNSTREAM_SECRET_CONTRACT', json.dumps(request))
        self.assertNotIn('connected_contracts', request)
        self.assertTrue(request['input_bindings'])
        for binding in request['input_bindings']:
            self.assertIn(binding['input_port'], request['input_values'])
            self.assertEqual(binding['access'], f"inputs[{binding['input_port']!r}]")

    def test_nested_demo_is_valid_and_retrievable(self):
        bank = load_catalog(ROOT / 'demonstrations/catalog.json')
        selected = retrieve(bank, 'editable nested Loop Branch body condition state', 'function', 'humaneval', 1)
        self.assertEqual(selected[0]['id'], 'function-nested-control')
        self.assertEqual(validate(selected[0]['graph']), [])


if __name__ == '__main__':
    unittest.main()
