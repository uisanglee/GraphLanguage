"""Compiler, experimental controls and safe evaluator integration regressions."""
import ast
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from build_pseudocode_eval import request_for
from export_predictions import export_functional
from inference_journal import JournalClient
from pseudocode_graphir import compile_pseudocode, validate_graph, artifact_errors
from pseudocode_graphir import SCHEMA
from validate_artifact import strip_fence
from schema_validation import load_schema, validate_schema
from run_pseudocode_pipeline import generate_one, compiler_feedback
from official_results import inference_totals
from summarize_experiment import structural_node_counts
import run_experiments
import build_pseudocode_eval
import run_pseudocode_pipeline


PLAN = '''def total(values: list[int]) -> int:
    """Sum positive values; ignore zero and negative numbers."""
    result = 0
    for value in values:
        if value > 0:
            result += value
    return result
'''
TASK = {'interface_mode': 'function', 'entrypoint': 'total',
        'starter_code': 'def total(values: list[int]) -> int:\n    pass',
        'task': 'Sum strictly positive values.'}
SYSTEM = (ROOT / 'prompts/pseudocode_to_python.md').read_text()


def all_nodes(body):
    for node in body['nodes']:
        yield node
        if node['kind'] == 'Loop':
            yield from all_nodes(node['body'])
            if 'else_body' in node:
                yield from all_nodes(node['else_body'])
        if node['kind'] == 'Branch':
            for branch in node['branches']:
                yield from all_nodes(branch['body'])


def request():
    record = {'id': 'humaneval:synthetic', 'benchmark': 'humaneval', 'split': 'test',
              'interface': 'function', 'entrypoint': 'total', 'starter_code': TASK['starter_code'],
              'prompt': {'primary': TASK['task']},
              'reference': {'test': 'HIDDEN_SECRET', 'canonical_solution': 'GOLD_SECRET'},
              'metadata': {'official_metadata': {'hidden': 'METADATA_SECRET'}}}
    return request_for(record, 'primary', TASK['task'], 'Generate pseudocode.')


class FakeClient:
    model = 'test-model'

    def __init__(self, text=PLAN, finish_reason='stop'):
        self.text, self.finish_reason = text, finish_reason
        self.calls = []

    def complete(self, messages, max_tokens, temperature, **kwargs):
        self.calls.append(messages)
        return self.text, {'usage': {'total_tokens': 10}, 'elapsed_seconds': 1,
                           'finish_reason': self.finish_reason}


class SequenceClient(FakeClient):
    def __init__(self, responses):
        super().__init__()
        self.responses = list(responses)

    def complete(self, messages, max_tokens, temperature, **kwargs):
        self.calls.append(messages)
        text, finish_reason = self.responses.pop(0)
        return text, {'usage': {'total_tokens': 10}, 'elapsed_seconds': 1,
                      'finish_reason': finish_reason}


class CompilerTests(unittest.TestCase):
    def test_pseudocode_lowers_to_distinct_structural_nodes(self):
        graph = compile_pseudocode(PLAN, TASK)
        body = graph['functions'][0]['body']
        self.assertEqual([n['kind'] for n in body['nodes']],
                         ['Input', 'Assign', 'Loop', 'Return'])
        loop = next(n for n in body['nodes'] if n['kind'] == 'Loop')
        branch = loop['body']['nodes'][0]
        self.assertEqual(branch['kind'], 'Branch')
        self.assertEqual(branch['branches'][0]['body']['nodes'][0]['kind'], 'Update')
        self.assertEqual(compile_pseudocode(PLAN, TASK), graph)
        self.assertNotIn('pseudocode', graph['functions'][0])
        self.assertNotIn('source', json.dumps(graph))
        self.assertTrue(all(set(e) == {'kind', 'from', 'to'} for e in body['edges']))
        self.assertEqual(validate_schema(graph, load_schema(SCHEMA)), [])
        counts = structural_node_counts(graph)
        self.assertEqual(counts['Loop'], 1)
        self.assertEqual(counts['Branch'], 1)
        self.assertGreater(counts['Update'], 0)

    def test_node_mapping_covers_calls_resources_asserts_and_effects(self):
        plan = '''def total(values: list[int]) -> int:
    assert values
    best = math.inf
    for value in values:
        if value < 0:
            continue
        best = min(best, value)
    return best
'''
        graph = compile_pseudocode(plan, TASK)
        kinds = {node['kind'] for node in all_nodes(graph['functions'][0]['body'])}
        self.assertTrue({'Input', 'Resource', 'Assert', 'Assign', 'Loop', 'Branch',
                         'Control', 'Return'} <= kinds)

    def test_comprehension_locals_do_not_become_resources(self):
        graph = compile_pseudocode('def total(values: list[int]) -> int:\n    squares = [x*x for x in values if x > 0]\n    return sum(squares)', TASK)
        resources = [node['config']['symbol'] for node in graph['functions'][0]['body']['nodes']
                     if node['kind'] == 'Resource']
        self.assertNotIn('x', resources)

    def test_lambda_parameters_are_local_expression_bindings(self):
        plan = '''def total(values):
    ordered = sorted(values, key=lambda item: item[1])
    return ordered
'''
        graph = compile_pseudocode(plan, dict(TASK, starter_code='def total(values):\n    pass'))
        call = next(node for node in graph['functions'][0]['body']['nodes'] if node['kind'] == 'Call')
        self.assertEqual(call['config']['value'], 'sorted(values, key=lambda item: item[1])')
        self.assertEqual(set(call['inputs']), {'values'})

    def test_trailing_module_asserts_are_separated_without_execution(self):
        plan = '''def total(values):
    return len(values)

assert total([]) == 0
assert total([1, 2]) == 2
'''
        graph = compile_pseudocode(plan, dict(TASK, starter_code='def total(values):\n    pass'))
        self.assertEqual(len(graph['functions']), 1)
        self.assertFalse(any(node['kind'] == 'Assert'
                             for node in all_nodes(graph['functions'][0]['body'])))
        with self.assertRaisesRegex(ValueError, 'must follow'):
            compile_pseudocode('assert True\ndef total(values):\n    return 0',
                               dict(TASK, starter_code='def total(values):\n    pass'))

    def test_recursion_repeated_calls_and_higher_order_refs(self):
        plan = '''def helper(x):
    return helper(x - 1) if x > 0 else 0
def total(values: list[int]) -> int:
    for x in values:
        if x > 0:
            helper(x)
            helper(x + 1)
    return sum(map(helper, values))
'''
        graph = compile_pseudocode(plan, TASK)
        self.assertEqual(len(graph['functions']), 2)
        helper_nodes = list(all_nodes(graph['functions'][0]['body']))
        total_nodes = list(all_nodes(graph['functions'][1]['body']))
        self.assertEqual(helper_nodes[-1]['config']['calls'][0]['definition'], 'function_0')
        direct = [call for node in total_nodes for call in node['config'].get('calls', [])
                  if call['definition'] == 'function_0']
        self.assertEqual(len(direct), 2)
        self.assertEqual(direct[1]['arguments'], ['x + 1'])
        output = next(node for node in total_nodes if node['kind'] == 'Return')
        self.assertEqual(output['config']['function_references']['helper'], 'function_0')

    def test_mutual_recursion(self):
        plan = 'def a(x):\n    return b(x)\ndef b(x):\n    return a(x)\ndef total(values: list[int]) -> int:\n    return a(values)'
        graph = compile_pseudocode(plan, TASK)
        calls = [[call['definition'] for node in all_nodes(fn['body'])
                  for call in node['config'].get('calls', [])] for fn in graph['functions'][:2]]
        self.assertIn('function_1', calls[0])
        self.assertIn('function_0', calls[1])

    def test_dynamic_callable_is_not_a_false_definition_edge(self):
        graph = compile_pseudocode('def helper(x):\n    return x\ndef total(values: list[int]) -> int:\n    helper = values[0]\n    return helper()', TASK)
        total = graph['functions'][1]
        calls = [call for node in all_nodes(total['body']) for call in node['config'].get('calls', [])]
        self.assertTrue(calls)
        self.assertTrue(all(call['definition'] is None for call in calls))

    def test_abstract_helpers_are_permitted(self):
        graph = compile_pseudocode('def total(values: list[int]) -> int:\n    return sum_positive_entries(values)', TASK)
        call = next(node for node in all_nodes(graph['functions'][0]['body']) if node['kind'] == 'Return')['config']['calls'][0]
        self.assertIsNone(call['definition'])
        self.assertEqual(call['target'], 'sum_positive_entries')

    def test_external_comments_and_module_description_are_preserved(self):
        graph = compile_pseudocode('"""Handle positive values."""\n# helper returns the sum of positive entries\n' + PLAN, TASK)
        self.assertEqual(graph['module_description'], 'Handle positive values.')
        self.assertEqual(graph['notes'], ['# helper returns the sum of positive entries'])

    def test_local_algorithm_comment_attaches_to_compact_statement(self):
        plan = '''def total(values):
    # Ignore invalid entries before applying the abstract scoring operation.
    result = score_valid_entries(values)
    return result
'''
        graph = compile_pseudocode(plan, dict(TASK, starter_code='def total(values):\n    pass'))
        call = next(node for node in graph['functions'][0]['body']['nodes'] if node['kind'] == 'Call')
        self.assertEqual(call['config']['comments'],
                         ['Ignore invalid entries before applying the abstract scoring operation.'])

    def test_exact_parameters_required_but_annotations_restored(self):
        graph = compile_pseudocode('def total(values):\n    return 0', TASK)
        self.assertEqual(graph['interface']['signature'], '(values: list[int]) -> int')
        with self.assertRaisesRegex(ValueError, 'parameters'):
            compile_pseudocode('def total(numbers):\n    return 0', TASK)
        task = dict(TASK, starter_code='def total(values, /, *, limit=2):\n    pass')
        compile_pseudocode('def total(values, /, *, limit=2):\n    return limit', task)
        with self.assertRaises(ValueError):
            compile_pseudocode('def total(values, limit=2):\n    return limit', task)

    def test_class_method_interface(self):
        task = dict(TASK, entrypoint='Solution.total',
                    starter_code='class Solution:\n    def total(self, values: list[int]) -> int:')
        plan = 'class Solution:\n    def total(self, values):\n        return self.helper(values)\n    def helper(self, values):\n        return sum(values)'
        graph = compile_pseudocode(plan, task)
        self.assertEqual(graph['interface']['entrypoint'], 'Solution.total')
        call = next(node for node in all_nodes(graph['functions'][0]['body']) if node['kind'] == 'Return')
        self.assertEqual(call['config']['calls'][0]['definition'], 'function_1')

    def test_augassign_reads_old_value_and_loop_exports_state(self):
        graph = compile_pseudocode(PLAN, TASK)
        loop = next(node for node in graph['functions'][0]['body']['nodes'] if node['kind'] == 'Loop')
        update = next(node for node in all_nodes(loop['body']) if node['kind'] == 'Update')
        self.assertIn('result', update['inputs'])
        update_edges = [edge for branch in loop['body']['nodes'][0]['branches']
                        for edge in branch['body']['edges'] if edge['to'] == [update['id'], 'result']]
        self.assertEqual(len(update_edges), 1)
        self.assertIn('result', loop['config']['carried'])
        self.assertTrue(any(edge['kind'] == 'state' and edge['to'] == ['$output', 'result']
                            for edge in loop['body']['edges']))

    def test_undefined_bare_name_and_partial_branch_definition_rejected(self):
        for plan in ('def total(values):\n    return typo_name',
                     'def total(values):\n    if values:\n        answer = 1\n    return answer'):
            with self.subTest(plan=plan), self.assertRaisesRegex(ValueError, 'undefined'):
                compile_pseudocode(plan, dict(TASK, starter_code='def total(values):\n    pass'))

    def test_value_defined_on_every_fallthrough_branch_is_available(self):
        plan = '''def total(values):
    if values:
        answer = 1
    else:
        return 0
    return answer
'''
        graph = compile_pseudocode(plan, dict(TASK, starter_code='def total(values):\n    pass'))
        branch = next(node for node in graph['functions'][0]['body']['nodes'] if node['kind'] == 'Branch')
        self.assertIn('answer', branch['outputs'])
        self.assertTrue(branch['branches'][0]['body']['falls_through'])
        self.assertFalse(branch['branches'][1]['body']['falls_through'])

    def test_control_edges_preserve_order_and_unreachable_code_is_rejected(self):
        graph = compile_pseudocode('def total(values):\n    log(values)\n    save(values)\n    return 0',
                                   dict(TASK, starter_code='def total(values):\n    pass'))
        body = graph['functions'][0]['body']
        calls = [node for node in body['nodes'] if node['kind'] == 'Call']
        self.assertTrue(any(edge['kind'] == 'control'
                            and edge['from'] == [calls[0]['id'], '$control']
                            and edge['to'] == [calls[1]['id'], '$control'] for edge in body['edges']))
        with self.assertRaisesRegex(ValueError, 'unreachable'):
            compile_pseudocode('def total(values):\n    return 0\n    values = []',
                               dict(TASK, starter_code='def total(values):\n    pass'))

    def test_break_and_continue_preserve_loop_carried_state(self):
        for operation in ('break', 'continue'):
            plan = f'''def total(values):
    answer = 0
    for value in values:
        answer += value
        {operation}
    return answer
'''
            graph = compile_pseudocode(plan, dict(TASK, starter_code='def total(values):\n    pass'))
            loop = next(node for node in graph['functions'][0]['body']['nodes'] if node['kind'] == 'Loop')
            self.assertFalse(loop['body']['falls_through'])
            self.assertEqual(set(loop['body']['outputs']), {'answer'})
            self.assertTrue(any(edge['kind'] == 'state' and edge['to'] == ['$output', 'answer']
                                for edge in loop['body']['edges']))

    def test_stdio_and_no_execution_even_of_defaults(self):
        graph = compile_pseudocode('def solve(stdin: str) -> str:\n    return format_sum(stdin)', {'interface_mode': 'stdio'})
        self.assertEqual(graph['interface']['signature'], '(stdin: str) -> str')
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'executed'
            raw = f'def total(values=__import__("pathlib").Path({str(marker)!r}).touch()):\n    return 0'
            compile_pseudocode(raw, {'interface_mode': 'function', 'entrypoint': 'total'})
            self.assertFalse(marker.exists())

    def test_reject_unsupported_and_malformed_plans(self):
        for plan in ('import os\n' + PLAN, 'def total(values):\n    ...',
                     'def total(values):\n    break\n    return 0',
                     PLAN + PLAN, 'def total(values):\n    def helper():\n        return 0\n    return helper()',
                     'def total(values):\n    yield 1\n    return 0',
                     'Sort values and return the sum.'):
            with self.subTest(plan=plan), self.assertRaises((ValueError, SyntaxError)):
                compile_pseudocode(plan, TASK)

    def test_missing_ports_and_duplicate_drivers_rejected(self):
        graph = compile_pseudocode(PLAN, TASK)
        for change in ('port', 'duplicate', 'missing'):
            bad = copy.deepcopy(graph)
            edges = bad['functions'][0]['body']['edges']
            if change == 'port': edges[0]['from'][1] = 'wrong'
            if change == 'duplicate': edges.append(copy.deepcopy(edges[0]))
            if change == 'missing': edges.pop(0)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_graph(bad)

    def test_artifact_gate_preserves_interface(self):
        self.assertEqual(artifact_errors(PLAN, TASK), [])
        self.assertTrue(artifact_errors('def unrelated(values):\n    return 0', TASK))
        self.assertTrue(artifact_errors('def total(other):\n    return 0', TASK))


class PipelineTests(unittest.TestCase):
    def run_arm(self, representation='graphir', source_context='original', planner=None,
                example_context=None, req=None, max_plan_repairs=0):
        planner = planner or FakeClient()
        coder = FakeClient()
        if example_context is None:
            example_context = 'public' if source_context == 'original' else 'none'
        row = generate_one(req or request(), planner, coder, representation=representation,
                           source_context=source_context, example_context=example_context,
                           system=SYSTEM, repair_system='Repair the plan.',
                           max_plan_repairs=max_plan_repairs)
        return row, planner, coder

    def test_public_source_is_preserved_and_hidden_fields_excluded(self):
        row, planner, coder = self.run_arm()
        self.assertTrue(row['artifact_valid'])
        self.assertEqual(row['llm_calls'], 2)
        payload = json.loads(coder.calls[0][-1]['content'])
        self.assertEqual(payload['source_specification']['task'], TASK['task'])
        self.assertNotIn('public_examples', payload['source_specification'])
        all_prompts = json.dumps(planner.calls + coder.calls)
        for secret in ('HIDDEN_SECRET', 'GOLD_SECRET', 'METADATA_SECRET'):
            self.assertNotIn(secret, all_prompts)
        self.assertIn('graphir', payload)
        self.assertNotIn('_gd_node', all_prompts)
        self.assertEqual(inference_totals(row), (20, 2))

    def test_all_arms_share_same_plan_and_equal_source_for_representation_comparison(self):
        rows = []
        for rep, context, examples in [('pseudocode', 'original', 'public'),
                                       ('graphir', 'none', 'none'),
                                       ('graphir', 'original', 'public')]:
            row, _, coder = self.run_arm(rep, context, example_context=examples)
            payload = json.loads(coder.calls[0][-1]['content'])
            self.assertEqual('source_specification' in payload, context == 'original')
            self.assertNotIn('public_examples', payload)  # this synthetic task has none
            self.assertIn(rep, payload)
            rows.append(row)
        self.assertEqual(len({r['pseudocode_raw'] for r in rows}), 1)
        self.assertEqual(rows[0]['graph'], rows[2]['graph'])

    def test_direct_one_call_and_same_original_input(self):
        row, planner, coder = self.run_arm('direct')
        self.assertEqual(len(planner.calls), 0)
        self.assertEqual(row['llm_calls'], 1)
        self.assertNotIn('compiler_version', row)
        self.assertTrue(row['artifact_valid'])

    def test_failure_and_truncation_do_not_resample_or_synthesize(self):
        for planner in (FakeClient('not valid code'), FakeClient(PLAN, 'length')):
            row, planner, coder = self.run_arm(planner=planner)
            self.assertFalse(row['artifact_valid'])
            self.assertFalse(row['graph_valid'])
            self.assertEqual(len(planner.calls), 1)
            self.assertEqual(len(coder.calls), 0)
            self.assertTrue(row['pseudocode_errors'])

    def test_compiler_feedback_repair_recovers_once_and_preserves_audit_trail(self):
        planner = SequenceClient([('def total(other):\n    return 0', 'stop'),
                                  (PLAN, 'stop')])
        row, planner, coder = self.run_arm(planner=planner, max_plan_repairs=2)
        self.assertTrue(row['artifact_valid'])
        self.assertFalse(row['initial_pseudocode_valid'])
        self.assertTrue(row['pseudocode_repaired'])
        self.assertEqual(row['pseudocode_repair_attempts'], 1)
        self.assertEqual(row['llm_calls'], 3)
        self.assertEqual(len(row['pseudocode_attempts']), 2)
        feedback = row['pseudocode_attempts'][0]['compiler_feedback'][0]
        self.assertEqual(feedback['code'], 'SIGNATURE_MISMATCH')
        repair_payload = json.loads(planner.calls[1][-1]['content'])
        self.assertEqual(repair_payload['compiler_feedback'][0]['code'], 'SIGNATURE_MISMATCH')
        self.assertEqual(repair_payload['source_specification']['entrypoint'], 'total')
        self.assertNotIn('HIDDEN_SECRET', json.dumps(planner.calls))
        self.assertEqual(inference_totals(row), (30, 3))

    def test_compiler_feedback_repair_stops_at_budget(self):
        planner = SequenceClient([('not valid code', 'stop')] * 3)
        row, planner, coder = self.run_arm(planner=planner, max_plan_repairs=2)
        self.assertFalse(row['artifact_valid'])
        self.assertFalse(row['pseudocode_valid'])
        self.assertEqual(row['pseudocode_repair_attempts'], 2)
        self.assertEqual(row['llm_calls'], 3)
        self.assertEqual(len(planner.calls), 3)
        self.assertEqual(len(coder.calls), 0)
        self.assertEqual(row['pseudocode_attempts'][-1]['compiler_feedback'][0]['code'],
                         'SYNTAX_ERROR')

    def test_syntax_feedback_contains_location(self):
        try:
            compile('def broken(:\n    pass', '<pseudocode>', 'exec')
        except SyntaxError as error:
            feedback = compiler_feedback(error)
        self.assertEqual(feedback['code'], 'SYNTAX_ERROR')
        self.assertEqual(feedback['line'], 1)
        self.assertIn('source_line', feedback)

    def test_shared_journal_does_not_call_model_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            first = JournalClient(client, Path(tmp))
            second = JournalClient(client, Path(tmp))
            a = first.complete(request()['messages'], 4096, 0)
            b = second.complete(request()['messages'], 4096, 0)
            self.assertEqual(a, b)
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(second.cache_hits, 1)

    def test_public_examples_for_mbpp_and_livecodebench(self):
        for bench, tests in [('mbpp', ['assert total([1, -1]) == 1']),
                             ('livecodebench', [{'input': '2\n1 -1\n', 'output': '0\n', 'testtype': 'stdin'}])]:
            rec = {'id': bench + ':synthetic', 'benchmark': bench,
                   'interface': 'function' if bench == 'mbpp' else 'stdio',
                   'entrypoint': 'total', 'public_tests': tests,
                   'reference': {'test': 'SECRET'}}
            req = request_for(rec, 'primary', 'Public task', 'Plan')
            task = json.loads(req['messages'][-1]['content'])
            self.assertEqual(len(task['public_examples']), 1)
            self.assertNotIn('SECRET', json.dumps(req))

    def test_public_examples_are_prompt_sidecar_not_graphir_state(self):
        rec = {'id': 'mbpp:synthetic', 'benchmark': 'mbpp', 'interface': 'function',
               'entrypoint': 'total', 'starter_code': TASK['starter_code'],
               'public_tests': ['assert total([2, -1]) == 2'],
               'reference': {'test': 'SECRET'}}
        req = request_for(rec, 'primary', TASK['task'], 'Generate pseudocode.')
        without, _, coder_without = self.run_arm('graphir', 'none',
                                                  example_context='none', req=req)
        with_examples, _, coder_with = self.run_arm('graphir', 'none',
                                                     example_context='public', req=req)
        payload_without = json.loads(coder_without.calls[0][-1]['content'])
        payload_with = json.loads(coder_with.calls[0][-1]['content'])
        self.assertNotIn('public_examples', payload_without)
        self.assertEqual(len(payload_with['public_examples']), 1)
        self.assertEqual(payload_without['graphir'], payload_with['graphir'])
        self.assertNotIn('public_examples', json.dumps(payload_with['graphir']))
        self.assertEqual(without['provided_public_example_count'], 0)
        self.assertEqual(with_examples['provided_public_example_count'], 1)

    def test_mbpp_entrypoint_is_inferred_from_consistent_public_asserts(self):
        rec = {'id': 'mbpp:synthetic', 'benchmark': 'mbpp', 'interface': 'function',
               'entrypoint': None, 'starter_code': '',
               'public_tests': ['assert target([1]) == 1', 'assert target([]) == 0']}
        req = request_for(rec, 'primary', 'Return the length.', 'Plan')
        task = json.loads(req['messages'][-1]['content'])
        self.assertEqual(task['entrypoint'], 'target')
        complex_case = dict(rec, public_tests=['assert angle_complex(0, 1j) == 1.57'])
        complex_task = json.loads(request_for(complex_case, 'primary', 'Angle.', 'Plan')
                                  ['messages'][-1]['content'])
        self.assertEqual(complex_task['entrypoint'], 'angle_complex')
        with self.assertRaisesRegex(ValueError, 'cannot infer'):
            request_for(dict(rec, public_tests=['assert first([]) == 0',
                                                'assert second([]) == 0']),
                        'primary', 'Ambiguous.', 'Plan')

    def test_one_leading_python_fence_is_extracted_before_trailing_prose(self):
        raw = '```python\ndef total(values):\n    return 0\n```\nExplanation follows.'
        self.assertEqual(strip_fence(raw), 'def total(values):\n    return 0')
        self.assertEqual(artifact_errors(raw, dict(TASK, starter_code='def total(values):\n    pass')), [])

    def test_valid_and_invalid_results_export_to_sandbox_jobs(self):
        row, _, _ = self.run_arm()
        tasks = {'humaneval:synthetic': {'entrypoint': 'total', 'reference': {'test': 'def check(candidate):\n    assert candidate([2,-1]) == 2'}, 'prompt': {'primary': ''}, 'starter_code': ''}}
        job = export_functional(tasks, {'humaneval:synthetic': row}, 'humaneval')[0]
        self.assertEqual(job['candidate'], PLAN.strip())
        self.assertTrue(job['generation_valid'])
        row['artifact_valid'] = False
        failed = export_functional(tasks, {'humaneval:synthetic': row}, 'humaneval')[0]
        self.assertEqual(failed['candidate'], '')

    def test_experiment_dispatch_has_no_legacy_generation_options(self):
        config = json.loads((ROOT / 'experiments/pseudocode_smoke_v23.json').read_text())
        with patch.object(run_experiments, 'run') as run:
            run_experiments.prepare(config, True)
            run_experiments.generate(config, True)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 8)
        self.assertTrue(all('pseudocode' in command[1] for command in commands))
        for command in commands:
            self.assertNotIn('--constraint-mode', command)
            self.assertNotIn('--contract-version', command)
            if 'run_pseudocode_pipeline.py' in command[1]:
                self.assertIn('--max-plan-repairs', command)

    def test_cli_resume_shared_plan_and_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rec = {'id': 'humaneval:synthetic', 'benchmark': 'humaneval', 'split': 'test',
                   'interface': 'function', 'entrypoint': 'total',
                   'starter_code': TASK['starter_code'], 'prompt': {'primary': TASK['task']}}
            (base / 'humaneval.jsonl').write_text(json.dumps(rec) + '\n')
            requests = base / 'requests.jsonl'
            with patch.object(sys, 'argv', ['build', '--input-dir', tmp, '--output', str(requests),
                                           '--benchmarks', 'humaneval', '--official-eval-only']):
                build_pseudocode_eval.main()
            client = FakeClient()
            paths = []
            for rep, context, examples in [('pseudocode', 'original', 'public'),
                                           ('graphir', 'original', 'public'),
                                           ('graphir', 'none', 'none')]:
                output = base / (rep + '-' + context) / 'results.jsonl'
                paths.append(output)
                argv = ['run', '--input', str(requests), '--output', str(output), '--model', client.model,
                        '--plans-dir', str(base / 'shared'), '--representation', rep,
                        '--source-context', context, '--example-context', examples]
                with patch.object(sys, 'argv', argv), patch.object(run_pseudocode_pipeline, 'Client', return_value=client):
                    run_pseudocode_pipeline.main()
                    run_pseudocode_pipeline.main()  # no duplicate rows or calls
                self.assertEqual(len(output.read_text().splitlines()), 1)
            rows = [json.loads(path.read_text()) for path in paths]
            self.assertEqual(len(client.calls), 4)  # one shared plan, three code samples
            self.assertEqual([r['actual_api_calls'] for r in rows], [2, 1, 1])
            self.assertEqual([r['cached_calls'] for r in rows], [0, 1, 1])
            self.assertEqual(len({r['pseudocode_raw'] for r in rows}), 1)
            changed = argv + ['--temperature', '0.5']
            with patch.object(sys, 'argv', changed), self.assertRaisesRegex(ValueError, 'identity changed'):
                run_pseudocode_pipeline.main()


if __name__ == '__main__':
    unittest.main()
