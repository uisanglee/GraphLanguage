#!/usr/bin/env python3
"""Evaluate HumanEval/MBPP jobs one-per-container with restrictive Docker settings."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import hashlib
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Iterator


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def require_docker() -> None:
    if shutil.which("docker") is None:
        raise RuntimeError("Docker is required; generated code will not be executed on the host")
    check = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20,
    )
    if check.returncode:
        raise RuntimeError(f"Docker daemon is unavailable: {check.stderr.strip()}")


def build_image(image: str, root: Path) -> None:
    subprocess.run(
        [
            "docker", "build", "--pull", "--tag", image,
            "--file", str(root / "sandbox" / "Dockerfile.functional"),
            str(root / "sandbox"),
        ],
        check=True,
    )


def evaluate_one(
    job: dict[str, Any], image: str, timeout: int, memory_mb: int
) -> dict[str, Any]:
    if not job.get("generation_valid", False):
        return {
            "task_id": job["task_id"],
            "status": "invalid_generation",
            "passed": False,
            "generation_errors": job.get("generation_errors", []),
        }
    job = dict(job)
    job["timeout_seconds"] = timeout
    job["memory_bytes"] = max(128, memory_mb - 64) * 1024 * 1024
    name = f"graphdsl-eval-{uuid.uuid4().hex[:16]}"
    with tempfile.TemporaryDirectory(prefix="graphdsl-job-") as directory:
        job_path = Path(directory) / "input.json"
        job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        os.chmod(job_path, 0o444)
        command = [
            "docker", "run", "--rm", "--name", name,
            "--network", "none",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", "64",
            "--memory", f"{memory_mb}m",
            "--memory-swap", f"{memory_mb}m",
            "--cpus", "1.0",
            "--ulimit", "nofile=64:64",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--volume", f"{job_path.resolve()}:/job/input.json:ro",
            image,
            "/job/input.json",
        ]
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout + 15,
                check=False,
            )
        except subprocess.TimeoutExpired:
            subprocess.run(
                ["docker", "rm", "--force", name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            return {"task_id": job["task_id"], "status": "container_timeout", "passed": False}
    if completed.returncode != 0:
        return {
            "task_id": job["task_id"],
            "status": "container_error",
            "passed": False,
            "container_returncode": completed.returncode,
            "container_stderr": completed.stderr[-4000:],
        }
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        return {
            "task_id": job["task_id"],
            "status": "invalid_worker_output",
            "passed": False,
            "error": str(error),
            "container_stdout": completed.stdout[-4000:],
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", default="graphdsl-functional-eval:0.1")
    parser.add_argument("--build-image", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=5)
    parser.add_argument("--memory-mb", type=int, default=512)
    args = parser.parse_args()

    if args.workers < 1 or args.timeout < 1 or args.memory_mb < 128:
        parser.error("workers/timeout must be positive and memory-mb must be at least 128")
    require_docker()
    root = Path(__file__).resolve().parents[1]
    if args.build_image:
        build_image(args.image, root)

    jobs = list(iter_jsonl(args.jobs))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(args.jobs.read_bytes() + json.dumps({
        'image': args.image, 'timeout': args.timeout, 'memory': args.memory_mb
    },sort_keys=True).encode()).hexdigest()
    identity_path = args.output.with_suffix('.identity.json')
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise ValueError('evaluation inputs/settings changed; use a new output path')
    if not identity_path.exists() and args.output.exists() and args.output.stat().st_size:
        raise ValueError('old evaluations lack provenance; use a fresh output path')
    identity_path.write_text(json.dumps(identity))
    completed_ids = {
        row["task_id"] for row in iter_jsonl(args.output)
        if row.get('status') in {'passed','failed','timeout','invalid_generation'}
    } if args.output.exists() else set()
    pending = [job for job in jobs if job["task_id"] not in completed_ids]
    with args.output.open("a", encoding="utf-8") as output:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(evaluate_one, job, args.image, args.timeout, args.memory_mb): job
                for job in pending
            }
            for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
                job = futures[future]
                try:
                    result = future.result()
                except Exception as error:
                    result = {
                        "task_id": job["task_id"], "status": "runner_error",
                        "passed": False, "error": f"{type(error).__name__}: {error}",
                    }
                output.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
                output.flush()
                print(f"{index}/{len(pending)} {result['task_id']} {result['status']}")

    results = list({r['task_id']:r for r in iter_jsonl(args.output)}.values())
    passed = sum(bool(row.get("passed")) for row in results)
    summary = {
        "tasks": len(results),
        "passed": passed,
        "pass_at_1": passed / len(results) if results else 0.0,
        "sandbox": {
            "image": args.image,
            "network": "none",
            "read_only_root": True,
            "memory_mb": args.memory_mb,
            "cpus_per_task": 1.0,
            "timeout_seconds": args.timeout,
        },
    }
    summary_path = args.output.with_suffix(args.output.suffix + ".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
