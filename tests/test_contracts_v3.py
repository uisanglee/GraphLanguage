"""Atomic contracts derive all wiring from the public interface."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import build_qwen_eval
import run_qwen_pipeline
from graphdsl_nodes import BOUNDARIES, compile_graph, symbol
from graphir_contracts import compile_contracts, contract_schema, prepare_contract_task
from graphir_contracts_v3 import SCHEMA_V3
from graphir_core import canonicalize_graph
from schema_validation import load_schema, validate_schema


def atomic():
    return json.loads((ROOT / 'examples/contracts_v3_atomic.json').read_text())


class AtomicContracts(unittest.TestCase):
    def test_one_compute_uses_every_public_input_and_return_type(self):
        doc = atomic()
        graph = compile_contracts(doc)
        computes = [n for n in graph['nodes'] if n['kind'] == 'Compute']
        self.assertEqual(len(computes), 1)
        self.assertEqual(computes[0]['inputs'], {'paren_string': 'str'})
        self.assertEqual(computes[0]['outputs'], {'result': 'list[str]'})
        self.assertEqual(graph['edges'], [
            {'from': 'paren_string.value', 'to': 'node_0.paren_string'},
            {'from': 'node_0.result', 'to': 'node_1.value'},
        ])

    def test_all_inputs_are_wired_and_result_name_cannot_collide(self):
        doc = atomic()
        doc['interface'].update(entrypoint='combine', signature='(result: int, other: int) -> int')
        graph = compile_contracts(doc)
        compute = next(n for n in graph['nodes'] if n['kind'] == 'Compute')
        self.assertEqual(compute['inputs'], {'result': 'int', 'other': 'int'})
        self.assertEqual(compute['outputs'], {'result_0': 'int'})

    def test_atomic_schema_forbids_model_authored_wiring(self):
        for key, value in [('steps', []), ('needs', ['input:paren_string']), ('output', 'list[str]')]:
            doc = atomic()
            if key == 'steps':
                doc[key] = value
            else:
                doc['compute'][key] = value
            with self.subTest(key=key):
                self.assertTrue(validate_schema(doc, load_schema(SCHEMA_V3)))
                with self.assertRaises(ValueError):
                    compile_contracts(doc)

    def test_fixed_interface_is_authoritative(self):
        task = prepare_contract_task(dict(interface_mode='function', entrypoint='solve',
            starter_code='def solve(values: list[int], limit: int) -> list[int]:\n    pass'))
        schema = contract_schema(load_schema(SCHEMA_V3), task)
        doc = {'contract_version': '3.0', 'compute': {'description': 'Filter values by limit.'}}
        self.assertEqual(validate_schema(doc, schema), [])
        graph = compile_contracts(doc, task)
        compute = next(n for n in graph['nodes'] if n['kind'] == 'Compute')
        self.assertEqual(compute['inputs'], {'values': 'list[int]', 'limit': 'int'})
        self.assertEqual(compute['outputs'], {'result': 'list[int]'})

    def test_stdio_is_one_compute(self):
        doc = {'contract_version':'3.0',
               'interface':{'mode':'stdio','entrypoint':None,'signature':None},
               'compute':{'description':'Parse integers and print their sum.'}}
        graph = compile_contracts(doc)
        compute = next(n for n in graph['nodes'] if n['kind'] == 'Compute')
        self.assertEqual(compute['inputs'], {'stdin':'str'})
        self.assertEqual(compute['outputs'], {'result':'str'})

    def test_request_pipeline_uses_atomic_prompt_schema_and_one_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            task = dict(id='humaneval:atomic', benchmark='humaneval', split='test', interface='function',
                        entrypoint='distance', starter_code='def distance(a: float, b: float) -> float:\n    pass',
                        prompt={'primary':'Return the absolute difference of a and b.'})
            (directory / 'humaneval.jsonl').write_text(json.dumps(task) + '\n')
            requests, output = directory/'requests.jsonl', directory/'results.jsonl'
            with patch.object(sys, 'argv', ['build', '--input-dir', tmp, '--output', str(requests),
                    '--planner-format','contracts','--contract-version','3','--num-demonstrations','1']):
                build_qwen_eval.main()
            request = json.loads(requests.read_text())
            self.assertEqual(request['metadata']['contract_version'], '3')
            self.assertTrue(all(json.loads(m['content'])['contract_version'] == '3.0'
                                for m in request['messages'] if m['role'] == 'assistant'))

            def complete(client, messages, *args, **kwargs):
                if kwargs.get('schema'):
                    self.assertNotIn('interface', kwargs['schema']['properties'])
                    return json.dumps({'contract_version':'3.0', 'compute':{
                        'description':'Return the non-negative absolute difference between a and b.'}}), {'usage':{'total_tokens':1}}
                payload = json.loads(messages[-1]['content'])
                self.assertEqual(payload['input_values'], {
                    'a':{'type':'float','access':"inputs['a']"},
                    'b':{'type':'float','access':"inputs['b']"}})
                return payload['signature'] + "\n    return {'result': abs(inputs['a'] - inputs['b'])}", {'usage':{'total_tokens':1}}

            with patch.object(sys, 'argv', ['run','--input',str(requests),'--output',str(output),
                    '--model','fixture','--planner-format','contracts','--contract-version','3',
                    '--num-code-demonstrations','0']), patch.object(
                    run_qwen_pipeline.Client, 'complete', autospec=True, side_effect=complete):
                run_qwen_pipeline.main()
            row = json.loads(output.read_text())
            self.assertTrue(row['artifact_valid'])
            canonical = canonicalize_graph(row['graph'])
            self.assertEqual(len([n for n in canonical['nodes'] if n['kind'] not in BOUNDARIES]), 1)
            ns = {}
            exec(row['generated_artifact'], ns)
            self.assertEqual(ns['distance'](2.0, 7.0), 5.0)


if __name__ == '__main__':
    unittest.main()
