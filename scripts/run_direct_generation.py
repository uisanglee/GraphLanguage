#!/usr/bin/env python3
"""Run the direct NL -> Python/Patch baseline against an OpenAI-compatible endpoint."""

from __future__ import annotations

import argparse
import json
import os
import hashlib
from pathlib import Path

from run_qwen_pipeline import Client, completed_ids, iter_jsonl, selected_requests
from validate_artifact import validate_artifact
from inference_journal import JournalClient, run_identity


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://localhost:8000/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", ""))
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--condition-name", default="direct")
    args = parser.parse_args()

    client = Client(args.base_url, args.model, args.api_key, args.timeout)
    run_identity(args.output, {k:v for k,v in vars(args).items() if k not in {'api_key','limit','no_resume'}}, args.input)
    if args.no_resume and args.output.exists() and args.output.stat().st_size:
        parser.error('--no-resume requires a fresh output path')
    done = set() if args.no_resume else completed_ids(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    processed = 0
    with args.output.open("a", encoding="utf-8") as output:
        for request in selected_requests(args.input, args.limit):
            custom_id = request["custom_id"]
            if custom_id in done:
                continue
            metadata = request.get("metadata", {})
            mode = metadata.get("interface_mode")
            record = {
                "custom_id": custom_id,
                "metadata": metadata,
                "model": args.model,
                "condition": args.condition_name,
            }
            try:
                journal = JournalClient(client, args.output.parent / 'inference' / hashlib.sha256(custom_id.encode()).hexdigest())
                artifact, inference = journal.complete(
                    request["messages"], args.max_tokens, args.temperature
                )
                artifact_errors = validate_artifact(artifact, mode)
                record.update({
                    "generated_artifact": artifact,
                    "artifact_errors": artifact_errors,
                    "artifact_valid": not artifact_errors,
                    "inference": inference,
                    "llm_calls": 1,
                })
            except Exception as error:
                record["generation_error"] = f"{type(error).__name__}: {error}"
            output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            output.flush()
            processed += 1
            print(f"{processed}: {custom_id} artifact_valid={record.get('artifact_valid', False)}")


if __name__ == "__main__":
    main()
