#!/usr/bin/env python3
"""Build direct NL -> Python/Patch baseline requests from normalized benchmark rows."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from build_qwen_eval import is_official_evaluation_task, iter_records, safe_task, variants


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path("data/normalized"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--system-prompt", type=Path, default=Path("prompts/nl_to_python.md"))
    parser.add_argument("--benchmarks", nargs="+")
    parser.add_argument("--expand-variants", action="store_true")
    parser.add_argument("--official-eval-only", action="store_true")
    args = parser.parse_args()

    system = args.system_prompt.read_text(encoding="utf-8").strip()
    prompt_hash = hashlib.sha256(system.encode("utf-8")).hexdigest()
    selected = set(args.benchmarks) if args.benchmarks else None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    count = 0
    with temporary.open("w", encoding="utf-8") as output:
        for record in iter_records(args.input_dir, selected):
            if args.official_eval_only and not is_official_evaluation_task(record):
                continue
            for variant, prompt_text in variants(record, args.expand_variants):
                task = safe_task(record, prompt_text, variant)
                request = {
                    "custom_id": f"{record['id']}:{variant}",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(task, ensure_ascii=False)},
                    ],
                    "metadata": {
                        "benchmark": record["benchmark"],
                        "task_id": record["id"],
                        "prompt_variant": variant,
                        "interface_mode": record["interface"],
                        "direct_prompt_sha256": prompt_hash,
                    },
                }
                output.write(json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n")
                count += 1
    temporary.replace(args.output)
    print(f"wrote {count} direct requests to {args.output}")


if __name__ == "__main__":
    main()
