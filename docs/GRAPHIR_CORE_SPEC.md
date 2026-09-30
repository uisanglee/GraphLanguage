# GraphIR Core 0.2

GraphIR Core is the default model-facing language. `docs/GRAPHDSL_SPEC.md` and
`schemas/graphdsl.schema.json` remain the frozen legacy 0.1 representation for reproducibility.

## Design boundary

- A graph node is an independently implementable semantic responsibility, not a Python statement.
- An edge is only a typed connection from one output port to one input port.
- Local loops and conditionals belong inside `Compute`.
- `Loop` and `Branch` own editable nested graphs. Core does not require authored regions,
  boundary nodes, callback bindings, carried mappings, or graph back-edges.
- The deterministic normalizer expands Core into the existing executable node ABI. It fills syntax
  defaults only; it does not add, remove, reconnect, or reinterpret semantic nodes.

## Nested control

See `examples/core_nested.graph.json` for a complete Loop containing a Branch.
Each body is `{inputs, outputs, nodes, edges}`. Its inputs/outputs are type maps. Edges use
`$input.port` as sources and `$output.port` as destinations; these pins are not authored nodes.
Node IDs are local to their body. Dots and the `__` prefix are reserved for compiler-generated IDs.

Loop has `control: {for_each: "items", item: "item", state: ["total"]}` and `body`.
The iterable is an owner input; state names appear in both owner inputs and outputs. Body inputs
are the current item, state and invariant owner inputs. Body outputs are exactly the next state.
Types must agree. Empty iteration returns the initial state. This compact controller currently
supports `for_each`; while/async/break behavior remains a Compute contract or a legacy 0.1 graph.

Branch has `branches: [{when: "condition", body: ...}, {when: null, body: ...}]`.
Conditions are boolean owner inputs, evaluated in order; the sole else must be last. Each body
accepts a same-name subset of owner inputs and returns exactly the owner outputs with matching
types. Bodies can recursively contain Loop/Branch nodes.

The compiler namespaces nodes, creates boundary pins and expands state/invariant mappings.
The synthesizer still implements each executable node separately, including controllers that call
their already-implemented body callbacks. The generated canonical graph is audit output; to reload
an experiment use its original `graph` and normalize again (metadata alone never changes dialect).

## Compact document

```json
{
  "graphir_version": "0.2.0",
  "interface": {
    "mode": "function",
    "entrypoint": "absolute_distance",
    "signature": "(a: float, b: float) -> float"
  },
  "nodes": [
    {
      "id": "a",
      "kind": "Input",
      "description": "The public parameter a.",
      "inputs": {},
      "outputs": {"value": "float"}
    },
    {
      "id": "b",
      "kind": "Input",
      "description": "The public parameter b.",
      "inputs": {},
      "outputs": {"value": "float"}
    },
    {
      "id": "distance",
      "kind": "Compute",
      "description": "Return the non-negative absolute difference in O(1) time.",
      "inputs": {"a": "float", "b": "float"},
      "outputs": {"value": "float"}
    },
    {
      "id": "result",
      "kind": "Output",
      "description": "Return the absolute distance.",
      "inputs": {"value": "float"},
      "outputs": {}
    }
  ],
  "edges": [
    {"from": "a.value", "to": "distance.a"},
    {"from": "b.value", "to": "distance.b"},
    {"from": "distance.value", "to": "result.value"}
  ]
}
```

Input and output port namespaces are directional, so the same port name may appear on both sides of
a node. Equivalent `typing` spellings are normalized to built-in generics before type comparison.

## Defaults

- target: Python `>=3.10`
- node label: derived from node id
- node scope: root
- function Input binding: node id
- function Output mode: return
- stdio Input/Output modes: stdin/stdout
- missing config, constraints, examples, and metadata: empty

## Profiles

Default generation now uses `schemas/graphir-compact.schema.json`: only Input, Output, Compute,
Branch for function/stdio tasks. See COMPACT_GENERATION.md. The following extended kinds remain
supported for historical graphs but are not offered to the default planner.

Function and stdio tasks use `Input`, `Output`, `Literal`, `Compute`, `Call`, and only when useful
`Loop`, `Branch`, `Resource`, `Context`, `Effect`, `Assert`, or `Test`. Repository tasks additionally
use `SourceArtifact`, `Locate`, `Edit`, `AddArtifact`, `DeleteArtifact`, and `Patch`.

The normative model-facing schema is `schemas/graphir-core.schema.json`. Core examples use the
`examples/core_*.graph.json` naming convention.
