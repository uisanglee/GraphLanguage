# Named contracts and deterministic graph construction (v9)

## LiveCodeBench public I/O (v14)

LiveCodeBench public cases now use the same immutable example sidecar as HumanEval/MBPP.
LeetCode `functional` cases are decoded one argument per line using JSON first and Python literal
syntax second; no expression is executed. The repaired starter method signature maps those values
to public parameters while excluding implicit `self`/`cls` from GraphIR Input nodes. AtCoder and
Codeforces `stdin` cases preserve input/output strings exactly, including newlines, as
`stdin`/`stdout` graph examples. Any future non-literal functional case falls back to raw evidence.

Across the checked normalized LiveCodeBench corpus, 1,054/1,055 tasks expose 2,762 public cases:
1,095 functional and 1,667 stdin. All currently parse structurally; the one task without public
cases retains the previous prompt shape. Reference solutions and official private tests are not
used. Representative method and stdio contracts, exact node-example routing, and raw fallback are
covered by local tests.

```sh
python scripts/run_experiments.py --config experiments/legacy/graphir_livecodebench_smoke_v14.json --stage all
python scripts/run_experiments.py --config experiments/legacy/graphir_livecodebench_full_v14.json --stage all
```

These configurations compare direct Python with GraphIR Direct/Plan and use the existing official
LiveCodeBench evaluator image. The full run contains all 1,055 normalized tasks.

## Preserved public examples for node synthesis (v13)

GraphIR contract requests now carry original public examples in an immutable request sidecar.
They are extracted with Python AST/doctest parsing only; submitted expressions are never run.
Literal calls and expected values become program-boundary `graph.examples`. The exact original
assert/doctest text is also retained in graph metadata, so JSON conversion does not hide Python
tuple/list spelling. Examples that cannot be reduced to literals remain raw evidence rather than
being guessed or discarded. Reference solutions, challenge tests, and hidden evaluator tests are
never read by this path.

The sidecar is not duplicated in the NL planner message: the original task already contains its
public examples. After deterministic graph construction, a graph with exactly one synthesized
node receives exact `node_examples`. In a multi-node graph, only a node feeding the public Output
receives `program_examples`, explicitly marked as whole-program evidence rather than local ABI
values. Non-literal evidence uses `public_example_evidence`. Intermediate expected values are never
invented. With no public examples, all three fields are omitted and the previous prompt shape is
preserved. UI clients may render `graph.examples` as virtual ExampleInput/ExpectedOutput nodes;
they are constraints, not executable data-flow nodes.

This remains 1x1: examples guide the single synthesis attempt and may be evaluated afterward, but
there is no selection, retry, repair, or hidden-test feedback. Parsel is unchanged. For an ablation,
set `"preserve_public_examples": false` on a GraphIR condition; request preparation then passes
`--no-preserve-public-examples`.

Use fresh v13 outputs:

```sh
python scripts/run_experiments.py --config experiments/legacy/parsel_graphdsl_smoke_v13.json --stage all
python scripts/run_experiments.py --config experiments/legacy/parsel_graphdsl_humaneval_v13.json --stage all
```

The focused smoke ablation compares contract-only against preserved-example synthesis:

```sh
python scripts/run_experiments.py --config experiments/legacy/graphir_examples_ablation_smoke_v13.json --stage all
```

## Evaluation fixes and contract guards (v11)

HumanEval evaluation now preserves public starter helpers/imports but removes the target stub.
Setup, candidate and tests compile separately in the same sandbox namespace, so a candidate's
future imports remain legal. Public example diagnostics parse only the target docstring, not
the closing Python quotes. Reference solutions are never used as setup or generation inputs.

Reevaluate existing v10 candidates **without any LLM calls**, preserving the original files:

```sh
python scripts/run_experiments.py --config experiments/legacy/parsel_graphdsl_humaneval_v10.json --stage reevaluate --evaluation-subdir evaluation-v11
```

This exports fresh jobs, rebuilds the Docker worker, evaluates all four configured conditions,
and writes `summary-evaluation-v11.csv` in the original experiment directory. Per-condition
jobs/results live in `evaluation-v11/humaneval/`. An existing destination is refused; after an
interruption use the individual `export`, `evaluate`, `summarize` stages with the same subdir,
as appropriate. Reevaluation does not reclassify old generation gates using new validators.

For **new generation**, use `experiments/legacy/parsel_graphdsl_smoke_v12.json`,
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
python scripts/run_experiments.py --config experiments/legacy/parsel_graphdsl_humaneval_v10.json --stage all
```

The rest of this document describes the underlying contract format and v9 introduction.

Use `experiments/legacy/parsel_graphdsl_smoke_v9.json` and `parsel_graphdsl_full_v9.json` for the
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
python scripts/run_experiments.py --config experiments/legacy/parsel_graphdsl_smoke_v9.json --stage all
python scripts/run_experiments.py --config experiments/legacy/parsel_graphdsl_full_v9.json --stage all
```

Outputs go to `outputs/qwen7b-contracts-smoke-v9` and `outputs/qwen7b-contracts-full-v9`.
The full configuration evaluates HumanEval 164 and MBPP 500 tasks with the five existing
comparison conditions. Individual scripts accept `--planner-format contracts`; use it for
both `build_qwen_eval.py` and `run_qwen_pipeline.py`. Mismatched request formats are rejected.

This revision changes the planner language and examples together. Its performance must be
measured again; eliminating manual edge mistakes does not imply 100% graph compilation or
higher functional accuracy. Tests use handwritten implementations and mock inference only.
