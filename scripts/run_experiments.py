#!/usr/bin/env python3
"""Prepare, generate, export, and evaluate a reproducible experiment matrix."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def absolute(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def run(command: list[str], dry_run: bool) -> None:
    print("+ " + json.dumps(command, ensure_ascii=False))
    if not dry_run:
        subprocess.run(command, cwd=ROOT, check=True)


def common_generation(config: dict[str, Any], condition: dict[str, Any]) -> list[str]:
    generation = config["generation"]
    args = [
        "--base-url", generation["base_url"],
        "--model", generation["model"],
        "--temperature", str(generation.get("temperature", 0.0)),
        "--condition-name", condition["name"],
    ]
    if generation.get("limit") is not None:
        args += ["--limit", str(generation["limit"])]
    return args


def prepare(config: dict[str, Any], dry_run: bool) -> None:
    input_dir = absolute(config["input_dir"])
    output_dir = absolute(config["output_dir"])
    for condition in config["conditions"]:
        benchmarks = condition.get("benchmarks", config["benchmarks"])
        condition_dir = output_dir / condition["name"]
        requests = condition_dir / "requests.jsonl"
        if condition["kind"] == "pseudocode":
            command = [
                sys.executable, str(ROOT / "scripts" / "build_pseudocode_eval.py"),
                "--input-dir", str(input_dir), "--output", str(requests),
                "--benchmarks", *benchmarks,
            ]
        elif condition["kind"] == "direct":
            command = [
                sys.executable, str(ROOT / "scripts" / "build_direct_eval.py"),
                "--input-dir", str(input_dir), "--output", str(requests),
                "--benchmarks", *benchmarks,
            ]
        elif condition["kind"] == "parsel":
            command = [
                sys.executable, str(ROOT / "scripts" / "build_parsel_eval.py"),
                "--input-dir", str(input_dir), "--output", str(requests),
                "--benchmarks", *benchmarks,
                "--num-demonstrations", str(condition.get("parsel_demonstrations", 1)),
            ]
        elif condition["kind"] == "graphdsl":
            command = [
                sys.executable, str(ROOT / "scripts" / "build_qwen_eval.py"),
                "--input-dir", str(input_dir), "--output", str(requests),
                "--benchmarks", *benchmarks,
                "--num-demonstrations", str(condition.get("planner_demonstrations", 1)),
                "--planner-format", condition.get('planner_format', 'graph'),
                "--contract-version", condition.get('contract_version', '1'),
            ]
            if condition.get('preserve_public_examples', True) is False:
                command.append('--no-preserve-public-examples')
        else:
            raise ValueError(f"unknown condition kind: {condition['kind']}")
        if config.get("official_eval_only", True):
            command.append("--official-eval-only")
        run(command, dry_run)


def generate(config: dict[str, Any], dry_run: bool) -> None:
    output_dir = absolute(config["output_dir"])
    if not dry_run and any(c['kind'] == 'parsel' for c in config['conditions']):
        from run_safe_functional_eval import require_docker
        from run_official_evaluator import inspect_image
        from parsel_upstream import verify_upstream
        verify_upstream()
        require_docker()
        inspect_image('graphdsl-parsel:0.1')
    for condition in config["conditions"]:
        condition_dir = output_dir / condition["name"]
        base = ["--input", str(condition_dir / "requests.jsonl"), "--output", str(condition_dir / "results.jsonl")]
        if condition["kind"] == "pseudocode":
            command = [sys.executable, str(ROOT / "scripts" / "run_pseudocode_pipeline.py"), *base,
                '--representation', condition.get('representation', 'graphir'),
                '--source-context', condition.get('source_context', 'original'),
                '--example-context', condition.get(
                    'example_context',
                    'public' if condition.get('source_context', 'original') == 'original' else 'none'),
                '--plans-dir', str(output_dir / 'shared-pseudocode'),
                '--max-plan-tokens', str(config['generation'].get('max_plan_tokens', 4096)),
                '--max-plan-repairs', str(config['generation'].get('max_plan_repairs', 0)),
                '--max-code-tokens', str(config['generation'].get('max_code_tokens', 8192))]
        elif condition["kind"] == "direct":
            command = [sys.executable, str(ROOT / "scripts" / "run_direct_generation.py"), *base]
        elif condition["kind"] == "parsel":
            command = [sys.executable, str(ROOT / "scripts" / "run_parsel_pipeline.py"), *base]
            if condition.get("high_level_plan", False):
                command.append("--high-level-plan")
        elif condition["kind"] == "graphdsl":
            command = [
                sys.executable, str(ROOT / "scripts" / "run_qwen_pipeline.py"), *base,
                "--constraint-mode", condition.get("constraint_mode", "response_format"),
                "--validation-mode", condition.get("validation_mode", "full"),
                "--num-code-demonstrations", str(condition.get("code_demonstrations", 1)),
                "--synthesis-mode", condition.get('synthesis_mode', 'nodes'),
                "--planner-format", condition.get('planner_format', 'graph'),
                "--contract-version", condition.get('contract_version', '1'),
            ]
            if condition.get("high_level_plan", False):
                command.append("--high-level-plan")
        else:
            raise ValueError(f"unknown condition kind: {condition['kind']}")
        command += common_generation(config, condition)
        if condition.get('high_level_plan') and condition['kind'] != 'pseudocode':
            command += ['--plans-dir',str(output_dir / 'shared-plans'),
                        '--max-plan-tokens',str(config['generation'].get('max_plan_tokens',2048))]
        run(command, dry_run)


def export(config: dict[str, Any], dry_run: bool) -> None:
    input_dir = absolute(config["input_dir"])
    output_dir = absolute(config["output_dir"])
    suffixes = {
        "humaneval": "jobs.jsonl", "mbpp": "jobs.jsonl",
        "bigcodebench": "predictions.jsonl", "livecodebench": "predictions.json",
        "swebench": "predictions.jsonl",
    }
    for condition in config["conditions"]:
        for benchmark in condition.get("benchmarks", config["benchmarks"]):
            destination = output_dir / condition["name"] / config.get('evaluation', {}).get('subdir', 'evaluation') / benchmark / suffixes[benchmark]
            command = [
                sys.executable, str(ROOT / "scripts" / "export_predictions.py"),
                "--results", str(output_dir / condition["name"] / "results.jsonl"),
                "--input-dir", str(input_dir), "--benchmark", benchmark,
                "--output", str(destination), "--model-name", condition["name"],
            ]
            if config["generation"].get("limit") is not None:
                command.append("--allow-partial")
            if config.get("official_eval_only", True):
                command.append("--official-eval-only")
            run(command, dry_run)


def evaluate(config: dict[str, Any], dry_run: bool) -> None:
    output_dir = absolute(config["output_dir"])
    evaluation = config.get("evaluation", {})
    workers = str(evaluation.get("workers", 4))
    for condition in config["conditions"]:
        base = output_dir / condition["name"] / evaluation.get('subdir', 'evaluation')
        for benchmark in condition.get("benchmarks", config["benchmarks"]):
            bench_dir = base / benchmark
            if benchmark in {"humaneval", "mbpp"}:
                command = [
                    sys.executable, str(ROOT / "scripts" / "run_safe_functional_eval.py"),
                    "--jobs", str(bench_dir / "jobs.jsonl"),
                    "--output", str(bench_dir / "results.jsonl"),
                    "--workers", workers,
                    "--timeout", str(evaluation.get("functional_timeout", 5)),
                    "--memory-mb", str(evaluation.get("functional_memory_mb", 512)),
                ]
                if evaluation.get("build_functional_image"):
                    command.append("--build-image")
                run(command, dry_run)
            elif evaluation.get("run_official", False):
                prediction_name = "predictions.json" if benchmark == "livecodebench" else "predictions.jsonl"
                command = [
                    sys.executable, str(ROOT / "scripts" / "run_official_evaluator.py"), benchmark,
                    "--predictions", str(bench_dir / prediction_name),
                    "--output-dir", str(bench_dir), "--workers", workers,
                    "--timeout", str(evaluation.get("official_timeout", 1800)),
                    "--run-id", f"{config['name']}-{condition['name']}",
                    "--bigcodebench-image", evaluation.get("bigcodebench_image", "bigcodebench/bigcodebench-evaluate:latest"),
                    "--livecodebench-image", evaluation.get("livecodebench_image", "graphdsl-livecodebench:release-v6"),
                    "--swebench-dataset", evaluation.get("swebench_dataset", "princeton-nlp/SWE-bench"),
                ]
                run(command, dry_run)
            else:
                raise ValueError(f'{benchmark} requested but run_official=false; enable it or select only functional benchmarks')


def summarize(config: dict[str, Any], dry_run: bool) -> None:
    output_dir = absolute(config["output_dir"])
    subdir = config.get('evaluation', {}).get('subdir', 'evaluation')
    summary_name = 'summary.csv' if subdir == 'evaluation' else f'summary-{subdir}.csv'
    run([
        sys.executable, str(ROOT / "scripts" / "summarize_experiment.py"),
        "--experiment-dir", str(output_dir),
        "--output", str(output_dir / summary_name),
        "--evaluation-subdir", subdir,
    ], dry_run)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--stage",
        choices=["prepare", "generate", "export", "evaluate", "summarize", "all", "reevaluate"],
        required=True,
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument('--evaluation-subdir', help='Separate evaluation folder; never changes generation results')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.evaluation_subdir:
        if Path(args.evaluation_subdir).name != args.evaluation_subdir or args.evaluation_subdir in {'.', '..'}:
            parser.error('--evaluation-subdir must be one directory name')
        config.setdefault('evaluation', {})['subdir'] = args.evaluation_subdir
    stages = ["prepare", "generate", "export", "evaluate", "summarize"] if args.stage == "all" else [args.stage]
    if args.stage == 'reevaluate':
        if not args.evaluation_subdir or args.evaluation_subdir == 'evaluation':
            parser.error('reevaluate requires a fresh --evaluation-subdir (e.g. evaluation-v11)')
        for condition in config['conditions']:
            destination = absolute(config['output_dir']) / condition['name'] / args.evaluation_subdir
            if destination.exists():
                parser.error(f'reevaluation destination already exists: {destination}; use another name or resume individual stages')
        config.setdefault('evaluation', {})['build_functional_image'] = True
        stages = ['export', 'evaluate', 'summarize']
    actions = {
        "prepare": prepare, "generate": generate, "export": export,
        "evaluate": evaluate, "summarize": summarize,
    }
    for stage in stages:
        actions[stage](config, args.dry_run)


if __name__ == "__main__":
    main()
