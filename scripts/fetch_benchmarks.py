#!/usr/bin/env python3
"""Fetch and normalize the five benchmark prompt corpora.

The script intentionally keeps references/tests in separate fields. Qwen prompt builders must
only use ``prompt`` and public task metadata, never ``reference`` or hidden tests.
"""

from __future__ import annotations
from benchmark_requests import normalize_interface

import argparse
import gzip
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Iterator


HUMANEVAL_URL = (
    "https://raw.githubusercontent.com/openai/human-eval/master/data/HumanEval.jsonl.gz"
)
MBPP_URL = (
    "https://raw.githubusercontent.com/google-research/google-research/master/mbpp/mbpp.jsonl"
)
HF_ROWS_URL = "https://datasets-server.huggingface.co/rows"

LCB_UPSTREAM = "livecodebench/code_generation_lite"
LCB_MIRROR = "ali-elganzory/livecodebench-code_generation_lite"
LCB_RELEASE = "release_v6"
LCB_SHARDS = 9

EXPECTED_COUNTS = {
    "humaneval": 164,
    "mbpp": 974,
    "bigcodebench": 1140,
    "livecodebench": 1055,
    "swebench": 2294,
}


def http_bytes(url: str, attempts: int = 6) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "graphdsl-benchmark-lab/0.1"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return response.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
            retryable = not isinstance(error, urllib.error.HTTPError) or error.code in {
                408, 425, 429, 500, 502, 503, 504,
            }
            if not retryable or attempt + 1 == attempts:
                raise
            delay = min(30, 2 ** attempt)
            print(f"retrying {url} after {error} ({delay}s)", file=sys.stderr)
            time.sleep(delay)
    raise AssertionError("unreachable")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    temporary.replace(path)
    return count


def checked_write(name: str, output_dir: Path, rows: Iterable[dict[str, Any]]) -> None:
    count = write_jsonl(output_dir / f"{name}.jsonl", rows)
    expected = EXPECTED_COUNTS[name]
    if count != expected:
        raise RuntimeError(f"{name}: expected {expected} rows, received {count}")
    print(f"{name}: {count} records", file=sys.stderr)


def fetch_humaneval() -> Iterator[dict[str, Any]]:
    payload = gzip.decompress(http_bytes(HUMANEVAL_URL)).decode("utf-8")
    for line in payload.splitlines():
        raw = json.loads(line)
        yield {
            "id": f"humaneval:{raw['task_id']}",
            "benchmark": "humaneval",
            "split": "test",
            "interface": "function",
            "prompt": {"primary": raw["prompt"], "variants": {"completion": raw["prompt"]}},
            "starter_code": raw["prompt"],
            "entrypoint": raw["entry_point"],
            "public_tests": [],
            "reference": {
                "canonical_solution": raw["canonical_solution"],
                "test": raw["test"],
            },
            "metadata": {"task_id": raw["task_id"]},
            "provenance": {"url": HUMANEVAL_URL, "upstream": "openai/human-eval"},
        }


def fetch_mbpp() -> Iterator[dict[str, Any]]:
    payload = http_bytes(MBPP_URL).decode("utf-8")
    for line in payload.splitlines():
        raw = json.loads(line)
        task_id = str(raw["task_id"])
        numeric_task_id = int(raw["task_id"])
        if 11 <= numeric_task_id <= 510:
            split = "test"
        elif 1 <= numeric_task_id <= 10:
            split = "prompt"
        elif 511 <= numeric_task_id <= 600:
            split = "validation"
        else:
            split = "train"
        tests = raw.get("test_list", [])
        primary = raw["text"]
        if tests:
            primary += "\n\nYour code should satisfy these examples:\n" + "\n".join(tests)
        yield {
            "id": f"mbpp:{task_id}",
            "benchmark": "mbpp",
            "split": split,
            "interface": "function",
            "prompt": {"primary": primary, "variants": {"description": raw["text"]}},
            "starter_code": raw.get("test_setup_code", ""),
            "entrypoint": None,
            "public_tests": tests,
            "reference": {
                "canonical_solution": raw.get("code", ""),
                "challenge_tests": raw.get("challenge_test_list", []),
            },
            "metadata": {"task_id": raw["task_id"], "source_file": raw.get("source_file")},
            "provenance": {"url": MBPP_URL, "upstream": "google-research/mbpp"},
        }


def hf_rows(dataset: str, config: str, split: str, page_size: int = 100) -> Iterator[dict]:
    offset = 0
    total: int | None = None
    while total is None or offset < total:
        query = urllib.parse.urlencode(
            {
                "dataset": dataset,
                "config": config,
                "split": split,
                "offset": offset,
                "length": page_size,
            }
        )
        response = json.loads(http_bytes(f"{HF_ROWS_URL}?{query}"))
        if "error" in response:
            raise RuntimeError(f"Hugging Face rows API: {response['error']}")
        total = int(response["num_rows_total"])
        rows = response["rows"]
        if not rows and offset < total:
            raise RuntimeError(f"Hugging Face rows API returned an empty page at {offset}/{total}")
        for wrapped in rows:
            yield wrapped["row"]
        offset += len(rows)


def fetch_bigcodebench() -> Iterator[dict[str, Any]]:
    dataset = "bigcode/bigcodebench"
    revision = "v0.1.4"
    for raw in hf_rows(dataset, "default", revision):
        yield {
            "id": f"bigcodebench:{raw['task_id']}",
            "benchmark": "bigcodebench",
            "split": revision,
            "interface": "function",
            "prompt": {
                "primary": raw["instruct_prompt"],
                "variants": {
                    "instruct": raw["instruct_prompt"],
                    "complete": raw["complete_prompt"],
                },
            },
            "starter_code": raw.get("code_prompt", ""),
            "entrypoint": raw.get("entry_point"),
            "public_tests": [],
            "reference": {
                "canonical_solution": raw.get("canonical_solution", ""),
                "test": raw.get("test", ""),
            },
            "metadata": {
                "task_id": raw["task_id"],
                "doc_struct": raw.get("doc_struct", ""),
                "libs": raw.get("libs", ""),
            },
            "provenance": {
                "url": f"https://huggingface.co/datasets/{dataset}",
                "upstream": dataset,
                "revision": revision,
            },
        }


def lcb_urls() -> list[str]:
    base = f"https://huggingface.co/datasets/{LCB_MIRROR}/resolve/main/{LCB_RELEASE}"
    return [f"{base}/test-{index:05d}-of-{LCB_SHARDS:05d}.parquet" for index in range(LCB_SHARDS)]


def fetch_livecodebench() -> Iterator[dict[str, Any]]:
    try:
        import duckdb  # type: ignore
    except ImportError as error:
        raise RuntimeError(
            "LiveCodeBench column projection requires DuckDB: python -m pip install duckdb"
        ) from error

    connection = duckdb.connect()
    urls_sql = ",".join("?" for _ in lcb_urls())
    query = f"""
        SELECT question_title, question_content, platform, question_id, contest_id,
               contest_date, starter_code, difficulty, public_test_cases, metadata
        FROM read_parquet([{urls_sql}])
        ORDER BY contest_date, platform, question_id
    """
    cursor = connection.execute(query, lcb_urls())
    columns = [item[0] for item in cursor.description]
    while batch := cursor.fetchmany(100):
        for values in batch:
            raw = dict(zip(columns, values))
            task_key = f"{raw['platform']}:{raw['question_id']}"
            content = raw["question_content"]
            starter = raw.get("starter_code") or ""
            primary = content
            if starter:
                primary += "\n\nStarter code:\n```python\n" + starter + "\n```"
            public_tests = raw.get("public_test_cases")
            try:
                public_tests = json.loads(public_tests) if isinstance(public_tests, str) else public_tests
            except json.JSONDecodeError:
                pass
            metadata = raw.get("metadata")
            try:
                metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
            except json.JSONDecodeError:
                pass
            yield normalize_interface({
                "id": f"livecodebench:{task_key}",
                "benchmark": "livecodebench",
                "split": LCB_RELEASE,
                "interface": "stdio",
                "prompt": {
                    "primary": primary,
                    "variants": {"question_content": content},
                },
                "starter_code": starter,
                "entrypoint": None,
                "public_tests": public_tests or [],
                "reference": {},
                "metadata": {
                    "title": raw["question_title"],
                    "platform": raw["platform"],
                    "question_id": raw["question_id"],
                    "contest_id": raw["contest_id"],
                    "contest_date": raw["contest_date"],
                    "difficulty": raw["difficulty"],
                    "official_metadata": metadata,
                },
                "provenance": {
                    "upstream": LCB_UPSTREAM,
                    "upstream_url": f"https://huggingface.co/datasets/{LCB_UPSTREAM}",
                    "revision": LCB_RELEASE,
                    "projection_mirror": LCB_MIRROR,
                    "projection_note": "Parquet conversion used only to exclude multi-GB private tests",
                },
            })
    connection.close()


def fetch_swebench() -> Iterator[dict[str, Any]]:
    dataset = "princeton-nlp/SWE-bench"
    for raw in hf_rows(dataset, "default", "test", page_size=100):
        instance_id = raw["instance_id"]
        yield {
            "id": f"swebench:{instance_id}",
            "benchmark": "swebench",
            "split": "test",
            "interface": "repository_patch",
            "prompt": {
                "primary": raw["problem_statement"],
                "variants": {
                    "issue": raw["problem_statement"],
                    "issue_with_hints": raw["problem_statement"]
                    + (("\n\nHints:\n" + raw["hints_text"]) if raw.get("hints_text") else ""),
                },
            },
            "starter_code": "",
            "entrypoint": None,
            "public_tests": [],
            "reference": {
                "patch": raw.get("patch", ""),
                "test_patch": raw.get("test_patch", ""),
                "fail_to_pass": raw.get("FAIL_TO_PASS", "[]"),
                "pass_to_pass": raw.get("PASS_TO_PASS", "[]"),
            },
            "metadata": {
                "instance_id": instance_id,
                "repo": raw["repo"],
                "base_commit": raw["base_commit"],
                "environment_setup_commit": raw.get("environment_setup_commit"),
                "version": raw.get("version"),
                "created_at": raw.get("created_at"),
            },
            "provenance": {
                "url": f"https://huggingface.co/datasets/{dataset}",
                "upstream": dataset,
                "split": "test",
            },
        }


FETCHERS = {
    "humaneval": fetch_humaneval,
    "mbpp": fetch_mbpp,
    "bigcodebench": fetch_bigcodebench,
    "livecodebench": fetch_livecodebench,
    "swebench": fetch_swebench,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("data/normalized"))
    parser.add_argument(
        "--only",
        nargs="+",
        choices=sorted(FETCHERS),
        help="Fetch only selected benchmarks (default: all).",
    )
    parser.add_argument("--skip-livecodebench", action="store_true")
    arguments = parser.parse_args()

    selected = arguments.only or list(FETCHERS)
    if arguments.skip_livecodebench:
        selected = [name for name in selected if name != "livecodebench"]
    for name in selected:
        checked_write(name, arguments.output_dir, FETCHERS[name]())


if __name__ == "__main__":
    main()
