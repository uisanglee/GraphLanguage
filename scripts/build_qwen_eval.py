#!/usr/bin/env python3
"""Build chat-style Qwen requests from normalized benchmark records."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from retrieve_demonstrations import load_catalog, planner_demo_messages, retrieve
from graphir_contracts import prepare_contract_task
from public_examples import preserved_public_examples


from benchmark_requests import iter_records, normalize_interface, safe_task, is_official_evaluation_task, variants


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path("data/normalized"))
    parser.add_argument("--output", type=Path, default=Path("data/qwen/nl_to_graphdsl.jsonl"))
    parser.add_argument('--planner-format', choices=['graph', 'contracts'], default='graph')
    parser.add_argument('--contract-version', choices=['1', '2', '3'], default='1')
    parser.add_argument("--system-prompt", type=Path)
    parser.add_argument(
        "--demo-catalog", type=Path
    )
    parser.add_argument(
        "--num-demonstrations", type=int, default=1,
        help="Retrieve this many same-profile few-shot examples for each task; use 0 to disable.",
    )
    parser.add_argument("--benchmarks", nargs="+")
    parser.add_argument(
        "--expand-variants",
        action="store_true",
        help="Emit all distinct prompt variants, including both BigCodeBench prompt styles.",
    )
    parser.add_argument(
        "--official-eval-only", action="store_true",
        help="Use published test splits (notably MBPP task IDs 11-510).",
    )
    parser.add_argument(
        '--preserve-public-examples', action=argparse.BooleanOptionalAction, default=True,
        help='Attach literal original public examples to contract tasks (default: enabled).',
    )
    args = parser.parse_args()

    suffix = '_' + 'v' + args.contract_version if args.contract_version in {'2', '3'} else ''
    args.system_prompt = args.system_prompt or Path(f'prompts/nl_to_contracts{suffix}.md' if args.planner_format == 'contracts' else 'prompts/nl_to_graphdsl.md')
    args.demo_catalog = args.demo_catalog or Path(f'demonstrations/contracts_catalog{suffix}.json' if args.planner_format == 'contracts' else 'demonstrations/catalog.json')

    system = args.system_prompt.read_text(encoding="utf-8").strip()
    system_sha256 = hashlib.sha256(system.encode("utf-8")).hexdigest()
    catalog = load_catalog(args.demo_catalog) if args.num_demonstrations else []
    selected = set(args.benchmarks) if args.benchmarks else None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    count = 0
    with temporary.open("w", encoding="utf-8") as output:
        for record in iter_records(args.input_dir, selected):
            if args.official_eval_only and not is_official_evaluation_task(record):
                continue
            if record['interface'] == 'repository_patch':
                raise ValueError('Repository editing is outside the compact GraphIR generation profile')
            for variant, prompt_text in variants(record, args.expand_variants):
                task = safe_task(record, prompt_text, variant)
                if args.planner_format == 'contracts':
                    task = prepare_contract_task(task)
                    public = preserved_public_examples(record) if args.preserve_public_examples else []
                demonstrations = retrieve(
                    catalog,
                    query="\n".join([prompt_text, record.get("starter_code", "")]),
                    interface=record["interface"],
                    benchmark=record["benchmark"],
                    limit=args.num_demonstrations,
                )
                request = {
                    "custom_id": f"{record['id']}:{variant}",
                    "messages": [
                        {"role": "system", "content": system},
                        *planner_demo_messages(demonstrations, fixed_contract_interface='fixed_interface' in task),
                        {
                            "role": "user",
                            "content": json.dumps(task, ensure_ascii=False),
                        },
                    ],
                    "metadata": {
                        "benchmark": record["benchmark"],
                        "task_id": record["id"],
                        "prompt_variant": variant,
                        "demonstration_ids": [item["id"] for item in demonstrations],
                        "planner_prompt_sha256": system_sha256,
                        **({'planner_format': 'contracts'} if args.planner_format == 'contracts' else {}),
                        **({'contract_version': args.contract_version} if args.planner_format == 'contracts' else {}),
                        **({'preserved_public_example_count': len(public)}
                           if args.planner_format == 'contracts' else {}),
                    },
                    **({'preserved_public_examples': public}
                       if args.planner_format == 'contracts' and public else {}),
                }
                output.write(json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n")
                count += 1
    temporary.replace(args.output)
    print(f"wrote {count} requests to {args.output}")


if __name__ == "__main__":
    main()
