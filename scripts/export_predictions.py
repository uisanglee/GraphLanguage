#!/usr/bin/env python3
"""Convert GraphDSL/direct result JSONL into benchmark evaluator input formats."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterator

from build_qwen_eval import is_official_evaluation_task
from validate_artifact import strip_fence


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def load_tasks(
    input_dir: Path, benchmark: str, official_eval_only: bool
) -> dict[str, dict[str, Any]]:
    path = input_dir / f"{benchmark}.jsonl"
    return {
        row["id"]: row for row in iter_jsonl(path)
        if not official_eval_only or is_official_evaluation_task(row)
    }


def selected_results(
    path: Path, benchmark: str, prompt_variant: str
) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(path):
        metadata = row.get("metadata", {})
        if metadata.get("benchmark") != benchmark:
            continue
        if metadata.get("prompt_variant", "primary") != prompt_variant:
            continue
        task_id = metadata.get("task_id")
        if not task_id:
            continue
        if task_id in selected:
            previous = selected[task_id]
            previous_failed = bool(previous.get("pipeline_error") or previous.get("generation_error"))
            current_failed = bool(row.get("pipeline_error") or row.get("generation_error"))
            if not previous_failed and not current_failed:
                raise ValueError(f"duplicate successful result for {task_id} and variant {prompt_variant}")
            if not previous_failed and current_failed:
                continue
        selected[task_id] = row
    return selected


def export_functional(
    tasks: dict[str, dict[str, Any]], results: dict[str, dict[str, Any]], benchmark: str
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for task_id, result in sorted(results.items()):
        task = tasks[task_id]
        candidate = strip_fence(result.get("generated_artifact", ""))
        if benchmark == "humaneval":
            test_source = task["reference"]["test"]
            invocation = f"check({task['entrypoint']})"
        else:
            tests = task.get("public_tests", [])
            test_source = "\n".join(tests)
            invocation = ""
        jobs.append({
            "task_id": task_id,
            "benchmark": benchmark,
            "candidate": candidate,
            "setup": task.get("starter_code", "") if benchmark == "mbpp" else "",
            "test_source": test_source,
            "invocation": invocation,
        })
    return jobs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, default=Path("data/normalized"))
    parser.add_argument(
        "--benchmark", required=True,
        choices=["humaneval", "mbpp", "bigcodebench", "livecodebench", "swebench"],
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prompt-variant", default="primary")
    parser.add_argument("--model-name", default="graphdsl-experiment")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--official-eval-only", action="store_true")
    args = parser.parse_args()

    tasks = load_tasks(args.input_dir, args.benchmark, args.official_eval_only)
    results = selected_results(args.results, args.benchmark, args.prompt_variant)
    unknown = sorted(set(results) - set(tasks))
    if unknown:
        raise ValueError(f"results contain unknown tasks: {unknown[:5]}")
    if not args.allow_partial and len(results) != len(tasks):
        raise ValueError(
            f"{args.benchmark}: expected {len(tasks)} results, found {len(results)}; "
            "use --allow-partial only for smoke tests"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.benchmark in {"humaneval", "mbpp"}:
        rows = export_functional(tasks, results, args.benchmark)
        with args.output.open("w", encoding="utf-8") as output:
            for row in rows:
                output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    elif args.benchmark == "bigcodebench":
        with args.output.open("w", encoding="utf-8") as output:
            for task_id, result in sorted(results.items()):
                task = tasks[task_id]
                code = strip_fence(result.get("generated_artifact", ""))
                row = {
                    "task_id": task["metadata"]["task_id"],
                    "solution": code,
                    "raw_solution": result.get("generated_artifact", ""),
                }
                output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    elif args.benchmark == "livecodebench":
        rows = [
            {
                "question_id": tasks[task_id]["metadata"]["question_id"],
                "code_list": [strip_fence(result.get("generated_artifact", ""))],
            }
            for task_id, result in sorted(results.items())
        ]
        args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        with args.output.open("w", encoding="utf-8") as output:
            for task_id, result in sorted(results.items()):
                task = tasks[task_id]
                row = {
                    "instance_id": task["metadata"]["instance_id"],
                    "model_name_or_path": args.model_name,
                    "model_patch": strip_fence(result.get("generated_artifact", "")),
                }
                output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"exported {len(results)} {args.benchmark} predictions to {args.output}")


if __name__ == "__main__":
    main()
