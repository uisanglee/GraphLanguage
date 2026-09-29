# GraphDSL node synthesis and original Parsel (1×1)

The current matrix is experiments/parsel_graphdsl_1x1.json. Its output directory is
outputs/qwen7b-parsel-graphdsl-nodes-v2; do not mix it with results from the old custom adapter.

## What runs

| Condition | Generation |
|---|---|
| direct-python-1x1 | task → one Python artifact |
| graphdsl-direct-1x1 | task → one graph → one implementation per executable node → deterministic wiring |
| graphdsl-plan-1x1 | task → shared plan → graph → node implementations → deterministic wiring |
| parsel-direct-1x1 | task → Parsel → original SCC synthesizer with n=k=1 |
| parsel-plan-1x1 | task → shared plan → Parsel → original SCC synthesizer with n=k=1 |

Input, Output, Literal, RegionInput and RegionOutput are compiled boundaries, not LLM calls.
Compute and control nodes have separate functions. A node sees its own contract, connected node
contracts, and contracts of its owned region callbacks. It never sees the original task or another
node's implementation. The compiler wires data ports in topological order within each region.
Loop and Branch implementations call already-implemented region callbacks only when needed.
Imports are local to node functions, avoiding collisions between independently generated modules.
Source functions retain stable node-ID comments and node results are recorded individually.

## Original Parsel fidelity

third_party/parsel is the unchanged official repository at commit
bc1687cab9e8a79249f0d3e3cb4f5826dba39a95. Host and container source hashes must agree.
The removed parsel_protocol.py, custom function system prompt, and LLM assembler are not used.

The sandbox calls original get_graph, parsel_graph, Function.implement, CodeGen.generate,
SCC/constraint evaluation, and fns_to_str. Only num_completions, min_completions and
num_completions_eval are set to 1. Automatic expansion, infilling and test generation remain at
the original off defaults. Existing constraints in generated Parsel are handled by the original
algorithm, not stripped or independently reinterpreted.

CodeGen's legacy OpenAI Completion transport is replaced by file RPC to the host, which forwards
the unchanged prompt/stop/temperature/token parameters to Qwen's /v1/completions endpoint.
There is no new function-level chat prompt. The original Python defaults remain 500 completion
tokens and temperature 0.6. GraphDSL node synthesis defaults to 4096 tokens and temperature 0.0.
These are **single-candidate comparisons, not equal-token or identical-decoding comparisons**.
Preserving Parsel's defaults is intentional; report measured cost and these settings.

The export uses the original fns_to_str and exec_pre, excluding constraint calls from the final
benchmark artifact after synthesis has already processed them. A class benchmark gets a mechanical
method wrapper delegating to the original free root function; stdio gets a main guard. These are
external benchmark adapters, not changes to the original synthesis algorithm.

NL→Parsel's grammar description follows Appendix O, Figure A.48, of the supplied NeurIPS paper.
The task-object interface, direct-NL condition and class/stdio instructions are explicit benchmark
adaptations. The old imposed helper-count limit, one-sentence rule and prohibition on constraints
have been removed. This is not a reproduction of the paper's reported multi-sample GPT-4 scores.

## Budgets and resume

Both plan conditions use the same prompt, model, temperature and 2048-token limit and share a
persistent per-task plan cache. IR generation has an 8192-token limit for both methods.
Exactly one response is used per stage/component. Responses are journaled before synthesis and
reused after interruption. An uncertain request is marked pending and stops automatic resampling;
inspect/reconcile it explicitly. Changing run inputs/settings requires a new output path.
Model-output validation failures are terminal candidate failures, not retry opportunities.
A limit of N selects a fixed first N tasks **per benchmark**, before resume filtering.

## Benchmarks and evaluation

HumanEval 164, MBPP published test 500, BigCodeBench 1140, LiveCodeBench release_v6 1055.
LCB is normalized into 444 class/function tasks and 611 stdio tasks when request files are built,
including when loading old normalized JSONL. Hidden tests/reference solutions are not planner inputs.
SWE-bench is outside the common Python-node comparison; repository patches use the explicit old
whole-artifact mode and require a separate experiment.

HumanEval/MBPP use disposable non-root, networkless Docker containers. NumPy is installed because
the original Parsel exec_pre imports it for all candidates. BigCodeBench and LCB use their official
evaluators. Official per-task files are normalized for summary, and incomplete/infrastructure-failed
evaluations have no published Pass@1. BigCodeBench uses calibrated=False because these pipelines
already emit complete modules (prepending its stub would invalidate future imports).
The official LCB custom evaluator requires a complete release; partial evaluation fails explicitly.

## Server commands

Run from the repository root on the Linux GPU host. Use a Qwen vLLM server supporting both
/v1/chat/completions and /v1/completions. Fix the exact model revision and evaluator images for a paper run.

```bash
docker build -f sandbox/Dockerfile.parsel -t graphdsl-parsel:0.1 .
docker build -f sandbox/Dockerfile.functional -t graphdsl-functional-eval:0.1 sandbox

python3 scripts/run_experiments.py --config experiments/parsel_graphdsl_1x1.json --stage prepare
python3 scripts/run_experiments.py --config experiments/parsel_graphdsl_1x1.json --stage generate
python3 scripts/run_experiments.py --config experiments/parsel_graphdsl_1x1.json --stage export
python3 scripts/run_experiments.py --config experiments/parsel_graphdsl_1x1.json --stage evaluate
python3 scripts/run_experiments.py --config experiments/parsel_graphdsl_1x1.json --stage summarize
```

Build/install BigCodeBench and LiveCodeBench evaluator images as described in EVALUATION.md.
For a small end-to-end run use experiments/parsel_graphdsl_smoke.json (HumanEval/MBPP, 3 each).
The original Parsel process may execute generated constraints and kill spawned workers; it is
therefore always run inside a separate networkless container. Only temporary RPC files are mounted,
not the project, credentials or Docker socket.

## Verification status

Local unit/integration tests cover loop-carried values, branch callbacks, class wrapping, local
node replacement, malformed graphs, journal resume, request generation, original parser/SCC/prompt
behavior, and official result normalization. Model responses in tests are hand-authored fixtures.
Actual GPU inference and Docker image execution remain unverified on the development machine,
which has no Docker command or Qwen endpoint. No benchmark accuracy is claimed yet.
