Describe the supplied Python task as one JSON object of GraphIR value contracts (contract_version
"1.0"). The TASK overrides HIGH_LEVEL_PLAN when present.
When fixed_interface is provided, omit interface from your output. available_inputs gives the
exact immutable parameter names and types; use these names verbatim, including unusual spellings.
The compiler supplies the public signature. Otherwise specify interface from the task/examples.
Use public examples embedded in TASK to retain observable limits, ordering, ties, formatting, and
boundary behavior in descriptions. Do not copy examples into the output or treat an example-only
hypothesis as an explicit natural-language fact.

A Compute step contains kind, description, needs (names of available values), and produces
(new value names mapped to Python types). Values come from public parameters or earlier steps.
Declare each result type once; input types and all graph connections are inferred. Use new names
for updated values, never overwrite parameters or previous results. return is an existing value
name, not a type or expression. To return several values, produce one tuple value in a Compute.
Use built-in types such as list[int].

Each description must specify observable behavior, boundary cases, ordering/ties, limits,
mutation, and relevant APIs precisely enough to implement without the task.
Preserve concrete input formats, units, exact output strings, and example-derived edge cases;
the implementer cannot see the original task or plan. Never replace these details with “as specified”.
Keep traversal, initialization, state updates, nested loops, and early exits in one Compute returning completed
results. Split only at useful completed intermediate values. Do not turn plan steps into nodes.

For distinct editable paths, Branch contains description, condition (an available bool name),
produces (one result), then and else; never needs (captures are inferred). Each path has steps and return. It can read outer values;
its local values stay private. Both paths return the declared result type. Only one path runs.
Small conditionals stay inside Compute. For stdio use null entrypoint/signature, read the available
stdin string, and return formatted stdout text.

Emit contracts only: no node IDs, ports, edges, Input/Output nodes, graph examples, or Python code.
