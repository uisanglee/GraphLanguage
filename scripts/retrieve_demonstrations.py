#!/usr/bin/env python3
"""Retrieve compact GraphDSL demonstrations with deterministic lexical scoring."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+|[가-힣]+")


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]


def load_catalog(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"demonstration catalog must be an array: {path}")
    resolved: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        graph_path = (path.parent / item.pop("graph")).resolve()
        item["graph"] = json.loads(graph_path.read_text(encoding="utf-8"))
        artifact = item.pop("artifact", None)
        item["artifact"] = (
            (path.parent / artifact).read_text(encoding="utf-8").strip()
            if artifact else None
        )
        resolved.append(item)
    return resolved


def retrieve(
    catalog: list[dict[str, Any]],
    query: str,
    interface: str,
    benchmark: str | None,
    limit: int,
    require_artifact: bool = False,
) -> list[dict[str, Any]]:
    """Rank examples using profile filters plus a stable BM25-style lexical score."""

    if limit <= 0:
        return []
    candidates = [
        item for item in catalog
        if item.get("interface") == interface
        and (not require_artifact or item.get("artifact"))
    ]
    if not candidates:
        return []

    documents = [
        tokenize(" ".join([item.get("task", ""), *item.get("keywords", [])]))
        for item in candidates
    ]
    query_counts = Counter(tokenize(query))
    document_frequency = Counter(token for doc in documents for token in set(doc))
    average_length = sum(map(len, documents)) / max(len(documents), 1)
    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for item, tokens in zip(candidates, documents):
        counts = Counter(tokens)
        lexical = 0.0
        for token, query_weight in query_counts.items():
            frequency = counts[token]
            if not frequency:
                continue
            inverse_document_frequency = math.log(
                1 + (len(documents) - document_frequency[token] + 0.5)
                / (document_frequency[token] + 0.5)
            )
            normalization = frequency + 1.2 * (
                0.25 + 0.75 * len(tokens) / max(average_length, 1.0)
            )
            lexical += query_weight * inverse_document_frequency * frequency * 2.2 / normalization
        profile_bonus = 10.0
        benchmark_bonus = 2.0 if benchmark in item.get("benchmarks", []) else 0.0
        ranked.append((profile_bonus + benchmark_bonus + lexical, item["id"], item))
    ranked.sort(key=lambda value: (-value[0], value[1]))
    return [item for _, _, item in ranked[:limit]]


def planner_demo_messages(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for item in items:
        task = {
            "task_id": f"demo:{item['id']}",
            "benchmark": item.get("benchmarks", ["example"])[0],
            "interface_mode": item["interface"],
            "task": item["task"],
        }
        messages.extend([
            {"role": "user", "content": json.dumps(task, ensure_ascii=False, separators=(",", ":"))},
            {"role": "assistant", "content": json.dumps(item["graph"], ensure_ascii=False, separators=(",", ":"))},
        ])
    return messages


def synthesizer_demo_messages(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for item in items:
        if not item.get("artifact"):
            continue
        messages.extend([
            {"role": "user", "content": json.dumps(item["graph"], ensure_ascii=False, separators=(",", ":"))},
            {"role": "assistant", "content": item["artifact"]},
        ])
    return messages


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--interface", required=True, choices=["function", "stdio", "repository_patch"])
    parser.add_argument("--benchmark")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--catalog", type=Path, default=Path("demonstrations/catalog.json"))
    parser.add_argument("--stage", choices=["planner", "synthesizer"], default="planner")
    args = parser.parse_args()
    catalog = load_catalog(args.catalog)
    selected = retrieve(
        catalog, args.query, args.interface, args.benchmark, args.limit,
        require_artifact=args.stage == "synthesizer",
    )
    print(json.dumps([item["id"] for item in selected], ensure_ascii=False))


if __name__ == "__main__":
    main()
