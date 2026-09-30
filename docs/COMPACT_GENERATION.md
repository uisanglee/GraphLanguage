# Compact generation profile (v8)

The wire format remains Core 0.2; this is a restricted generation profile, not a new runtime.
The default pipeline schema is `schemas/graphir-compact.schema.json`.

- Generate only Input, Output, Compute, Branch. Constants, API calls, imports, iteration,
  state updates and small conditionals belong inside Compute.
- Split by independently meaningful contracts, not individual algorithm steps. There is no
  hard node-count limit or automatic merging; granularity is still a planner decision.
- Branch exposes independently editable paths with matching outputs. The existing compiler
  derives boundaries and callback bindings; only the selected path executes.
- Edges remain pure connections. Contract requirements and deterministic checks are retained.
- Repository editing is outside this profile. Source mapping and patch tooling are not
  implemented by this change. The default request builder rejects repository_patch tasks.

The planner and node system prompts were shortened. The shared high-level-plan prompt and
all Parsel algorithms/prompts are unchanged. Compact retrieval uses the same lexical ranking
and collection bonus but no forced nested example. Its catalog has a Compute-only iteration
example and an optional Branch example with iteration inside Compute. Branch node synthesis
has a matching handwritten callback demonstration.

The smoke configuration now writes to `outputs/qwen7b-node-smoke-v8`; do not reuse v7 requests,
plans, journals or results. Run from the project root:

```sh
python scripts/run_experiments.py --config experiments/parsel_graphdsl_smoke.json --stage all
```

This changes prompts, generation schema, and demonstrations together; an improvement cannot
be attributed to node-kind reduction alone without controlled ablations. Public-example and
port diagnostics remain separate from the official pass@1 evaluation. No performance claim
is made until the Qwen/Docker experiment is rerun.

Compatibility: `graphir-core.schema.json`, `extended_catalog.json`, the extended_* system
prompts, existing examples, and the runtime still support prior extended graphs. For historical
generation use explicit --schema/--system-prompt/--node-system-prompt/--demo-catalog overrides
with the individual scripts (repository request preparation is no longer supported by the
default builder). Parsel and direct-Python baselines are unchanged.
