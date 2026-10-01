"""Build leakage-free requests for the structured pseudocode experiment."""
import argparse
import hashlib
import json
from pathlib import Path

from benchmark_requests import iter_records, safe_task, variants, is_official_evaluation_task
from public_interface import fixed_interface
from public_examples import preserved_public_examples

ROOT = Path(__file__).resolve().parents[1]


def request_for(record, variant, text, system):
    if record['interface'] not in {'function', 'stdio'}:
        raise ValueError('repository patch generation is outside the pseudocode profile')
    task = safe_task(record, text, variant)
    # Metadata is unnecessary to the model; in particular no opaque official
    # metadata object is forwarded to either inference stage.
    task.pop('metadata', None)
    task['public_examples'] = preserved_public_examples(record)
    if task['interface_mode'] == 'function' and not task.get('entrypoint'):
        inferred = {
            example.get('call', {}).get('entrypoint')
            for example in task['public_examples']
            if example.get('structured') and example.get('call', {}).get('entrypoint')
        }
        if len(inferred) != 1:
            raise ValueError(record['id'] + ': cannot infer one public entrypoint')
        task['entrypoint'] = inferred.pop()
    task['fixed_interface'] = fixed_interface(task)
    return {'custom_id': f"{record['id']}:{variant}",
            'messages': [{'role': 'system', 'content': system},
                         {'role': 'user', 'content': json.dumps(task, ensure_ascii=False)}],
            'metadata': {'benchmark': record['benchmark'], 'task_id': record['id'],
                         'prompt_variant': variant, 'interface_mode': record['interface'],
                         'pseudocode_prompt_sha256': hashlib.sha256(system.encode()).hexdigest()}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input-dir', type=Path, default=ROOT / 'data/normalized')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--benchmarks', nargs='+')
    parser.add_argument('--official-eval-only', action='store_true')
    args = parser.parse_args()
    system = (ROOT / 'prompts/nl_to_pseudocode.md').read_text()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix('.tmp')
    count = 0
    with temp.open('w') as output:
        for record in iter_records(args.input_dir, set(args.benchmarks) if args.benchmarks else None):
            if args.official_eval_only and not is_official_evaluation_task(record):
                continue
            for variant, text in variants(record, False):
                output.write(json.dumps(request_for(record, variant, text, system), ensure_ascii=False) + '\n')
                count += 1
    temp.replace(args.output)
    print(f'wrote {count} pseudocode requests to {args.output}')


if __name__ == '__main__':
    main()
