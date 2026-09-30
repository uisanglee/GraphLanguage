#!/usr/bin/env python3
"""Build chat-style Qwen requests from normalized benchmark records."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator

from retrieve_demonstrations import load_catalog, planner_demo_messages, retrieve
from graphir_contracts import prepare_contract_task


def iter_records(input_dir: Path, selected: set[str] | None) -> Iterator[dict[str, Any]]:
    for path in sorted(input_dir.glob("*.jsonl")):
        if selected and path.stem not in selected:
            continue
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                yield normalize_interface(json.loads(line))


def normalize_interface(record: dict[str, Any]) -> dict[str, Any]:
    """Repair both old on-disk LCB rows and new downloads using starter signatures."""
    if record.get("benchmark") != "livecodebench":
        return record
    record = dict(record)
    starter = record.get("starter_code", "").rstrip()
    if not starter:
        return record
    try:
        tree = ast.parse(starter + "\n        pass\n")
    except SyntaxError:
        tree = ast.parse(starter)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for method in node.body:
                if isinstance(method, ast.FunctionDef):
                    record["interface"] = "function"
                    record["entrypoint"] = f"{node.name}.{method.name}"
                    return record
        if isinstance(node, ast.FunctionDef):
            record["interface"] = "function"
            record["entrypoint"] = node.name
            return record
    return record


def safe_task(record: dict[str, Any], prompt_text: str, variant: str) -> dict[str, Any]:
    metadata = record.get("metadata", {})
    allowed_metadata = {
        key: metadata[key]
        for key in (
            "task_id", "title", "platform", "question_id", "contest_id", "contest_date",
            "difficulty", "repo", "base_commit", "instance_id", "version", "libs",
            "official_metadata",
        )
        if key in metadata and metadata[key] not in (None, "")
    }
    return {
        "task_id": record["id"],
        "benchmark": record["benchmark"],
        "interface_mode": record["interface"],
        "prompt_variant": variant,
        "task": prompt_text,
        "starter_code": record.get("starter_code", ""),
        "entrypoint": record.get("entrypoint"),
        "metadata": allowed_metadata,
    }


def is_official_evaluation_task(record: dict[str, Any]) -> bool:
    """Select published benchmark test sets while retaining all normalized prompts on disk."""

    if record.get("benchmark") == "mbpp":
        task_id = int(record.get("metadata", {}).get("task_id", -1))
        return 11 <= task_id <= 510
    return record.get("split") == "test" or record.get("benchmark") in {
        "bigcodebench", "livecodebench", "swebench"
    }
def variants(record: dict[str, Any], expand: bool) -> Iterator[tuple[str, str]]:
    if expand:
        seen: set[str] = set()
        for name, value in record["prompt"].get("variants", {}).items():
            if value and value not in seen:
                seen.add(value)
                yield name, value
        if not seen:
            yield "primary", record["prompt"]["primary"]
    else:
        yield "primary", record["prompt"]["primary"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path("data/normalized"))
    parser.add_argument("--output", type=Path, default=Path("data/qwen/nl_to_graphdsl.jsonl"))
    parser.add_argument('--planner-format', choices=['graph', 'contracts'], default='graph')
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
    args = parser.parse_args()

    args.system_prompt = args.system_prompt or Path('prompts/nl_to_contracts.md' if args.planner_format == 'contracts' else 'prompts/nl_to_graphdsl.md')
    args.demo_catalog = args.demo_catalog or Path('demonstrations/contracts_catalog.json' if args.planner_format == 'contracts' else 'demonstrations/catalog.json')

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
                    },
                }
                output.write(json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n")
                count += 1
    temporary.replace(args.output)
    print(f"wrote {count} requests to {args.output}")


if __name__ == "__main__":
    main()
