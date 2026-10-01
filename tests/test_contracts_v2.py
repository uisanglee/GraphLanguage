"""Positional compilation and shared schemas; execute handwritten fixtures only."""
import copy
import json
import sys
import unittest
import tempfile
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from graphir_contracts import compile_contracts, contract_schema, prepare_contract_task
from graphir_contracts_v2 import SCHEMA_V2
from graphir_core import canonicalize_graph
from graphdsl_nodes import compile_graph, node_request, symbol
from schema_validation import load_schema, validate_schema
import build_qwen_eval
import run_qwen_pipeline


def example(name):
    return json.loads((ROOT / f'examples/contracts_v2_{name}.json').read_text())


class PositionalContracts(unittest.TestCase):
    def test_request_pipeline_uses_v2_schema_demos_and_shared_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            task = dict(id='humaneval:v2', benchmark='humaneval', split='test', interface='function',
                        entrypoint='distance', starter_code='def distance(a: float, b: float) -> float:\n    pass',
                        prompt={'primary':'Return absolute difference of a and b.'})
            (directory / 'humaneval.jsonl').write_text(json.dumps(task) + '\n')
            requests, output = directory/'requests.jsonl', directory/'results.jsonl'
            with patch.object(sys, 'argv', ['build', '--input-dir', tmp, '--output', str(requests),
                    '--planner-format', 'contracts', '--contract-version', '2', '--num-demonstrations', '2']):
                build_qwen_eval.main()
            request = json.loads(requests.read_text())
            self.assertEqual(request['metadata']['contract_version'], '2')
            for msg in request['messages']:
                if msg['role'] == 'assistant':
                    self.assertEqual(json.loads(msg['content'])['contract_version'], '2.0')
            def complete(client, messages, *args, **kwargs):
                if kwargs.get('schema'):
                    self.assertEqual(kwargs['schema']['$defs']['ref']['anyOf'][0]['enum'], ['input:a', 'input:b'])
                    doc = example('basic')
                    del doc['interface']
                    return json.dumps(doc), {'usage': {'total_tokens': 1}}
                payload = json.loads(messages[-1]['content'])
                self.assertEqual(payload['value_contracts']['outputs'], {'value_0': 'float'})
                return payload['signature'] + "\n    return {'value_0': abs(inputs['a'] - inputs['b'])}", {'usage': {'total_tokens': 1}}
            with patch.object(sys, 'argv', ['run', '--input', str(requests), '--output', str(output),
                    '--model', 'fixture', '--planner-format', 'contracts', '--contract-version', '2',
                    '--num-code-demonstrations', '0']), patch.object(run_qwen_pipeline.Client, 'complete', autospec=True, side_effect=complete):
                run_qwen_pipeline.main()
            result = json.loads(output.read_text())
            self.assertTrue(result['artifact_valid'])
            self.assertEqual(result['llm_calls'], 2)

    def test_examples_and_generated_identifiers(self):
        for name in ('basic', 'records', 'branch'):
            doc = example(name)
            self.assertEqual(validate_schema(doc, load_schema(SCHEMA_V2)), [])
            graph = canonicalize_graph(compile_contracts(doc))
            self.assertTrue(graph['nodes'])
        doc = example('basic')
        doc['interface']['signature'] = '(value_0: float, b: float) -> float'
        doc['steps'][0]['needs'][0] = 'input:value_0'
        graph = compile_contracts(doc)
        self.assertEqual(graph['nodes'][2]['outputs'], {'value_1': 'float'})

    def test_unknown_inputs_forward_refs_and_invalid_names_rejected(self):
        for ref in ('input:missing', 'step:0', 'step:9', 'tuple', 'False', 'step:-1'):
            doc = example('basic')
            doc['steps'][0]['needs'] = [ref]
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                compile_contracts(doc)

    def test_fixed_signature_controls_input_enum(self):
        task = prepare_contract_task(dict(interface_mode='function', entrypoint='distance',
            starter_code='def distance(a: float, b: float) -> float:\n    pass'))
        schema = contract_schema(load_schema(SCHEMA_V2), task)
        doc = example('basic')
        del doc['interface']
        self.assertEqual(validate_schema(doc, schema), [])
        compile_contracts(doc, task)
        doc['steps'][0]['needs'] = ['input:renamed']
        self.assertTrue(validate_schema(doc, schema))

    def test_record_definition_shared_and_runtime_enforced(self):
        graph = canonicalize_graph(compile_contracts(example('records')))
        producer, consumer = [n for n in graph['nodes'] if n['kind'] == 'Compute']
        output = node_request(graph, producer)['value_contracts']['outputs']
        inputs = node_request(graph, consumer)['value_contracts']['inputs']
        self.assertEqual(output, inputs)
        implementations = {
            producer['id']: f"def {symbol(producer['id'])}(inputs, regions):\n    return {{'value_0': [{{'number': n, 'digit_sum': sum(map(int,str(abs(n)))), 'original_index': i}} for i,n in enumerate(inputs['nums'])]}}",
            consumer['id']: f"def {symbol(consumer['id'])}(inputs, regions):\n    return {{'value_1': [r['number'] for r in sorted(inputs['value_0'], key=lambda r: (r['digit_sum'], r['original_index']))]}}",
        }
        ns = {}
        exec(compile_graph(graph, implementations), ns)
        self.assertEqual(ns['sort_numbers']([22, 11, 20]), [11, 20, 22])
        implementations[producer['id']] = f"def {symbol(producer['id'])}(inputs, regions):\n    return {{'value_0': [(22, 4)]}}"
        ns = {}
        exec(compile_graph(graph, implementations), ns)
        with self.assertRaisesRegex(TypeError, 'shared value contract'):
            ns['sort_numbers']([22])

    def test_branch_scope_shape_and_nested_program(self):
        doc = example('branch')
        # Path locals never become additional outer slots.
        doc['return'] = 'step:2'
        with self.assertRaisesRegex(ValueError, 'forward step'):
            compile_contracts(doc)
        doc = example('branch')
        doc['steps'][1]['then']['steps'][0]['output'] = 'str'
        with self.assertRaisesRegex(ValueError, 'structure'):
            compile_contracts(doc)
        doc = example('branch')
        inner = copy.deepcopy(doc['steps'][1])
        doc['steps'][1]['then'] = {'steps': [inner], 'return': 'step:1'}
        graph = canonicalize_graph(compile_contracts(doc))
        self.assertEqual(sum(n['kind'] == 'Branch' for n in graph['nodes']), 2)

    def test_intermediate_tuple_rejected_public_tuple_allowed(self):
        doc = example('records')
        doc['steps'][0]['output'] = 'list[tuple[int, int]]'
        with self.assertRaisesRegex(ValueError, 'named record'):
            compile_contracts(doc)
        doc = example('basic')
        doc['interface']['signature'] = '(a: float, b: float) -> tuple[float, float]'
        doc['steps'][0]['output'] = 'tuple[float, float]'
        compile_contracts(doc)


if __name__ == '__main__':
    unittest.main()
