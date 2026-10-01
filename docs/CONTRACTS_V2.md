# Positional references and shared value structures (v16)

Use `--planner-format contracts --contract-version 2` for both request preparation
and generation. The orchestrator passes these options for conditions with
`"contract_version": "2"`. Legacy version 1 remains readable and is the standalone
CLI default for existing configurations; do not mix versioned requests and runs.

The model emits one output type/shape per step, never output identifiers. Inputs
use `input:<exact parameter name>`, and prior results use `step:<zero-based index>`.
The compiler generates collision-free value names and all connections. With a
starter signature, the request schema restricts public inputs to an enum. Without
a starter (e.g. some MBPP tasks), the model declares the public interface once;
its parameter names then bind all input references. stdio uses `input:stdin`.

The schema restricts reference syntax. Compilation checks existence and ordering;
static JSON Schema cannot prevent all forward references during one-shot decoding.
No invalid reference is guessed, silently repaired or routed to a different value.
Return values and Branch conditions use the same reference rules. Each Branch path
inherits previous steps and starts local numbering at the Branch index. Locals
are private to that path. The outer Branch result occupies the Branch index.

Compound intermediate values use named records with field descriptions and shapes:

```json
{"kind":"list","items":{"kind":"record","fields":{
  "number":{"description":"Original number","shape":"int"},
  "digit_sum":{"description":"Sum of absolute decimal digits","shape":"int"},
  "original_index":{"description":"Position in input","shape":"int"}
}}}
```

Records are Python dictionaries. `output` declares a structure once; consumers
only refer to its producing step. Compilation copies the same definition into
producer-output and consumer-input metadata, including across Branch captures.
Branch path return structures must agree with the Branch result. Anonymous tuple
types are rejected at generated Compute input boundaries; public tuple results
are still supported via a final conversion. Basic input/output types use strings.

The node synthesizer receives `value_contracts` as the authoritative structure.
The runtime checks exact record keys, nested lists and field types, independently
of optional legacy port diagnostics. Wrong data raises GraphIRPortTypeError before
a consumer can unpack it incorrectly. This checks representation, not whether the
model computed the intended meaning of each field. Unknown Python annotations
retain the legacy permissive behavior; prefer concrete built-in field types.

No extra model sampling, execution-guided repair or hidden tests enter generation.
The v16 prompt, schema and three new demonstrations change together, so report this
as a new generation configuration, not an isolated compiler ablation.

```sh
python scripts/run_experiments.py --config experiments/graphir_contracts_smoke_v16.json --stage all
python scripts/run_experiments.py --config experiments/graphir_contracts_full_v16.json --stage all
```

Both run GraphIR Direct then GraphIR Plan on HumanEval and MBPP with fresh v16
output folders. Full evaluation uses 164 + 500 tasks per condition. Qwen and Docker
are needed for end-to-end accuracy; unit tests execute handwritten fixtures only.
