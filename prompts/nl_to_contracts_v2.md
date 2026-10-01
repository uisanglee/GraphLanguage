Return one JSON contract_version "2.0" program. TASK overrides HIGH_LEVEL_PLAN.
With fixed_interface, omit interface and use available_inputs exactly. Otherwise declare the
public interface once from the task and use its parameter names. For stdio use null entrypoint
and signature; the only public input is stdin. Never rename public parameters.

Each Compute has kind, description, needs, output. References are only "input:<parameter>"
or "step:<zero-based index>". Each step produces ONE completed value. The compiler assigns
output names and wiring. Never emit produces, IDs, Python expressions, literal values as
references, or undeclared references. return references one available input or completed step.
Keep a traversal, its state updates and early exits together. Split at completed useful values.

output is a Python type string (e.g. "int", "list[str]") or a named structure:
{"kind":"record","fields":{"number":{"description":"Original number","shape":"int"}}}
{"kind":"list","items":<type or record>}
Use named records for compound intermediate values, not positional tuples. Define each field's
meaning and type once on the producing step. Consumers reference that step; they cannot redeclare
its structure. Descriptions must agree with these fields. Convert to the required public tuple
or other public representation in the final Compute when necessary.

Branch has kind, description, condition (an available bool reference), output, then, else.
Each path has steps and return. Paths inherit earlier steps; their first local index is the
Branch's index. Local steps then increment that index. Sibling path locals are invisible; after
Branch, that index refers to the Branch result. Both paths return the same declared structure.
Small conditions remain inside Compute. Never use a condition expression as a reference.

Descriptions must preserve required behavior, input formats, units, boundaries, ordering/ties,
exact strings, mutation and APIs. Use public examples as evidence without claiming an inferred
rule was explicit. Implementers see only local contracts and applicable public evidence.
Emit no examples or Python code. Loops stay inside Compute.
