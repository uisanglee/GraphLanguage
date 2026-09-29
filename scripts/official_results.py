"""Normalize official per-task output without changing its correctness decision."""
import json
from pathlib import Path


def load_official(benchmark: str, directory: Path) -> list[dict]:
    if benchmark == 'bigcodebench':
        path = directory / 'predictions_eval_results.json'
        if not path.exists(): return []
        source = json.loads(path.read_text())
        rows = []
        for task_id, candidates in source['eval'].items():
            if len(candidates) != 1:
                raise ValueError(f'{task_id}: 1x1 expected exactly one evaluated candidate')
            rows.append(dict(task_id='bigcodebench:' + task_id,
                             passed=candidates[0]['status'] == 'pass', status=candidates[0]['status']))
        return rows
    if benchmark == 'livecodebench':
        path = directory / 'predictions_codegeneration_output_eval_all.json'
        if not path.exists(): return []
        rows = []
        for task in json.loads(path.read_text()):
            graded = task['graded_list']
            if len(graded) != 1 or not isinstance(graded[0], bool):
                raise ValueError('expected one boolean grade per LiveCodeBench task')
            rows.append(dict(task_id=f"livecodebench:{task['platform']}:{task['question_id']}",
                             passed=graded[0]))
        return rows
    return []


def inference_totals(row):
    """Collect telemetry dictionaries, including node and upstream function calls, once."""
    tokens = 0
    elapsed = 0.0
    def visit(value):
        nonlocal tokens, elapsed
        if isinstance(value, dict):
            if 'usage' in value:
                usage = value.get('usage') or {}
                tokens += usage.get('total_tokens', usage.get('prompt_tokens',0) + usage.get('completion_tokens',0))
                elapsed += value.get('elapsed_seconds',0)
            else:
                for child in value.values(): visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    visit({k:v for k,v in row.items() if k.endswith('_inference') or k == 'node_results'})
    return tokens, elapsed
