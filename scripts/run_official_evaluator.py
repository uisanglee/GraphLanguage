#!/usr/bin/env python3
"""Invoke official BigCodeBench, LiveCodeBench, or SWE-bench evaluators safely."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any


def require(command: str) -> None:
    if shutil.which(command) is None:
        raise RuntimeError(f"required command is not installed: {command}")


def docker_info() -> None:
    require("docker")
    result = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20,
    )
    if result.returncode:
        raise RuntimeError(f"Docker daemon is unavailable: {result.stderr.strip()}")


def inspect_image(image: str) -> str:
    result = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20,
    )
    if result.returncode:
        raise RuntimeError(f"Docker image is unavailable: {image}: {result.stderr.strip()}")
    return result.stdout.strip()


def container_prefix(name: str, memory: str, cpus: str) -> list[str]:
    return [
        "docker", "run", "--rm", "--name", name,
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "512",
        "--memory", memory,
        "--memory-swap", memory,
        "--cpus", cpus,
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=2g",
        "--env", "HOME=/tmp",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "benchmark", choices=["bigcodebench", "livecodebench", "swebench"]
    )
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--run-id", default="graphdsl-eval")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--bigcodebench-image", default="bigcodebench/bigcodebench-evaluate:latest"
    )
    parser.add_argument("--bigcodebench-split", choices=["instruct", "complete"], default="instruct")
    parser.add_argument("--livecodebench-image", default="graphdsl-livecodebench:release-v6")
    parser.add_argument("--swebench-dataset", default="princeton-nlp/SWE-bench")
    args = parser.parse_args()

    predictions = args.predictions.resolve()
    if not predictions.is_file():
        parser.error(f"prediction file does not exist: {predictions}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_dir = args.output_dir.resolve()
    provenance: dict[str, Any] = {
        "benchmark": args.benchmark,
        "predictions": str(predictions),
        "workers": args.workers,
        "timeout": args.timeout,
    }

    if args.benchmark in {"bigcodebench", "livecodebench"}:
        if predictions.parent != output_dir:
            parser.error("for container evaluation, place --predictions directly in --output-dir")
        filename = predictions.name
        image = (
            args.bigcodebench_image if args.benchmark == "bigcodebench"
            else args.livecodebench_image
        )
        if args.benchmark == "bigcodebench":
            command = container_prefix(f"graphdsl-bigcodebench-{uuid.uuid4().hex[:12]}", "16g", "8") + [
                "--volume", f"{output_dir}:/app:rw",
                "--workdir", "/app",
                image,
                "--execution", "local",
                "--split", args.bigcodebench_split,
                "--subset", "full",
                "--samples", f"/app/{filename}",
                "--parallel", str(args.workers),
                "--calibrated", "False",
            ]
        else:
            if len(json.loads(predictions.read_text())) != 1055:
                parser.error('official LiveCodeBench custom evaluator requires all 1055 release_v6 tasks; use HumanEval/MBPP for partial evaluation smoke tests')
            command = container_prefix(f"graphdsl-livecodebench-{uuid.uuid4().hex[:12]}", "16g", "8") + [
                "--volume", f"{output_dir}:/work:rw",
                "--workdir", "/work",
                image,
                "python", "-m", "lcb_runner.runner.custom_evaluator",
                "--custom_output_file", f"/work/{filename}",
                "--scenario", "codegeneration",
                "--release_version", "release_v6",
                "--num_process_evaluate", str(args.workers),
                "--timeout", str(min(args.timeout, 60)),
            ]
        if args.benchmark == 'bigcodebench':
            ids = [json.loads(line)['task_id'] for line in predictions.read_text().splitlines() if line.strip()]
            if len(ids) < 1140:
                command += ['--selective_evaluate', ','.join(ids)]
        if not args.dry_run:
            docker_info()
            provenance["image"] = image
            provenance["image_id"] = inspect_image(image)
    else:
        command = [
            sys.executable, "-m", "swebench.harness.run_evaluation",
            "--dataset_name", args.swebench_dataset,
            "--split", "test",
            "--predictions_path", str(predictions),
            "--max_workers", str(args.workers),
            "--run_id", args.run_id,
            "--timeout", str(args.timeout),
        ]
        provenance["dataset"] = args.swebench_dataset
        if not args.dry_run:
            docker_info()

    provenance["command"] = command
    (output_dir / "evaluator_provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.dry_run:
        print(json.dumps(command, ensure_ascii=False))
        return
    completed = subprocess.run(command, cwd=output_dir, check=False, stdin=subprocess.DEVNULL)
    if completed.returncode:
        raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
