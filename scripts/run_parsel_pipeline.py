#!/usr/bin/env python3
"""NL -> Parsel -> unmodified upstream SCC synthesis, n=k=1, inside Docker."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
from inference_journal import JournalClient, run_identity
from run_qwen_pipeline import Client, completed_ids, iter_jsonl, with_high_level_plan, selected_requests
from parsel_upstream import run_upstream, verify_upstream, COMMIT
from validate_artifact import validate_artifact, strip_fence
from run_safe_functional_eval import require_docker


def benchmark_wrapper(source, task, root):
    entry = task.get('entrypoint') or ''
    if '.' in entry:
        starter = task['starter_code'].rstrip()
        try: tree = ast.parse(starter + '\n        pass\n')
        except SyntaxError: tree = ast.parse(starter)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef))
        arguments = [a.arg for a in method.args.args if a.arg != 'self']
        method.body = [ast.Return(ast.Call(func=ast.Name(id=root, ctx=ast.Load()),
            args=[ast.Name(id=a, ctx=ast.Load()) for a in arguments], keywords=[]))]
        cls.body = [method]
        source += '\n' + ast.unparse(ast.fix_missing_locations(cls)) + '\n'
    elif task['interface_mode'] == 'stdio':
        source += f"\nif __name__ == '__main__':\n    {root}()\n"
    return source


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--base-url', default='http://localhost:8000/v1')
    p.add_argument('--model', required=True)
    p.add_argument('--api-key', default=os.environ.get('OPENAI_API_KEY',''))
    p.add_argument('--high-level-plan', action='store_true')
    p.add_argument('--plan-system-prompt', type=Path, default=Path('prompts/high_level_plan.md'))
    p.add_argument('--plans-dir', type=Path)
    p.add_argument('--max-plan-tokens', type=int, default=2048)
    p.add_argument('--max-parsel-tokens', type=int, default=8192)
    p.add_argument('--temperature', type=float, default=0.0)
    p.add_argument('--timeout', type=int, default=1800)
    p.add_argument('--limit', type=int)
    p.add_argument('--condition-name', default='parsel-original-1x1')
    p.add_argument('--parsel-image', default='graphdsl-parsel:0.1')
    args = p.parse_args()
    verify_upstream()
    require_docker()
    plan_system = args.plan_system_prompt.read_text().strip()
    settings = {k:v for k,v in vars(args).items() if k not in {'api_key','limit'}}
    settings.update(upstream_commit=COMMIT, plan_system=plan_system)
    run_identity(args.output, settings, args.input)
    raw = Client(args.base_url,args.model,args.api_key,args.timeout)
    done = completed_ids(args.output)
    processed = 0
    with args.output.open('a') as stream:
        for request in selected_requests(args.input, args.limit):
            cid = request['custom_id']
            if cid in done: continue
            key = hashlib.sha256(cid.encode()).hexdigest()
            cache = args.output.parent / 'inference' / key
            client = JournalClient(raw, cache / 'planner')
            row = dict(custom_id=cid, metadata=request['metadata'], condition=args.condition_name,
                       model=args.model, upstream_commit=COMMIT, llm_calls=0)
            try:
                messages = request['messages']
                if args.high_level_plan:
                    plans = JournalClient(raw, (args.plans_dir or args.output.parent / 'plans') / key)
                    plan, info = plans.complete([{'role':'system','content':plan_system}, messages[-1]],
                        args.max_plan_tokens,args.temperature)
                    row.update(high_level_plan=plan, plan_inference=info)
                    row['llm_calls'] += 1
                    messages = with_high_level_plan(messages,plan)
                text, info = client.complete(messages,args.max_parsel_tokens,args.temperature)
                row.update(parsel_raw=text,parsel_inference=info)
                row['llm_calls'] += 1
                result = run_upstream(strip_fence(text),args.model,args.base_url,args.api_key,
                                      cache / 'upstream',args.parsel_image,args.timeout)
                row.update(result)
                row['llm_calls'] += len(result['function_inference'])
                if 'generated_artifact' in row:
                    task = json.loads(request['messages'][-1]['content'])
                    row['generated_artifact'] = benchmark_wrapper(row['generated_artifact'],task,row['root_name'])
                    row['artifact_errors'] = validate_artifact(row['generated_artifact'],task['interface_mode'])
                    row['artifact_valid'] = not row['artifact_errors']
            except Exception as error:
                row['pipeline_error'] = f'{type(error).__name__}: {error}'
            stream.write(json.dumps(row,ensure_ascii=False)+'\n')
            stream.flush()
            processed += 1
            print(f'{processed}: {cid} artifact_valid={row.get("artifact_valid",False)}',flush=True)

if __name__ == '__main__': main()
