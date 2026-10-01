"""Shared inference transport and deterministic request selection."""
from __future__ import annotations
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator


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
