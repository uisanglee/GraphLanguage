"""Handwritten synthetic programs only; model-produced code is never executed here."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from export_predictions import export_functional
from public_examples import public_checks
from graphdsl_nodes import compile_graph, symbol
from graphir_types import _gd_matches, _gd_annotation
from retrieve_demonstrations import load_catalog, retrieve
from run_safe_functional_eval import evaluate_one, diagnostics_complete
from validate_graph import validate


class ExecutionDiagnostics(unittest.TestCase):
    def test_public_checks_use_original_prompt_only(self):
        task = {'prompt': {'primary': 'Example:\n>>> twice(3)\n6\n'},
                'reference': {'test': 'HIDDEN_TEST', 'canonical_solution': 'SECRET'}}
        info = public_checks(task, 'humaneval')
        self.assertEqual(info['count'], 1)
        self.assertNotIn('HIDDEN_TEST', info['source'])
        self.assertNotIn('SECRET', info['source'])
        exec(info['source'], {'twice': lambda x: 2*x})
        self.assertEqual(public_checks({'prompt': {'primary': 'No examples'}}, 'humaneval')['status'], 'unavailable')

    def test_export_ignores_model_examples(self):
        task = {'id': 'synthetic', 'entrypoint': 'twice', 'starter_code': '',
                'public_tests': ['assert twice(3) == 6']}
        result = {'artifact_valid': True, 'generated_artifact': 'def twice(x): return x*2',
                  'graph': {'examples': [{'inputs': {'x': '3'}, 'outputs': {'return': '999'}}]}}
        job = export_functional({'synthetic': task}, {'synthetic': result}, 'mbpp')[0]
        self.assertEqual(job['public_examples']['source'], 'assert twice(3) == 6')
        self.assertNotIn('999', job['public_examples']['source'])

    def test_diagnostics_do_not_change_official_result(self):
        job = {'task_id': 'synthetic', 'generation_valid': True, 'candidate': 'FIXTURE',
               'test_source': 'OFFICIAL', 'invocation': 'check(candidate)',
               'graphir_type_diagnostics': True,
               'public_examples': {'source': 'PUBLIC', 'count': 1, 'origin': 'original'}}
        original = copy.deepcopy(job)
        outcomes = [
            {'status': 'failed', 'passed': False},
            {'status': 'passed', 'passed': True},
            {'status': 'failed', 'passed': False, 'stderr': 'GraphIRPortTypeError: score.scored'},
        ]
        with patch('run_safe_functional_eval.evaluate_single', side_effect=outcomes) as mocked:
            result = evaluate_one(job, 'fixture-image', 1, 128)
        self.assertFalse(result['passed'])
        self.assertTrue(result['example_valid'])
        self.assertFalse(result['port_contracts_valid'])
        self.assertEqual(mocked.call_args_list[1].args[0]['invocation'], '')
        self.assertEqual(mocked.call_args_list[1].args[0]['test_source'], 'PUBLIC')
        self.assertNotIn('OFFICIAL', mocked.call_args_list[1].args[0]['test_source'])
        self.assertEqual(job, original)
        self.assertFalse(diagnostics_complete({'public_evaluation': {'status': 'container_error'}}))

    def test_semantic_failure_is_not_false_port_type_failure(self):
        job = {'task_id': 'fixture', 'generation_valid': True, 'graphir_type_diagnostics': True}
        with patch('run_safe_functional_eval.evaluate_single', return_value={
                'status': 'failed', 'passed': False, 'stderr': 'AssertionError: wrong answer'}):
            result = evaluate_one(job, 'fixture-image', 1, 128)
        self.assertIsNone(result['port_contracts_valid'])

    def test_nested_container_types_and_unknown_annotations(self):
        check = lambda value, typ: _gd_matches(value, _gd_annotation(typ))
        self.assertFalse(check([(0, 9)], 'list[tuple[int, list[int]]]'))
        self.assertTrue(check([(9, [4, 5])], 'list[tuple[int, list[int]]]'))
        self.assertTrue(check({'value': 3.5}, 'dict[str, float]'))
        self.assertTrue(check(None, 'int | None'))
        self.assertTrue(check(2, 'float'))
        self.assertTrue(check(object(), 'torch.Tensor'))
        self.assertTrue(check((2, 3), 'tuple[int, ...]'))

    def test_compiled_diagnostic_reports_exact_port_without_changing_default(self):
        graph = json.loads((ROOT / 'examples/core_collection_contract.graph.json').read_text())
        self.assertEqual(validate(graph), [])
        implementations = {
            'score': f"def {symbol('score')}(inputs, regions):\n    return {{'scored': [(7, 11)]}}",
            'select': f"def {symbol('select')}(inputs, regions):\n    return {{'labels': ['fixture']}}",
        }
        namespace = {}
        exec(compile_graph(graph, implementations), namespace)
        self.assertEqual(namespace['rank_packages']([('box', [1])], 1), ['fixture'])
        namespace['_gd_check_types'] = True
        with self.assertRaisesRegex(TypeError, 'score.scored.*outputs'):
            namespace['rank_packages']([('box', [1])], 1)
        implementations['score'] = f"def {symbol('score')}(inputs, regions):\n    return {{'scored': [(sum(costs), label) for label, costs in inputs['packages']]}}"
        exec(compile_graph(graph, implementations), namespace)
        namespace['_gd_check_types'] = True
        self.assertEqual(namespace['rank_packages']([('box', [1])], 1), ['fixture'])

    def test_structural_retrieval_respects_budget_and_ablation(self):
        bank = load_catalog(ROOT / 'demonstrations/catalog.json')
        selected = retrieve(bank, 'sort rows by total', 'function', 'mbpp', 2)
        self.assertEqual([d['id'] for d in selected], ['function-collection-contract', 'function-nested-control'])
        self.assertEqual(retrieve(bank, 'sort', 'function', 'mbpp', 0), [])
        for demo in bank:
            self.assertEqual(validate(demo['graph']), [], demo['id'])


if __name__ == '__main__':
    unittest.main()
