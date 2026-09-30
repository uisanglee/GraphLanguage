# Safe benchmark evaluation

Generated code is untrusted. No script in this repository executes it directly on the host.

For the current node-wise GraphIR Core versus unchanged upstream Parsel experiment, use
`experiments/parsel_graphdsl_1x1.json` and `PARSEL_GRAPHDSL_1X1.md`. Parsel synthesis itself also runs
in Docker because its original SCC solver executes constraints. The older matrix below describes
the legacy whole-artifact GraphDSL ablations, not the current Core node-wise comparison.
The runner now rejects silently disabled official evaluation. `--limit` selects a fixed prefix per
benchmark; use the HumanEval/MBPP smoke matrix for a short complete evaluation.

## Experiment matrix

`experiments/matrix.example.json` defines the initial paper matrix:

| condition | GraphIR | retrieval | constrained decoding | validation gate |
|---|---:|---:|---:|---|
| `direct` | no | no | no | artifact syntax only |
| `graphir-core-full` | yes | yes | yes | schema + semantics |
| `graphir-core-no-retrieval` | yes | no | yes | schema + semantics |
| `graphir-core-no-grammar` | yes | yes | no | schema + semantics |
| `graphir-core-no-semantic-gate` | yes | yes | yes | schema only |

Copy this file for an actual run and record an exact model revision instead of relying on a moving
model alias. With the published MBPP test split (task IDs 11–510), a full five-condition run contains
25,765 generations before prompt variants, so start with one
benchmark or a small `generation.limit` smoke test.

```bash
python3 scripts/run_experiments.py --config experiments/my-run.json --stage prepare
python3 scripts/run_experiments.py --config experiments/my-run.json --stage generate
python3 scripts/run_experiments.py --config experiments/my-run.json --stage export
python3 scripts/run_experiments.py --config experiments/my-run.json --stage evaluate
```

All stages support `--dry-run`. Generation is resumable by `custom_id`; functional evaluation is
resumable by task ID.

The current smoke output is `outputs/qwen7b-node-smoke-v6`. It isolates the revised input-only
node prompts and ABI checks from previous cached results. Run `--stage all` for generation AND
sandboxed execution; `artifact_valid` alone measures static acceptance, not correctness.
ABI checks catch direct literal references to missing input ports/owned callbacks and clearly
invalid indexing for known scalar/sequence types. They allow dictionary values and unknown types;
aliases, dynamic keys and shadowed names are not a full type/dataflow analysis. Algorithms, index
provenance, output limits and other semantic contracts still require benchmark execution. Failed
attempts are recorded without repair/resampling or selecting candidates using evaluation tests.
The nested planner example is available to lexical retrieval; a one-example budget does not
guarantee it is selected. Inspect demonstration_ids when comparing the direct and plan conditions.

## HumanEval and MBPP

The exporter joins generated artifacts with tests only after generation, so hidden/reference fields
never enter model prompts. `run_safe_functional_eval.py` launches one disposable container per task
with:

- no network;
- read-only root filesystem;
- all Linux capabilities dropped and `no-new-privileges`;
- no Docker socket and no GPU device;
- one CPU, memory/PID/file limits, an inner CPU limit, and two timeouts;
- only one read-only job file mounted;
- a non-root user and a bounded `tmpfs`.

Build the image once:

```bash
docker build \
  -f sandbox/Dockerfile.functional \
  -t graphdsl-functional-eval:0.1 \
  sandbox
```

Then export and evaluate through the matrix runner, or invoke
`scripts/run_safe_functional_eval.py` directly. The worker uses the official HumanEval test body and
the official MBPP `test_list` stored in the normalized corpus. It does not claim EvalPlus scores.

## BigCodeBench

`export_predictions.py` writes the official `task_id`/`solution` JSONL shape. The wrapper invokes
the official `bigcodebench/bigcodebench-evaluate` image with local execution inside a restricted,
networkless outer container. For a paper run, replace `:latest` in the config with an immutable
digest. The wrapper records the resolved image ID in `evaluator_provenance.json`.

BigCodeBench has a wide dependency surface. The default outer limit is 16 GB and 8 CPUs; lower it
only after a ground-truth smoke test. Some tasks that genuinely require network access will fail in
the deliberately networkless policy and must be reported separately rather than silently enabling
network access.

## LiveCodeBench

`export_predictions.py` writes the official custom-evaluator format:

```json
[{"question_id": "...", "code_list": ["..."]}]
```

Build an evaluator image from an exact upstream commit. Dataset download happens at image build
time; runtime is offline.

```bash
docker build \
  -f sandbox/Dockerfile.livecodebench \
  --build-arg LCB_REF=<full-commit-sha> \
  -t graphdsl-livecodebench:release-v6 \
  sandbox
```

The image build asserts that `release_v6` contains 1,055 tasks. The runtime wrapper calls
`lcb_runner.runner.custom_evaluator` in the restricted container.

## SWE-bench

The exporter writes the official fields `instance_id`, `model_name_or_path`, and `model_patch`.
`run_official_evaluator.py` then invokes `swebench.harness.run_evaluation`, which manages an isolated
repository container per instance. Start with SWE-bench Lite or Verified before the 2,294-task full
set; the full harness requires substantial Docker disk space.

SWE-bench generation needs repository context in addition to the normalized issue text. The current
completion pipeline preserves `repo` and `base_commit` metadata and can score supplied patches, but
it is not yet a repository-browsing agent. Results produced without a checked-out repository must be
reported as the issue-only setting, not compared as a standard SWE-bench agent result.

## Reporting

HumanEval/MBPP aggregation with Wilson 95% intervals:

```bash
python3 scripts/summarize_experiment.py \
  --experiment-dir outputs/my-run \
  --output outputs/my-run/summary.csv
```

BigCodeBench, LiveCodeBench, and SWE-bench official result artifacts remain authoritative. Preserve
their complete output directories along with `evaluator_provenance.json`, the experiment config,
prompt/schema hashes in generation rows, model revision, vLLM version, CUDA/driver versions, and
Docker image digests.

## Safety boundary

Containers substantially reduce risk but are not a perfect security boundary. Run experiments on a
dedicated machine without secrets, do not mount home directories or the Docker socket, and do not
enable privileged mode. The official SWE-bench harness may grant additional capabilities to some
instance containers for browser test suites; review its current harness before running untrusted
patches on shared infrastructure.
