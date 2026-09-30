#!/usr/bin/env python3
"""Container-internal worker for one HumanEval or MBPP candidate."""

from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import time
import tempfile
import uuid
from pathlib import Path


def limits(cpu_seconds: int, memory_bytes: int) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1_048_576, 1_048_576))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    if hasattr(resource, "RLIMIT_NPROC"):
        resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))


def execution_source(job):
    # Separate compilation preserves future imports at each module's beginning.
    # All phases share globals so tests and candidates can use public helpers.
    return '\n'.join(
        f'exec(compile({job[key]!r}, {"<" + key + ">"!r}, "exec", dont_inherit=True), globals())'
        for key in ('setup', 'candidate', 'test_source', 'invocation') if job.get(key)
    )


def main() -> None:
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    timeout = int(job.get("timeout_seconds", 5))
    memory = int(job.get("memory_bytes", 384 * 1024 * 1024))
    source = execution_source(job)
    marker = 'BENCHMARK_COMPLETED_' + uuid.uuid4().hex
    source += '\nprint(' + repr(marker) + ')\n'
    started = time.monotonic()
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": "/tmp",
        "TMPDIR": "/tmp",
    }
    try:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            completed = subprocess.run(
            [sys.executable, "-I", "-c", source],
            cwd="/tmp",
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            text=True,
            timeout=timeout,
            preexec_fn=lambda: limits(max(1, timeout), memory),
            check=False,
            )
            stdout.seek(0,2)
            stdout.seek(max(0,stdout.tell()-4000))
            captured_out = stdout.read().decode(errors='replace')
            stderr.seek(0,2)
            stderr.seek(max(0,stderr.tell()-4000))
            captured_err = stderr.read().decode(errors='replace')
        passed = completed.returncode == 0 and marker in captured_out.splitlines()
        status = "passed" if passed else "failed"
        result = {
            "task_id": job["task_id"],
            "status": status,
            "passed": passed,
            "returncode": completed.returncode,
            "duration_seconds": time.monotonic() - started,
            "stdout": captured_out.replace(marker,'')[-4000:],
            "stderr": captured_err,
        }
    except subprocess.TimeoutExpired as error:
        result = {
            "task_id": job["task_id"],
            "status": "timeout",
            "passed": False,
            "duration_seconds": time.monotonic() - started,
            "stdout": (error.stdout or "")[-4000:] if isinstance(error.stdout, str) else "",
            "stderr": (error.stderr or "")[-4000:] if isinstance(error.stderr, str) else "",
        }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
