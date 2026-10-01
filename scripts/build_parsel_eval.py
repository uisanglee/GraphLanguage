#!/usr/bin/env python3
"""Build Qwen requests for direct or plan-conditioned NL -> Parsel generation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from benchmark_requests import is_official_evaluation_task, iter_records, safe_task, variants


SUPPORTED = {"humaneval", "mbpp", "bigcodebench", "livecodebench"}


def load_demonstrations(path: Path) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"Parsel demonstration catalog must be an array: {path}")
    for row in rows:
        if not isinstance(row, dict) or not all(
            key in row for key in ("id", "interface", "task", "parsel")
        ):
            raise ValueError(f"invalid Parsel demonstration in {path}")
    return rows


def demonstration_messages(
    catalog: list[dict], interface: str, benchmark: str, limit: int
) -> tuple[list[dict[str, str]], list[str]]:
    candidates = [
        row for row in catalog
        if row["interface"] == interface
        and (not row.get("benchmarks") or benchmark in row["benchmarks"])
    ]
    candidates.sort(key=lambda row: row["id"])
    selected = candidates[:limit]
    messages: list[dict[str, str]] = []
    for row in selected:
        messages.extend([
            {"role": "user", "content": json.dumps(row["task"], ensure_ascii=False)},
            {"role": "assistant", "content": row["parsel"]},
        ])
    return messages, [row["id"] for row in selected]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path("data/normalized"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--system-prompt", type=Path, default=Path("prompts/nl_to_parsel.md"))
    parser.add_argument(
        "--demo-catalog", type=Path, default=Path("demonstrations/parsel_catalog.json")
    )
    parser.add_argument("--num-demonstrations", type=int, default=1)
    parser.add_argument("--benchmarks", nargs="+")
    parser.add_argument("--expand-variants", action="store_true")
    parser.add_argument("--official-eval-only", action="store_true")
    args = parser.parse_args()

    selected = set(args.benchmarks or sorted(SUPPORTED))
    unsupported = selected - SUPPORTED
    if unsupported:
        parser.error(
            "Parsel-Python 1x1 does not support repository patches: "
            + ", ".join(sorted(unsupported))
        )
    system = args.system_prompt.read_text(encoding="utf-8").strip()
    if args.num_demonstrations < 0:
        parser.error("--num-demonstrations must be non-negative")
    catalog = load_demonstrations(args.demo_catalog) if args.num_demonstrations else []
    prompt_hash = hashlib.sha256(system.encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    count = 0
    with temporary.open("w", encoding="utf-8") as output:
        for record in iter_records(args.input_dir, selected):
            if args.official_eval_only and not is_official_evaluation_task(record):
                continue
            for variant, prompt_text in variants(record, args.expand_variants):
                task = safe_task(record, prompt_text, variant)
                demos, demo_ids = demonstration_messages(
                    catalog, record["interface"], record["benchmark"], args.num_demonstrations
                )
                request = {
                    "custom_id": f"{record['id']}:{variant}",
                    "messages": [
                        {"role": "system", "content": system},
                        *demos,
                        {"role": "user", "content": json.dumps(task, ensure_ascii=False)},
                    ],
                    "metadata": {
                        "benchmark": record["benchmark"],
                        "task_id": record["id"],
                        "prompt_variant": variant,
                        "interface_mode": record["interface"],
                        "demonstration_ids": demo_ids,
                        "parsel_prompt_sha256": prompt_hash,
                    },
                }
                output.write(json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n")
                count += 1
    temporary.replace(args.output)
    print(f"wrote {count} Parsel requests to {args.output}")


if __name__ == "__main__":
    main()
