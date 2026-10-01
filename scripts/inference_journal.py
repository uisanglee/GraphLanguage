"""Persistent per-stage generations. An uncertain request is never silently resampled."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path


class JournalClient:
    def __init__(self, client, directory: Path):
        self.client = client
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.events = []
        self.cache_hits = 0
        self.requests_sent = 0

    def complete(self, messages, max_tokens, temperature, schema=None, constraint_mode="none", **kwargs):
        payload = dict(model=self.client.model, messages=messages, max_tokens=max_tokens,
                       temperature=temperature, schema=schema, constraint_mode=constraint_mode)
        key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        path = self.directory / (key + '.json')
        if path.exists():
            cached = json.loads(path.read_text())
            if cached['status'] != 'completed':
                raise RuntimeError(f"unresolved inference {path}; no automatic resampling")
            self.events.append(cached['inference'])
            self.cache_hits += 1
            return cached['text'], cached['inference']
        # Write ahead. A crash after sending a request requires explicit reconciliation.
        with path.open('x') as stream:
            json.dump({'status': 'pending', 'payload_sha256': key}, stream)
        self.requests_sent += 1
        text, inference = self.client.complete(messages, max_tokens, temperature,
            schema=schema, constraint_mode=constraint_mode, attempts=1)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps({'status': 'completed', 'text': text, 'inference': inference}))
        temp.replace(path)
        self.events.append(inference)
        return text, inference


def run_identity(output: Path, settings: dict, inputs: Path) -> None:
    """Prevent resuming an old condition with different prompts/models/tasks."""
    fingerprint = dict(settings=settings, input_sha256=hashlib.sha256(inputs.read_bytes()).hexdigest())
    path = output.with_suffix('.identity.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.loads(json.dumps(fingerprint, default=str))
    if path.exists() and json.loads(path.read_text()) != encoded:
        raise ValueError(f"run identity changed; choose a new output directory: {path}")
    if not path.exists():
        if output.exists() and output.stat().st_size:
            raise ValueError('legacy results have no run identity; choose a new output directory')
        path.write_text(json.dumps(encoded, indent=2))
