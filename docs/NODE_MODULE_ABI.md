# Isolated node modules (v15)

Node synthesis accepts the exact ABI entry function plus explicit imports, helper
functions, module docstrings and literal constants. Existing single-function
responses remain supported. Signature, port-key, region-callback and undefined-name
checks still apply. Modules cannot use wildcard/relative/future imports, duplicate
bindings, top-level execution, decorated functions or non-literal function defaults.
Global/nonlocal writes remain rejected. Missing imports are not inferred.

The compiler nests each module's declarations inside a private factory and exposes
only its entry function. Helpers remain siblings of the entry rather than becoming
children of its body: they do not accidentally capture entry-local variables. Each
node has its own imports, constants and helpers, preventing cross-node name clashes.
AST assembly preserves literal values, including multiline strings. Generated source
is parsed/compiled but never executed by the generation runner; evaluation still
runs in the Docker sandbox.

Results retain the accepted source, raw response when fence stripping changes it,
and `source_format: isolated-node-module-v1`. The change uses no additional model
calls, candidates or repair retries. It changes both the synthesis prompt and the
accepted source format; do not present a rerun as a compiler-only ablation.

Fresh GraphIR-only configurations run direct then plan, each on HumanEval and MBPP:

```sh
python scripts/run_experiments.py --config experiments/legacy/graphir_abi_smoke_v15.json --stage all
python scripts/run_experiments.py --config experiments/legacy/graphir_abi_full_v15.json --stage all
```

Outputs use separate `outputs/qwen7b-contracts-{smoke,full}-v15` directories. Existing
v13 generations cannot be resumed under changed prompts/compiler fingerprints.
The full run contains HumanEval 164 and MBPP 500 tasks per condition. Qwen and Docker
must be available. This does not repair incorrect algorithms, infer missing imports,
or address public-example diagnostic comparison errors. Accuracy needs a fresh run.
