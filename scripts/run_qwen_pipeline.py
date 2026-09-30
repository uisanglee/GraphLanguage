#!/usr/bin/env python3
"""Run NL -> GraphDSL -> Python/Patch against an OpenAI-compatible Qwen endpoint.

This script validates graph structure but deliberately does not execute untrusted generated code.
Use each benchmark's official evaluator for correctness and sandboxing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

from retrieve_demonstrations import (
    load_catalog,
    retrieve,
    synthesizer_demo_messages,
)
from schema_validation import load_schema, validate_schema
from validate_artifact import strip_fence, validate_artifact
from validate_graph import validate_semantics
from graphdsl_nodes import BOUNDARIES, node_request, check_node_source, compile_graph, node_demonstrations
from inference_journal import JournalClient, run_identity
from graphir_core import canonicalize_graph


def make_chat_payload(
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    schema: dict[str, Any] | None = None,
    constraint_mode: str = "none",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if schema is not None and constraint_mode == "response_format":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "graphir_core_0_2",
                "strict": True,
                "schema": schema,
            },
        }
    elif schema is not None and constraint_mode == "structured_outputs":
        payload["structured_outputs"] = {"json": schema}
    return payload


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def selected_requests(path: Path, limit: int | None):
    """A fixed prefix per benchmark, chosen before resume filtering."""
    counts = {}
    for row in iter_jsonl(path):
        benchmark = row.get('metadata',{}).get('benchmark','unknown')
        counts[benchmark] = counts.get(benchmark,0) + 1
        if limit is None or counts[benchmark] <= limit:
            yield row


def parse_graph(
    content: str, schema: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    text = strip_fence(content)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        return None, [f"JSON parse error: {error}"], []
    if not isinstance(value, dict):
        return None, ["GraphDSL response must be a JSON object"], []
    schema_errors = [f"schema: {error}" for error in validate_schema(value, schema)]
    semantic_errors = [f"semantic: {error}" for error in validate_semantics(value)]
    return value, schema_errors, semantic_errors


class Client:
    def __init__(self, base_url: str, model: str, api_key: str, timeout: int) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        schema: dict[str, Any] | None = None,
        constraint_mode: str = "none",
        attempts: int = 1,
    ) -> tuple[str, dict[str, Any]]:
        payload = make_chat_payload(
            self.model, messages, max_tokens, temperature, schema, constraint_mode
        )
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        for attempt in range(attempts):
            try:
                started = time.monotonic()
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                elapsed = time.monotonic() - started
                content = payload["choices"][0]["message"].get("content") or ""
                return content, {"elapsed_seconds": elapsed, "usage": payload.get("usage", {}),
                                 "finish_reason": payload['choices'][0].get('finish_reason')}
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")[:4000]
                raise RuntimeError(f"HTTP {error.code}: {detail}") from error
            except (urllib.error.URLError, TimeoutError):
                if attempt + 1 == attempts:
                    raise
                time.sleep(min(20, 2 ** attempt))
        raise AssertionError("unreachable")


def completed_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        row["custom_id"] for row in iter_jsonl(path)
        if row.get("custom_id")
        and not row.get("pipeline_error")
        and not row.get("generation_error")
    }


def with_high_level_plan(
    messages: list[dict[str, str]], plan: str
) -> list[dict[str, str]]:
    """Add a plan to the task object without exposing it to demonstrations."""

    updated = [dict(message) for message in messages]
    if not updated or updated[-1].get("role") != "user":
        raise ValueError("planner request must end with a user task object")
    task = json.loads(updated[-1]["content"])
    if not isinstance(task, dict):
        raise ValueError("planner user content must be a JSON object")
    task["HIGH_LEVEL_PLAN"] = plan
    updated[-1] = {
        "role": "user",
        "content": json.dumps(task, ensure_ascii=False, separators=(",", ":")),
    }
    return updated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://localhost:8000/v1")
    parser.add_argument("--model", help="Shared model fallback for both stages.")
    parser.add_argument("--planner-base-url")
    parser.add_argument("--synthesizer-base-url")
    parser.add_argument("--planner-model")
    parser.add_argument("--synthesizer-model")
    parser.add_argument('--synthesis-mode', choices=['nodes', 'whole'], default='nodes')
    parser.add_argument('--node-system-prompt', type=Path, default=Path('prompts/graphdsl_node_to_python.md'))
    parser.add_argument('--max-plan-tokens', type=int, default=2048)
    parser.add_argument('--max-node-tokens', type=int, default=4096)
    parser.add_argument('--plans-dir', type=Path)
    parser.add_argument(
        "--high-level-plan", action="store_true",
        help="Generate exactly one implementation-independent plan before GraphDSL.",
    )
    parser.add_argument(
        "--plan-system-prompt", type=Path, default=Path("prompts/high_level_plan.md")
    )
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", ""))
    parser.add_argument("--code-system-prompt", type=Path, default=Path("prompts/graphdsl_to_python.md"))
    parser.add_argument("--schema", type=Path, default=Path("schemas/graphir-compact.schema.json"))
    parser.add_argument(
        "--constraint-mode",
        choices=["response_format", "structured_outputs", "none"],
        default="response_format",
        help="JSON grammar API used for NL -> GraphDSL. Modern vLLM supports response_format.",
    )
    parser.add_argument(
        "--validation-mode", choices=["full", "schema", "parse"], default="full",
        help="Gate before synthesis; all validation errors are recorded in every mode.",
    )
    parser.add_argument("--condition-name", default="graphdsl-full")
    parser.add_argument(
        "--demo-catalog", type=Path, default=Path("demonstrations/catalog.json")
    )
    parser.add_argument(
        "--num-code-demonstrations", type=int, default=1,
        help="Retrieved GraphDSL -> artifact examples; use 0 to disable.",
    )
    parser.add_argument("--max-graph-tokens", type=int, default=8192)
    parser.add_argument("--max-code-tokens", type=int, default=8192)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    planner_model = args.planner_model or args.model
    synthesizer_model = args.synthesizer_model or args.model
    if not planner_model or not synthesizer_model:
        parser.error("set --model, or set both --planner-model and --synthesizer-model")
    planner_client = Client(
        args.planner_base_url or args.base_url, planner_model, args.api_key, args.timeout
    )
    synthesizer_client = Client(
        args.synthesizer_base_url or args.base_url,
        synthesizer_model,
        args.api_key,
        args.timeout,
    )
    code_system = args.code_system_prompt.read_text(encoding="utf-8").strip()
    plan_system = args.plan_system_prompt.read_text(encoding="utf-8").strip()
    node_system = args.node_system_prompt.read_text().strip()
    schema = load_schema(args.schema)
    code_prompt_sha256 = hashlib.sha256(code_system.encode("utf-8")).hexdigest()
    schema_sha256 = hashlib.sha256(
        json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    settings = {k: v for k, v in vars(args).items() if k not in {'api_key','limit','no_resume'}}
    settings.update(node_prompt=node_system, plan_prompt=plan_system, code_prompt=code_system, schema=schema_sha256)
    run_identity(args.output, settings, args.input)
    if args.no_resume and args.output.exists() and args.output.stat().st_size:
        parser.error('--no-resume requires a fresh output path')
    catalog = load_catalog(args.demo_catalog) if args.num_code_demonstrations else []
    done = set() if args.no_resume else completed_ids(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    processed = 0
    with args.output.open("a", encoding="utf-8") as output:
        for request in selected_requests(args.input, args.limit):
            custom_id = request["custom_id"]
            if custom_id in done:
                continue
            record: dict[str, Any] = {
                "custom_id": custom_id,
                "metadata": request.get("metadata", {}),
                "planner_model": planner_model,
                "synthesizer_model": synthesizer_model,
                "constraint_mode": args.constraint_mode,
                "validation_mode": args.validation_mode,
                "condition": args.condition_name,
                "high_level_plan_enabled": args.high_level_plan,
                "schema_sha256": schema_sha256,
                "synthesizer_prompt_sha256": code_prompt_sha256,
            }
            try:
                task_key = hashlib.sha256(custom_id.encode()).hexdigest()
                task_journal = args.output.parent / 'inference' / task_key
                planner = JournalClient(planner_client, task_journal / 'planner')
                synthesizer = JournalClient(synthesizer_client, task_journal / 'synthesizer')
                record["llm_calls"] = 0
                graph_messages = request["messages"]
                if args.high_level_plan:
                    plan_client = JournalClient(planner_client, (args.plans_dir or args.output.parent / 'plans') / task_key)
                    plan_text, plan_timing = plan_client.complete(
                        [
                            {"role": "system", "content": plan_system},
                            request["messages"][-1],
                        ],
                        args.max_plan_tokens,
                        args.temperature,
                    )
                    graph_messages = with_high_level_plan(graph_messages, plan_text)
                    record["high_level_plan"] = plan_text
                    record["plan_inference"] = plan_timing
                    record["llm_calls"] += 1
                graph_text, graph_timing = planner.complete(
                    graph_messages, args.max_graph_tokens, args.temperature,
                    schema=schema, constraint_mode=args.constraint_mode,
                )
                record["llm_calls"] += 1
                graph, graph_schema_errors, graph_semantic_errors = parse_graph(graph_text, schema)
                graph_errors = graph_schema_errors + graph_semantic_errors
                if args.validation_mode == "full":
                    graph_accepted = graph is not None and not graph_errors
                elif args.validation_mode == "schema":
                    graph_accepted = graph is not None and not graph_schema_errors
                else:
                    graph_accepted = graph is not None
                record.update(
                    {
                        "graph_raw": graph_text,
                        "graph": graph,
                        "graph_errors": graph_errors,
                        "graph_schema_errors": graph_schema_errors,
                        "graph_semantic_errors": graph_semantic_errors,
                        "graph_valid": not graph_errors,
                        "graph_accepted": graph_accepted,
                        "graph_inference": graph_timing,
                    }
                )
                if graph_accepted and graph is not None:
                    executable_graph = canonicalize_graph(graph)
                    if executable_graph is not graph:
                        record["graph_canonical"] = executable_graph
                    interface = executable_graph["interface"]["mode"]
                    record['synthesis_mode'] = args.synthesis_mode
                    if args.synthesis_mode == 'nodes':
                        implementations = {}
                        record['node_results'] = {}
                        for node in executable_graph['nodes']:
                            if node['kind'] in BOUNDARIES:
                                continue
                            dialect = "core-0.2" if executable_graph.get("metadata", {}).get("graphir_core_version") else "legacy-0.1"
                            demo_messages, demo_ids = node_demonstrations(
                                node, args.num_code_demonstrations, dialect=dialect
                            )
                            raw_code, inference = synthesizer.complete([
                                {'role':'system', 'content':node_system},
                                *demo_messages,
                                {'role':'user', 'content':json.dumps(node_request(executable_graph, node), ensure_ascii=False)},
                            ], args.max_node_tokens, args.temperature)
                            record['llm_calls'] += 1
                            code = strip_fence(raw_code)
                            errors = check_node_source(code, node, executable_graph)
                            node_result = dict(
                                code=code, errors=errors, inference=inference,
                                demonstration_ids=demo_ids,
                            )
                            if code != raw_code.strip():
                                node_result['raw_code'] = raw_code
                            record['node_results'][node['id']] = node_result
                            if errors:
                                record['artifact_errors'] = [f"{node['id']}: {e}" for e in errors]
                                break
                            implementations[node['id']] = code
                        else:
                            try:
                                record['generated_artifact'] = compile_graph(executable_graph, implementations)
                                record['artifact_errors'] = validate_artifact(record['generated_artifact'], interface)
                            except (ValueError, SyntaxError, KeyError) as error:
                                record['artifact_errors'] = [str(error)]
                        record['artifact_valid'] = not record.get('artifact_errors') and 'generated_artifact' in record
                        # Record the single final attempt, including invalid node output, below.
                        output.write(json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n')
                        output.flush()
                        processed += 1
                        print(f'{processed}: {custom_id} node artifact_valid={record["artifact_valid"]}', flush=True)
                        continue
                    graph_query = " ".join(
                        [graph.get("title", ""), graph.get("description", "")]
                        + [
                            f"{node.get('label', '')} {node.get('description', '')}"
                            for node in graph.get("nodes", []) if isinstance(node, dict)
                        ]
                    )
                    code_demonstrations = retrieve(
                        catalog,
                        query=graph_query,
                        interface=interface,
                        benchmark=request.get("metadata", {}).get("benchmark"),
                        limit=args.num_code_demonstrations,
                        require_artifact=True,
                    )
                    code_text, code_timing = synthesizer.complete(
                        [
                            {"role": "system", "content": code_system},
                            *synthesizer_demo_messages(code_demonstrations),
                            {
                                "role": "user",
                                "content": json.dumps(graph, ensure_ascii=False, separators=(",", ":")),
                            },
                        ],
                        args.max_code_tokens,
                        args.temperature,
                    )
                    record["llm_calls"] += 1
                    record["generated_artifact"] = code_text
                    record["artifact_errors"] = validate_artifact(code_text, interface)
                    record["artifact_valid"] = not record["artifact_errors"]
                    record["code_demonstration_ids"] = [
                        item["id"] for item in code_demonstrations
                    ]
                    record["code_inference"] = code_timing
            except Exception as error:  # Keep a resumable record for endpoint/model failures.
                record["pipeline_error"] = f"{type(error).__name__}: {error}"
            output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            output.flush()
            processed += 1
            print(
                f"{processed}: {custom_id} graph_valid={record.get('graph_valid', False)}",
                flush=True,
            )


if __name__ == "__main__":
    main()
