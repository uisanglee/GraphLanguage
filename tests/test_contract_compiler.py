"""Exercise wiring and execution using only handwritten contracts/implementations."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from graphir_contracts import compile_contracts, signature_info, SCHEMA
from graphir_core import canonicalize_graph
from graphdsl_nodes import compile_graph, symbol, BOUNDARIES
from schema_validation import load_schema, validate_schema
from validate_graph import validate
from run_qwen_pipeline import parse_contracts
import run_qwen_pipeline
import build_qwen_eval
import run_experiments


def example(name):
    return json.loads((ROOT / f'examples/contracts_{name}.json').read_text())


def step(description, needs, produces):
    return dict(kind='Compute', description=description, needs=needs, produces=produces)


class ContractCompiler(unittest.TestCase):
    def test_catalog_compiles_to_compact_valid_graphs_deterministically(self):
        schema = load_schema(ROOT / 'schemas/graphir-compact.schema.json')
        for name in ['basic', 'iteration', 'collection', 'branch', 'stdio']:
            doc = example(name)
            before = copy.deepcopy(doc)
            graph = compile_contracts(doc)
            self.assertEqual(doc, before)
            self.assertEqual(graph, compile_contracts(doc))
            self.assertEqual(validate(graph), [])
            self.assertEqual(validate_schema(graph, schema), [])

    def test_multi_output_fanout_and_final_input_return(self):
        doc = example('basic')
        doc['steps'] = [step('Produce sum and difference.', ['a', 'b'], {'sum_value':'float', 'difference':'float'}),
                        step('Return sum times difference.', ['sum_value', 'difference'], {'answer':'float'})]
        doc['return'] = 'answer'
        graph = compile_contracts(doc)
        self.assertEqual(len(graph['edges']), 5)
        self.assertEqual(graph['nodes'][3]['inputs'], {'sum_value':'float', 'difference':'float'})
        doc['steps'] = []
        doc['return'] = 'a'
        graph = compile_contracts(doc)
        self.assertEqual(graph['edges'], [{'from':'a.value', 'to':'node_0.value'}])

    def test_errors_are_not_silently_repaired(self):
        cases = []
        for needs in [['missing'], ['distance'], ['a', 'a']]:
            doc = example('basic'); doc['steps'][0]['needs'] = needs; cases.append(doc)
        doc = example('basic'); doc['steps'][0]['produces'] = {'a':'float'}; cases.append(doc)
        doc = example('basic'); doc['return'] = 'missing'; cases.append(doc)
        doc = example('basic'); doc['steps'][0]['produces'] = {}; cases.append(doc)
        doc = example('basic'); doc['steps'][0]['produces'] = {'distance':'str'}; cases.append(doc)
        doc = example('basic'); doc['steps'][0]['produces'] = {'bad.name':'float'}; cases.append(doc)
        doc = example('branch'); doc['steps'][0]['condition'] = 'values'; cases.append(doc)
        doc = example('branch'); doc['return'] = 'product'; cases.append(doc)
        doc = example('branch'); doc['steps'][0]['else']['steps'][0]['produces']['total'] = 'str'; cases.append(doc)
        for doc in cases:
            with self.subTest(doc=doc):
                _, graph, shape, semantic = parse_contracts(json.dumps(doc), load_schema(SCHEMA))
                self.assertIsNone(graph)
                self.assertTrue(shape or semantic)
        doc = example('basic'); doc['edges'] = []
        self.assertTrue(parse_contracts(json.dumps(doc), load_schema(SCHEMA))[2])

    def test_public_signature_is_authoritative(self):
        doc = example('basic')
        task = dict(entrypoint='absolute_distance', interface_mode='function',
                    starter_code='def absolute_distance(a: float, b: float = 2) -> float:\n    pass')
        with self.assertRaisesRegex(ValueError, 'parameters/defaults'):
            compile_contracts(doc, task)
        doc['interface']['signature'] = '(a: int, b: int = 2) -> int'
        graph = compile_contracts(doc, task)
        self.assertEqual(graph['interface']['signature'], '(a: float, b: float=2) -> float')
        self.assertEqual(graph['nodes'][0]['outputs'], {'value':'float'})
        with self.assertRaisesRegex(ValueError, 'entrypoint'):
            compile_contracts(doc, dict(task, entrypoint='different'))

    def test_signature_variadics_and_node_id_collision(self):
        interface = dict(mode='function', entrypoint='f', signature='(node_0: int, /, *args: int, flag: bool = False, **kwargs: str) -> int')
        params, ret = signature_info(interface)
        self.assertEqual(params['args'], 'tuple[int, ...]')
        self.assertEqual(params['kwargs'], 'dict[str, str]')
        doc = dict(contract_version='1.0', interface=interface, steps=[], **{'return':'node_0'})
        self.assertEqual(validate(compile_contracts(doc)), [])

    def test_nested_branch_captures_and_lazy_execution(self):
        doc = example('branch')
        # Inner branch captures root values through the outer body's inferred pins.
        inner = copy.deepcopy(doc['steps'][0])
        inner['produces'] = {'inner_result':'int'}
        doc['steps'][0]['then'] = {'steps':[inner], 'return':'inner_result'}
        graph = canonicalize_graph(compile_contracts(doc))
        implementations = {}
        for node in graph['nodes']:
            if node['kind'] in BOUNDARIES:
                continue
            if node['kind'] == 'Branch':
                config = node['config']
                body = f'config = {config!r}\n'
                body += "branch = next(b for b in config['branches'] if b['condition_port'] is None or inputs[b['condition_port']])\nrid = branch['region']\nmapping = config['region_bindings'][rid]\nresult = regions[rid]({pin: inputs[name] for pin, name in mapping['inputs'].items()})\nreturn {name: result[pin] for name, pin in mapping['outputs'].items()}"
            elif 'product' in node['outputs'][0]['id']:
                body = "total = 1\nfor value in inputs['values']:\n    total *= value\nreturn {'product': total}"
            else:
                body = "raise RuntimeError('sum path selected')"
            implementations[node['id']] = f'def {symbol(node["id"])}(inputs, regions):\n' + '\n'.join('    '+line for line in body.splitlines())
        ns = {}
        exec(compile_graph(graph, implementations), ns)
        self.assertEqual(ns['combine_values']([2, 3, 4], True), 24)
        self.assertEqual(ns['combine_values']([], True), 1)
        with self.assertRaisesRegex(RuntimeError, 'sum path'):
            ns['combine_values']([2, 3], False)

    def test_request_builder_pipeline_and_resume(self):
        doc = example('basic')
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            task = dict(id='humaneval:fixture', benchmark='humaneval', split='test', interface='function',
                        entrypoint='absolute_distance', starter_code='', prompt={'primary':'Return absolute difference of a and b.'},
                        reference={'canonical_solution':'DO_NOT_EXPOSE', 'test':'HIDDEN_TEST'})
            (directory / 'humaneval.jsonl').write_text(json.dumps(task)+'\n')
            requests, output = directory/'requests.jsonl', directory/'results.jsonl'
            argv = ['builder', '--input-dir', tmp, '--output', str(requests), '--benchmarks', 'humaneval', '--planner-format', 'contracts', '--num-demonstrations', '2']
            with patch.object(sys, 'argv', argv): build_qwen_eval.main()
            request = json.loads(requests.read_text())
            self.assertNotIn('HIDDEN_TEST', requests.read_text())
            self.assertNotIn('DO_NOT_EXPOSE', requests.read_text())
            for message in request['messages']:
                if message['role'] == 'assistant':
                    self.assertIn('contract_version', json.loads(message['content']))
            calls = []
            def complete(client, messages, *args, **kwargs):
                calls.append(messages)
                if kwargs.get('schema'):
                    return json.dumps(doc), {'usage':{'total_tokens':1}}
                payload = json.loads(messages[-1]['content'])
                code = payload['signature'] + "\n    return {'distance': abs(inputs['a'] - inputs['b'])}"
                return code, {'usage':{'total_tokens':1}}
            argv = ['pipeline', '--input', str(requests), '--output', str(output), '--model', 'fixture', '--planner-format', 'contracts', '--num-code-demonstrations', '0']
            with patch.object(sys, 'argv', argv), patch.object(run_qwen_pipeline.Client, 'complete', autospec=True, side_effect=complete):
                run_qwen_pipeline.main()
                run_qwen_pipeline.main()
            row = json.loads(output.read_text())
            self.assertTrue(row['contract_valid'])
            self.assertTrue(row['graph_valid'])
            self.assertTrue(row['artifact_valid'])
            self.assertEqual(len(calls), 2)
            self.assertEqual(row['llm_calls'], 2)
            self.assertEqual(row['contracts'], doc)
            self.assertIn('edges', row['graph'])
            ns = {}; exec(row['generated_artifact'], ns)
            self.assertEqual(ns['absolute_distance'](2., 7.), 5.)

    def test_experiment_passes_contract_format_to_both_stages(self):
        config = json.loads((ROOT/'experiments/parsel_graphdsl_smoke_v9.json').read_text())
        commands = []
        with patch.object(run_experiments, 'run', side_effect=lambda command, dry: commands.append(command)):
            run_experiments.prepare(config, True)
            run_experiments.generate(config, True)
        relevant = [c for c in commands if any(p.endswith(('build_qwen_eval.py', 'run_qwen_pipeline.py')) for p in c)]
        self.assertEqual(len(relevant), 4)
        for command in relevant:
            self.assertEqual(command[command.index('--planner-format')+1], 'contracts')


if __name__ == '__main__': unittest.main()
