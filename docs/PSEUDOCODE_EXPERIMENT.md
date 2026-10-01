# Structured pseudocode → compact AST GraphIR experiment (v20)

## Literature and scope of the claim

The format basis is Mishra et al., **Prompting with Pseudo-Code Instructions**,
EMNLP 2023, §3 and Listing 1:
https://aclanthology.org/2023.emnlp-main.939/

That paper uses Python-shaped prototypes, docstrings, control constructs and
descriptively named, potentially unimplemented subtask calls. It evaluates
instructions for NLP tasks, not automatic NL→GraphIR→Python code generation.
Our parser restrictions and graph representation are extensions. Neither their
correctness nor a Qwen code-generation improvement is established by that paper.
No paper prompt/example from a benchmark is copied into our system prompt.

Related evidence has different scope:

- **Planning-Driven Programming**, ACL 2025, §2–3, uses original task descriptions
  alongside generated plans and verification, with iterative refinement. Its plan
  is natural language; it does not publish the static pseudocode grammar used here.
  https://aclanthology.org/2025.acl-long.621/
- **PERC**, COLING 2025, §4.2 and Appendix D, uses pseudocode plans in retrieval and
  code generation. Its illustrated plans are natural-language comment sequences;
  a deterministic control-flow parser cannot infer their semantics exactly.
  https://aclanthology.org/2025.coling-main.534/

We therefore adopt the EMNLP Python-shaped representation, without claiming to
reproduce PERC, LPW, their scores or their complete protocols.

## Accepted syntax

The parser uses Python's AST without executing the plan. Use ordinary synchronous
function definitions, docstrings/comments, expressions, assignments, for/while,
if/elif/else, return, break/continue and calls. Other synchronous Python statements
that parse/compile are retained, except the exclusions below. Helper operations
can have descriptive names without implementations. This leaves genuinely abstract
steps for the Python synthesizer; Python syntax does not imply executable Python.

No imports, decorators, async/await, yield, lambda, nested function/class,
global/nonlocal, ellipsis or module-level execution. Each function must contain
an explicit return. Top-level helper definitions are allowed. A plain class with
methods can represent an existing class-method interface. Annotation names are
never evaluated. Syntax compilation produces a code object but does not execute it.

Original entrypoint parameter names, positional/keyword kinds and defaults must
match. Original annotations are authoritative in the compiled interface. Missing
annotations in a plan are allowed. If no original signature exists, the planned
signature is used. stdio plans have `solve(stdin: str) -> str`; the synthesizer
produces a normal executable stdin/stdout program.

Original example (not from an evaluation task):

```python
def archive_labels(labels: list[str]) -> list[str]:
    """Keep nonempty labels in input order and normalize each for archiving."""
    result = []
    for label in labels:
        if label:
            # Strip surrounding space and normalize letter case for archive keys.
            result.append(normalize_archive_key(label))
    return result
```

`normalize_archive_key` is an abstract operation; its meaning is supplied by the
comment and original specification. There is no generated port/edge syntax.

Compile this example without any model or generated-code execution:

```bash
python scripts/pseudocode_graphir.py --pseudocode examples/archive_labels.pseudo --task examples/archive_labels.task.json --output outputs/archive_labels.graph.json
```

## Static GraphIR representation

Profile identifier: `pseudocode-graphir-3`. GraphIR is the deterministic compact
graph projection of the parsed pseudocode AST, not a second authored language and
not the legacy Core 0.2 eager dataflow executor. Existing Core/ABI code does not
consume this graph. `pseudocode_graphir.validate_graph` validates its connections.

Each declared function owns a graph. The compiler maps pseudocode structure as follows:

| Pseudocode construct | GraphIR node |
| --- | --- |
| parameter | `Input` |
| assignment | `Assign` |
| augmented state update | `Update` |
| direct call or call assignment | `Call` |
| `for`, `while` | `Loop` with a nested body |
| `if`, `elif`, `else` | `Branch` with nested bodies |
| external namespace such as `math` | `Resource` |
| `break`, `continue`, `raise`, `del`, `pass` | `Control` |
| `assert` | `Assert` |
| `return` | `Return` |

The compiler assigns node IDs, derives definition/use ports, and connects current
value producers to consumers. Loop and Branch nodes own recursively compiled
regions; carried/merged values appear as explicit state ports. A function may
therefore contain many editable statement nodes. Function boundaries remain
explicit in the top-level `functions` array.

Edges are `{kind, from: [node, port], to: [node, port]}`. `data` connects a
definition to a use, `control` fixes statement/effect order, and `state` returns a
carried or merged region value. Loop/Branch containment preserves nesting. Calls record target,
arguments and the statically resolved helper function when available. Repeated and
recursive calls remain separate call sites. An abstract helper may remain unresolved;
an attribute namespace such as `math` becomes a Resource node, while an undefined
bare value is rejected. This is not general Python alias analysis or executable bytecode.

Parsing produces the graph deterministically; the exact raw plan is logged beside
the graph in the result record, but is deliberately absent from the GraphIR object.
This prevents the graph-only arm from receiving the complete pseudocode through a
redundant field. Function docstrings remain function descriptions, while local
comments are attached only to the nearest same-indentation statement node so that
abstract helper intent is not discarded. A future editor must recompile or perform a validated graph edit;
no bidirectional UI editor is implemented in this change.

The compiler checks syntax, interface, node kinds and structural connections. It
does not prove algorithmic correctness, full Python typing, test compliance or
program equivalence. Invalid plans stop before synthesis and count as failures.

## Controlled experiment

| Condition | Second-stage input | Logical generations |
| --- | --- | --- |
| source-pseudocode-1x1 | original public task + pseudocode | 1 plan + 1 program |
| graphir-only-1x1 | GraphIR + public interface | 1 plan + 1 program |
| source-graphir-1x1 | original public task + GraphIR | 1 plan + 1 program |
| source-python-1x1 (optional) | original public task | 1 program |

Every planned arm shares the same raw plan via a content-addressed inference
journal. Even the pseudocode arm goes through the same parser gate to hold planner
failures constant. Compare source-pseudocode vs source-graphir to isolate the
representation effect; compare the two graph arms to isolate original-context
availability. The optional baseline uses the same public task/examples and code
prompt. It has a smaller inference budget, so report tokens and latency as well
as Pass@1. Graph encoding is longer than pseudocode, not a token-matched treatment.

The default matrix omits the direct Python baseline. `pseudocode_ablation_full_v20.json`
adds it. All arms use ordinary Python interfaces and one whole-program synthesis;
this does not measure per-node synthesis, local repair or combinatorial selection.
No extra NL high-level-plan pass, retrieval, retries, self-repair or test selection
is used. No JSON constrained decoding is needed because the model emits pseudocode;
Python parsing is a post-generation gate, not grammar-constrained decoding.

Source context contains the original task, starter code, entrypoint, fixed signature
and available public examples. Reference solutions, hidden tests and opaque official
metadata are excluded. In the graph-only arm these original fields and separately
preserved public examples are absent from stage two (although a generated plan can
itself mention examples). The public signature remains fixed in every arm.

## Run and inspect

Use `experiments/pseudocode_smoke_v20.json` first, then
`experiments/pseudocode_humaneval_v20.json` for HumanEval only or
`experiments/pseudocode_full_v20.json` for HumanEval + MBPP. Run from repository root:

```bash
python scripts/run_experiments.py --config experiments/pseudocode_smoke_v20.json --stage all
```

Stages remain prepare, generate, export, evaluate, summarize. Docker executes
generated programs under the existing network-disabled/read-only/resource-limited
functional evaluator. Plans are never executed. Full configs evaluate 164 HumanEval
and 500 MBPP tasks; smoke limits generation to three per benchmark. Invalid artifacts
remain in the denominator as failures. BigCodeBench and LiveCodeBench can use this
pipeline through their existing adapters with `evaluation.run_official=true` and
the appropriate evaluation image. Repository patches and APPS are not supported
by this new path.

Generation results retain `pseudocode_raw`, `pseudocode_errors`, `graph`,
`pseudocode_valid`, `graph_valid`, `generated_artifact`, `artifact_errors`, per-stage
inference telemetry and `llm_calls`. The latter counts attempted logical stages;
`actual_api_calls` and `cached_calls` distinguish shared cache reuse. Token/time
totals include the logical cost of the shared plan for each arm; sum physical API
calls separately for actual experiment cost. Truncated generations fail explicitly.
Pending uncertain network requests are not silently resampled. Use fresh output
directories for new prompts/settings; the identity file rejects mixed resumes.

See each condition's `evaluation/<benchmark>/results.jsonl` and the experiment's
`summary.csv` for accuracy. It also reports mean total, Assign, Update, Call, Loop
and Branch node counts. `artifact_valid` is only a static gate, not accuracy.

## Cleanup

Previous experiment configs moved to `experiments/legacy/`. Old docs were updated
to those paths. README now leads with this pipeline. Shared HTTP transport,
benchmark selection and signature extraction were removed from legacy generation
implementations and extracted into independent modules. The new path has no
legacy contract/schema/ABI/retrieval dependencies. Older modules remain for old
results and Parsel comparison, and their regression tests remain available.
No benchmark datasets, inference outputs or third-party Parsel source were deleted.
