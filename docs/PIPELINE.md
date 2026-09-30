# GraphIR Core node generation pipeline

The v8 default uses the compact generation profile described in COMPACT_GENERATION.md:
Input, Output, Compute, Branch. Loops stay inside Compute. Extended Core support below is
retained for existing graphs, not exposed by the default generation schema.

The default synthesis mode is now nodes. See PARSEL_GRAPHDSL_1X1.md for the comparison protocol.

1. A compact planner prompt plus an optional retrieved task/graph example produces one Core graph.
2. JSON-Schema-constrained decoding restricts its shape. Schema and semantic checks then validate
   ports, acyclicity, type compatibility and required connections.
3. Each executable node is generated once from its local contract and neighboring
   contracts using prompts/graphdsl_node_to_python.md.
4. The compiler validates a single exact-signature function, keeps node imports local, and wires
   all edges mechanically. It never asks an LLM to assemble or rewrite the final program.
5. Syntax checks precede the separate Docker benchmark evaluator.

Boundary nodes Input/Output/Literal are deterministic. For other nodes:
def <stable_node_symbol>(inputs, regions) returns a dict with exactly the declared output port IDs.
The original task, graph-wide problem description, and other generated source are excluded from
node synthesis requests. This enforces the information boundary, not semantic correctness.

Core 0.2 infers regions and carried bindings from nested body graphs and matching port names.
Ordinary Python control flow can stay inside Compute; explicit Loop/Branch calls its owned body
callbacks, whose executable nodes are independently synthesized. Legacy 0.1
region behavior remains supported only for reproduction and is documented in GRAPHDSL_SPEC.md.
Descriptions must support reimplementation; that property is an evaluation hypothesis and is not
proven by schema validation.

Retrieval: planner demonstrations use the existing catalog. Node synthesis uses the hand-authored
demonstrations/node_catalog.json, filtered by kind and ranked lexically. A missing matching example
means zero examples. IDs are logged. The v7 matrix uses two examples for GraphIR planning,
and one each for node synthesis and NL-to-Parsel. The v8 matrix retains these counts but removes
the reserved nested syntax reference and uses a compact-compatible catalog.
Collection pipelines receive structural ranking priority; zero demonstrations is an explicitly
labeled ablation. The old --synthesis-mode whole is available only as an explicitly labeled
ablation or for repository patch experiments.

Generation uses per-task, per-payload response journals. Interrupted later stages reuse earlier
responses. Pending uncertain requests never silently trigger another sample. A run identity prevents
mixing changed prompts, settings, schemas or request files into an old result set.

```bash
python3 scripts/build_qwen_eval.py --output data/qwen/nl_to_graphdsl.jsonl --num-demonstrations 1
python3 scripts/run_qwen_pipeline.py --input data/qwen/nl_to_graphdsl.jsonl \
  --output outputs/node-smoke/results.jsonl --model Qwen/Qwen2.5-Coder-7B-Instruct \
  --synthesis-mode nodes --limit 3
python3 -m unittest discover -s tests -v
```

For the common comparison prefer experiments/parsel_graphdsl_smoke.json, whose input set excludes
repository patches. --limit is per benchmark, not additional tasks on every resumed run.
