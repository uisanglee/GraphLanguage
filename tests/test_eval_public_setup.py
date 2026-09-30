"""Handwritten fixtures only. Never execute submitted model code on the host."""
import sys
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'sandbox'))
from public_examples import public_checks, humaneval_setup
from export_predictions import export_functional
from functional_worker import execution_source
from run_safe_functional_eval import verify_image
import run_experiments


class PublicSetupTests(unittest.TestCase):
    def task(self):
        prefix = '''from __future__ import annotations
import math
def helper(x):
    return math.floor(x) * 2
def twice(x: float) -> int:
    """Double the floor.
    >>> twice(3.5)
    6
    """
'''
        return dict(entrypoint='twice', starter_code=prefix, prompt={'primary': prefix},
                    reference={'test': 'def check(candidate):\n    assert candidate(3.5) == helper(3.5)',
                               'canonical_solution': 'SECRET_REFERENCE'})

    def test_helpers_and_future_imports_shared_without_target_stub(self):
        task = self.task()
        candidate = 'from __future__ import annotations\ndef twice(x):\n    return helper(x)'
        result = dict(artifact_valid=True, generated_artifact=candidate)
        job = export_functional({'fixture': task}, {'fixture': result}, 'humaneval')[0]
        self.assertNotIn('def twice', job['setup'])
        self.assertNotIn('SECRET_REFERENCE', job['setup'])
        self.assertIn('def helper', job['setup'])
        namespace = {}
        exec(execution_source(job), namespace)
        self.assertEqual(namespace['twice'](4.9), 8)

    def test_docstring_quotes_are_not_expected_output(self):
        info = public_checks(self.task(), 'humaneval')
        self.assertEqual(info['count'], 1)
        self.assertEqual(info['status'], 'available')
        exec(info['source'], {'twice': lambda x: int(x) * 2})
        with self.assertRaises(AssertionError):
            with contextlib.redirect_stdout(io.StringIO()):
                exec(info['source'], {'twice': lambda x: -1})

    def test_helper_examples_not_scored_as_target_examples(self):
        task = self.task()
        task['prompt']['primary'] = task['starter_code'].replace(
            '    return math.floor(x) * 2', '    """>>> helper(2)\n    4\n    """\n    return math.floor(x) * 2')
        self.assertEqual(public_checks(task, 'humaneval')['count'], 1)

    def test_incomplete_signature_and_malformed_prefix(self):
        task = dict(entrypoint='f', starter_code='import math\ndef f(x):',
                    prompt={'primary': 'import math\ndef f(x):'})
        self.assertEqual(humaneval_setup(task), 'import math')
        self.assertEqual(public_checks(task, 'humaneval')['status'], 'unavailable')
        task['prompt']['primary'] = 'def f( broken'
        self.assertEqual(public_checks(task, 'humaneval')['status'], 'unparseable')

    def test_candidate_cannot_silently_pass_when_assertions_fail(self):
        with self.assertRaises(AssertionError):
            exec(execution_source(dict(setup='x = 1', candidate='x = 2',
                                       test_source='assert x == 1')))

    def test_stale_docker_worker_is_rejected(self):
        digest = hashlib.sha256((ROOT / 'sandbox/functional_worker.py').read_bytes()).hexdigest()
        info = {'Id': 'fixture-id', 'Config': {'Labels': {'org.graphir.functional-worker-sha256': digest}}}
        with patch('run_safe_functional_eval.subprocess.run', return_value=SimpleNamespace(
                returncode=0, stdout=json.dumps([info]))):
            self.assertEqual(verify_image('fixture', ROOT), 'fixture-id')
        info['Config']['Labels'] = {}
        with patch('run_safe_functional_eval.subprocess.run', return_value=SimpleNamespace(
                returncode=0, stdout=json.dumps([info]))):
            with self.assertRaisesRegex(RuntimeError, '--build-image'):
                verify_image('fixture', ROOT)

    def test_reevaluation_skips_generation_and_preserves_old_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            config = dict(output_dir=directory, input_dir='data/normalized', benchmarks=['humaneval'],
                          generation={}, conditions=[dict(name='fixture')], evaluation={})
            config_path = Path(directory) / 'config.json'
            config_path.write_text(json.dumps(config))
            commands = []
            argv = ['runner', '--config', str(config_path), '--stage', 'reevaluate',
                    '--evaluation-subdir', 'evaluation-fixed']
            with patch.object(sys, 'argv', argv), patch.object(run_experiments, 'run',
                    side_effect=lambda cmd, dry: commands.append(cmd)):
                run_experiments.main()
            self.assertEqual(len(commands), 3)
            self.assertTrue(commands[0][1].endswith('export_predictions.py'))
            self.assertTrue(commands[1][1].endswith('run_safe_functional_eval.py'))
            self.assertIn('--build-image', commands[1])
            self.assertIn(str(Path(directory) / 'summary-evaluation-fixed.csv'), commands[2])
            self.assertIn(str(Path(directory) / 'fixture/evaluation-fixed/humaneval/results.jsonl'), commands[1])
            (Path(directory) / 'fixture/evaluation-fixed').mkdir(parents=True)
            with patch.object(sys, 'argv', argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    run_experiments.main()


if __name__ == '__main__':
    unittest.main()
