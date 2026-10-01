# Atomic function contracts (v17)

The benchmark default represented by the v17 configurations maps one public Python
function (or one stdio program) to one Compute. The model emits only the Compute's
complete behavioral description. The compiler derives every input port from the
public signature, connects all parameters, derives the output type, generates the
result identifier, and adds Input/Output nodes and edges.

This removes model-authored step numbering, intermediate names, `needs`, result
types, Branches and wiring. Loops, nested conditions, recursion, helper functions,
initialization and state updates stay inside the Compute implementation. A complex
stateful algorithm therefore remains one editable node by default.

Automatic benchmark generation does not guess whether a subproblem is sufficiently
independent to split. Explicit decomposition remains available through contract v2,
whose named record contracts are appropriate after a user or experiment explicitly
chooses subfunction boundaries. A visual editor can request such a split for one
selected Compute and retain the original node until the replacement validates.

Version 3 contract output has this shape:

```json
{
  "contract_version": "3.0",
  "compute": {
    "description": "Complete, independently implementable function contract."
  }
}
```

`interface` is omitted when starter code fixes it and is otherwise emitted once.
Public examples remain immutable synthesis evidence and are attached to the single
Compute as exact node I/O whenever they can be parsed. The GraphIR-to-Python ABI,
isolated helper/import support, static checks and Docker evaluation are unchanged.

Fresh 1x1 runs:

```sh
python scripts/run_experiments.py --config experiments/graphir_atomic_smoke_v17.json --stage all
python scripts/run_experiments.py --config experiments/graphir_atomic_full_v17.json --stage all
```

Both configurations run GraphIR Direct followed by GraphIR Plan on HumanEval and
MBPP. They use fresh v17 output directories. No repair attempt, resampling or test
feedback is added. Contract v3 changes the planner prompt, schema and demonstration,
so its functional accuracy must be measured in a new run.
