#!/usr/bin/env python3
"""Aggregate generation gates and available functional Pass@1 results by condition/benchmark."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator
from official_results import load_official, inference_totals


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def wilson(successes: int, total: int) -> tuple[float, float]:
    if not total:
        return 0.0, 0.0
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return center - margin, center + margin


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    for condition_dir in sorted(path for path in args.experiment_dir.iterdir() if path.is_dir()):
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        latest: dict[str, dict[str, Any]] = {}
        for result in iter_jsonl(condition_dir / "results.jsonl"):
            if result.get("custom_id"):
                latest[result["custom_id"]] = result
        for result in latest.values():
            benchmark = result.get("metadata", {}).get("benchmark", "unknown")
            grouped[benchmark].append(result)
        for benchmark, results in sorted(grouped.items()):
            total = len(results)
            graph_rows = [row for row in results if "graph_raw" in row]
            parsel_rows = [row for row in results if "parsel_raw" in row]
            evaluation_path = condition_dir / "evaluation" / benchmark / "results.jsonl"
            evaluations = list(iter_jsonl(evaluation_path))
            evaluations = list({r['task_id']:r for r in evaluations}.values())
            if benchmark in {'bigcodebench','livecodebench'}:
                evaluations = load_official(benchmark, evaluation_path.parent)
            expected_ids = {r.get('metadata',{}).get('task_id') for r in results}
            actual_ids = [r.get('task_id') for r in evaluations]
            if len(actual_ids) != len(set(actual_ids)):
                raise ValueError('duplicate evaluation task IDs')
            complete = bool(evaluations) and set(actual_ids) == expected_ids
            infra_errors = sum(r.get('status') in {'container_error','runner_error','invalid_worker_output','container_timeout'} for r in evaluations)
            complete = complete and not infra_errors
            totals = [inference_totals(r) for r in results]
            passed = sum(bool(row.get("passed")) for row in evaluations)
            lower, upper = wilson(passed, len(evaluations))
            public_checked = [r['example_valid'] for r in evaluations if isinstance(r.get('example_valid'), bool)]
            port_checked = [r['port_contracts_valid'] for r in evaluations if isinstance(r.get('port_contracts_valid'), bool)]
            rows.append({
                "condition": condition_dir.name,
                "benchmark": benchmark,
                "generated": total,
                "graph_parse_rate": (
                    sum(row.get("graph") is not None for row in graph_rows) / len(graph_rows)
                    if graph_rows else ""
                ),
                "graph_valid_rate": (
                    sum(bool(row.get("graph_valid")) for row in graph_rows) / len(graph_rows)
                    if graph_rows else ""
                ),
                "parsel_valid_rate": (
                    sum(bool(row.get("parsel_valid")) for row in parsel_rows) / len(parsel_rows)
                    if parsel_rows else ""
                ),
                "artifact_valid_rate": sum(bool(row.get("artifact_valid")) for row in results) / total if total else 0.0,
                "mean_llm_calls": (
                    sum(int(row.get("llm_calls", 0)) for row in results) / total if total else 0.0
                ),
                "evaluated": len(evaluations),
                "evaluation_complete": complete,
                "infrastructure_errors": infra_errors,
                "public_examples_evaluated": len(public_checked),
                "public_example_pass_rate": sum(public_checked) / len(public_checked) if public_checked else "",
                "port_contracts_conclusive": len(port_checked),
                "port_contract_failures": sum(not value for value in port_checked),
                "diagnostic_infrastructure_errors": sum(
                    r.get(key, {}).get('status') in {'container_error','runner_error','invalid_worker_output','container_timeout'}
                    for r in evaluations for key in ('public_evaluation','port_evaluation')),
                "mean_tokens": sum(t[0] for t in totals) / total if total else 0,
                "mean_inference_seconds": sum(t[1] for t in totals) / total if total else 0,
                "passed": passed,
                "pass_at_1": passed / len(evaluations) if complete else "",
                "pass_at_1_ci95_low": lower if complete else "",
                "pass_at_1_ci95_high": upper if complete else "",
            })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["condition", "benchmark"]
    with args.output.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} summary rows to {args.output}")


if __name__ == "__main__":
    main()
