"""One plan sample -> deterministic GraphIR -> one complete Python sample.

All arms share exactly the same cached plan. No execution, repair, selection or
hidden test access occurs during generation. Evaluation is a separate sandbox.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

from inference_journal import JournalClient, run_identity
from llm_client import Client, completed_ids, selected_requests
from pseudocode_graphir import VERSION, compile_pseudocode, artifact_errors

ROOT = Path(__file__).resolve().parents[1]


def generate_one(request, planner, synthesizer, *, representation, source_context,
                 example_context, system, max_plan_tokens=4096, max_code_tokens=8192,
                 temperature=0):
    task = json.loads(request['messages'][-1]['content'])
    public_examples = task.get('public_examples', [])
    source_specification = dict(task)
    source_specification.pop('public_examples', None)
    record = {'custom_id': request['custom_id'], 'metadata': request['metadata'],
              'representation': representation, 'source_context': source_context,
              'example_context': example_context,
              'compiler_version': VERSION, 'artifact_valid': False,
              'pseudocode_valid': False, 'graph_valid': False,
              'preserved_public_example_count': len(public_examples),
              'provided_public_example_count': len(public_examples) if example_context == 'public' else 0,
              'llm_calls': 0}
    try:
        if representation == 'direct':
            for key in ('compiler_version', 'pseudocode_valid', 'graph_valid'):
                record.pop(key)
            record['llm_calls'] = 1
            code, inference = synthesizer.complete(
                [{'role': 'system', 'content': system},
                 {'role': 'user', 'content': json.dumps({
                     'source_specification': source_specification,
                     **({'public_examples': public_examples}
                        if example_context == 'public' and public_examples else {})
                 }, ensure_ascii=False)}],
                max_code_tokens, temperature)
            errors = artifact_errors(code, task)
            if inference.get('finish_reason') == 'length':
                errors.append('Python generation truncated')
            record.update(generated_artifact=code, code_inference=inference,
                          artifact_errors=errors, artifact_valid=not errors)
            return record
        record['llm_calls'] += 1
        raw, inference = planner.complete(request['messages'], max_plan_tokens, temperature)
        record.update(pseudocode_raw=raw, plan_inference=inference)
        if inference.get('finish_reason') == 'length':
            record['pseudocode_errors'] = ['pseudocode generation truncated']
            return record
        try:
            graph = compile_pseudocode(raw, task)
        except (ValueError, TypeError, SyntaxError, RecursionError) as error:
            record['pseudocode_errors'] = [f'{type(error).__name__}: {error}']
            return record
        record.update(pseudocode_valid=True, graph_valid=True, graph=graph,
                      graph_raw=json.dumps(graph, ensure_ascii=False))
        payload = {'interface': graph['interface']}
        payload['graphir' if representation == 'graphir' else 'pseudocode'] = (
            graph if representation == 'graphir' else raw)
        if source_context == 'original':
            payload['source_specification'] = source_specification
        if example_context == 'public' and public_examples:
            payload['public_examples'] = public_examples
        record['llm_calls'] += 1
        code, inference = synthesizer.complete(
            [{'role': 'system', 'content': system},
             {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
            max_code_tokens, temperature)
        errors = artifact_errors(code, task)
        if inference.get('finish_reason') == 'length':
            errors.append('Python generation truncated')
        record.update(generated_artifact=code, code_inference=inference,
                      artifact_errors=errors, artifact_valid=not errors)
    except Exception as error:
        record['pipeline_error'] = f'{type(error).__name__}: {error}'
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base-url', default='http://localhost:8000/v1')
    parser.add_argument('--model', required=True)
    parser.add_argument('--api-key', default=os.environ.get('OPENAI_API_KEY', ''))
    parser.add_argument('--temperature', type=float, default=0)
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--condition-name', default='source-graphir-examples-1x1')
    parser.add_argument('--representation', choices=['direct', 'pseudocode', 'graphir'], default='graphir')
    parser.add_argument('--source-context', choices=['original', 'none'], default='original')
    parser.add_argument('--example-context', choices=['public', 'none'], default='public')
    parser.add_argument('--plans-dir', type=Path, required=True)
    parser.add_argument('--max-plan-tokens', type=int, default=4096)
    parser.add_argument('--max-code-tokens', type=int, default=8192)
    args = parser.parse_args()
    if args.representation == 'direct' and args.source_context != 'original':
        parser.error('direct requires the original source context')
    system = (ROOT / 'prompts/pseudocode_to_python.md').read_text()
    settings = {k: v for k, v in vars(args).items() if k not in {'api_key', 'limit'}}
    settings.update(system=system, compiler_version=VERSION,
                    compiler_sha256=hashlib.sha256((ROOT / 'scripts/pseudocode_graphir.py').read_bytes()).hexdigest())
    run_identity(args.output, settings, args.input)
    client = Client(args.base_url, args.model, args.api_key, args.timeout)
    # Journal keys also need endpoint provenance: identical model names can
    # refer to different checkpoints served at different addresses.
    endpoint = hashlib.sha256((args.base_url + '\n' + args.model).encode()).hexdigest()
    done = completed_ids(args.output)
    with args.output.open('a') as output:
        for request in selected_requests(args.input, args.limit):
            if request['custom_id'] in done:
                continue
            key = hashlib.sha256(request['custom_id'].encode()).hexdigest()
            planner = JournalClient(client, args.plans_dir / endpoint / key)
            synthesizer = JournalClient(client, args.output.parent / 'inference' / key)
            record = generate_one(request, planner, synthesizer,
                representation=args.representation, source_context=args.source_context,
                example_context=args.example_context, system=system, max_plan_tokens=args.max_plan_tokens,
                max_code_tokens=args.max_code_tokens, temperature=args.temperature)
            record.update(condition=args.condition_name, model=args.model)
            record.update(actual_api_calls=planner.requests_sent + synthesizer.requests_sent,
                          cached_calls=planner.cache_hits + synthesizer.cache_hits)
            output.write(json.dumps(record, ensure_ascii=False) + '\n')
            output.flush()
            print(f"{request['custom_id']}: pseudocode_valid={record.get('pseudocode_valid')} artifact_valid={record['artifact_valid']}")


if __name__ == '__main__':
    main()
