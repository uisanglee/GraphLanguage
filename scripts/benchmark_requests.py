"""Shared public benchmark request selection, without generation profiles."""
from __future__ import annotations
import ast
import json
from pathlib import Path
from typing import Any, Iterator


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
