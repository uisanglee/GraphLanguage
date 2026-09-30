# Named contracts and deterministic graph construction (v9)

## Evaluation fixes and contract guards (v11)

HumanEval evaluation now preserves public starter helpers/imports but removes the target stub.
Setup, candidate and tests compile separately in the same sandbox namespace, so a candidate's
future imports remain legal. Public example diagnostics parse only the target docstring, not
the closing Python quotes. Reference solutions are never used as setup or generation inputs.

Reevaluate existing v10 candidates **without any LLM calls**, preserving the original files:

```sh
python scripts/run_experiments.py --config experiments/parsel_graphdsl_humaneval_v10.json --stage reevaluate --evaluation-subdir evaluation-v11
```

This exports fresh jobs, rebuilds the Docker worker, evaluates all four configured conditions,
and writes `summary-evaluation-v11.csv` in the original experiment directory. Per-condition
jobs/results live in `evaluation-v11/humaneval/`. An existing destination is refused; after an
interruption use the individual `export`, `evaluate`, `summarize` stages with the same subdir,
as appropriate. Reevaluation does not reclassify old generation gates using new validators.

For **new generation**, use `experiments/parsel_graphdsl_smoke_v12.json`,
`parsel_graphdsl_humaneval_v12.json`, or `parsel_graphdsl_full_v12.json`. These use fresh output
directories and build the updated functional image. HumanEval-only still excludes direct Python.
The superseded v11 generation configs are retained only for provenance: their constrained schema
used unsupported `propertyNames`, so vLLM rejected requests before inference (`llm_calls=0`).

The constrained schema now distinguishes Compute and Branch (Branch cannot emit needs and
must declare exactly one result). Names, scope and type compatibility remain deterministic
semantic checks: unknown references and overwritten values are rejected, never guessed.
Value-key identifier rules are intentionally enforced after decoding because vLLM's response
format grammar does not implement JSON Schema `propertyNames`.
Prompts retain input formats, exact output strings and boundary semantics in local descriptions,
and explain tuple-valued returns. Synthesis receives a return-key template and explicit input
access expressions. Static symbol-table checks reject missing globals, including inside nested
helpers/comprehensions, while allowing locally imported APIs and closures.

No automatic repair, extra candidate sampling, or hidden-test feedback is added. Parsel's
algorithm is untouched; the shared evaluation fixes apply equally to all conditions. New
generation may still contain incorrect contracts/algorithms. Accuracy and vLLM constrained-schema
compatibility require a GPU smoke run; local unit tests are not evidence of improved pass@1.

## Fixed public interfaces (v10)

Use `parsel_graphdsl_smoke_v10.json`, `parsel_graphdsl_full_v10.json`, or
`parsel_graphdsl_humaneval_v10.json` after this update. Fresh output directories keep earlier
experiments separate. The HumanEval-only configuration contains GraphIR Direct/Plan and
Parsel Direct/Plan, without the direct-Python baseline.

When a named function/method signature can be extracted from public starter code, request
preparation supplies `fixed_interface` and `available_inputs`. Per-task constrained decoding
removes `interface` from the model's output schema, and retrieved examples use the same shape.
The compiler injects the original parameter spelling, defaults, positional/keyword conventions,
annotations and return annotation. It parses syntax only and does not execute starter code.
Formatting may be normalized by Python AST rendering. Needs still must reference exact names;
unknown names are rejected rather than guessed. Without a usable starter signature the existing
model-authored interface path remains available (notably MBPP). The shared high-level planning
task excludes these compiler metadata fields to preserve the GraphIR/Parsel comparison.

Tests cover the HumanEval `delimeter` spelling, method signatures and incomplete function
prefixes, default parameters, request examples and end-to-end mock generation. Live Qwen
accuracy must be measured again.

```sh
python scripts/run_experiments.py --config experiments/parsel_graphdsl_humaneval_v10.json --stage all
```

The rest of this document describes the underlying contract format and v9 introduction.

Use `experiments/parsel_graphdsl_smoke_v9.json` and `parsel_graphdsl_full_v9.json` for the
new `planner_format: contracts` condition. Existing v8 configurations keep explicit graph
generation so a running experiment is not silently changed. Both Direct and Plan use the
same compiler, schema and contract demonstration catalog. Parsel and direct-Python are unchanged.

The planner writes the public interface and ordered contracts. A Compute names the values
it needs and declares fresh result names/types once. The compiler obtains parameter types
from the signature, resolves each dependency to its unique producer, and generates Input
nodes, internal IDs, input/output ports, edges and the final Output. Public starter signatures
are authoritative when available. Without a starter (e.g. MBPP), the model still specifies
the public signature from the task/examples. No reference solution or hidden test is used.

Example: sort a sequence, then inspect adjacent values:

```json
{
  "contract_version": "1.0",
  "interface": {"mode": "function", "entrypoint": "has_close_elements", "signature": "(numbers: list[float], threshold: float) -> bool"},
  "steps": [
    {"kind": "Compute", "description": "Return an ascending sorted copy; preserve duplicates and do not mutate numbers.", "needs": ["numbers"], "produces": {"ordered": "list[float]"}},
    {"kind": "Compute", "description": "Return whether any adjacent pair in ordered has absolute difference strictly less than threshold. Scan completely or stop when found; fewer than two items returns false.", "needs": ["ordered", "threshold"], "produces": {"answer": "bool"}}
  ],
  "return": "answer"
}
```

`needs`, `produces` and `return` are semantic decisions that remain with the model; wiring
cannot be inferred reliably from descriptions alone. Each name identifies one value. The
compiler does not guess unknown names, pick a producer by type, repair algorithms or retry
generation. Duplicate definitions, duplicate dependencies, forward references, type-invalid
returns and unknown names fail before Python synthesis. Traversal-local state belongs within
Compute; updated values crossing contracts need fresh names.

Branch uses `condition` (an available bool), `produces` (one result), and `then`/`else`
subprograms, each with `steps` and `return`. The compiler infers captured inputs recursively
and maps the selected path's result to the Branch result. Both paths must return a compatible
type. Internal names cannot escape a branch. Nested Branch and empty pass-through paths are
supported; explicit Loop is absent. See `examples/contracts_branch.json`.

The resulting graph is standard editable Core 0.2. It still passes the existing graph validator,
then node-local Python synthesis, ABI checks, deterministic assembly and Docker evaluation.
The existing schema and semantic checks are not relaxed. The compiler does not prove the
description correct, infer omitted requirements or guarantee the generated Python behaves correctly.

Results retain `contract_raw`, parsed `contracts`, `contract_schema_errors`,
`contract_compile_errors`, `contract_valid`, compiler version and the compiled `graph`.
`graph_raw` contains the serialized compiler output in this mode. Accordingly `graph_parse_rate`
describes successful graph materialization; use `contract_parse_rate` for model JSON parsing
and `contract_valid_rate` for successful contract compilation. Official pass@1 includes all
invalid generations in its denominator, as before. Compiler source hash enters the resume
identity; changed compilers cannot silently resume an old run.

Run from the project root with Qwen and Docker available:

```sh
python scripts/run_experiments.py --config experiments/parsel_graphdsl_smoke_v9.json --stage all
python scripts/run_experiments.py --config experiments/parsel_graphdsl_full_v9.json --stage all
```

Outputs go to `outputs/qwen7b-contracts-smoke-v9` and `outputs/qwen7b-contracts-full-v9`.
The full configuration evaluates HumanEval 164 and MBPP 500 tasks with the five existing
comparison conditions. Individual scripts accept `--planner-format contracts`; use it for
both `build_qwen_eval.py` and `run_qwen_pipeline.py`. Mismatched request formats are rejected.

This revision changes the planner language and examples together. Its performance must be
measured again; eliminating manual edge mistakes does not imply 100% graph compilation or
higher functional accuracy. Tests use handwritten implementations and mock inference only.
