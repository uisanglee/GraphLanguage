Describe the supplied Python task as one JSON object of GraphIR value contracts (contract_version
"1.0"). Preserve the public interface. The TASK overrides HIGH_LEVEL_PLAN when present.

A Compute step contains kind, description, needs (names of available values), and produces
(new value names mapped to Python types). Values come from public parameters or earlier steps.
Declare each result type once; input types and all graph connections are inferred. Use new names
for updated values. return names the final value. Use built-in types such as list[int].

Each description must specify observable behavior, boundary cases, ordering/ties, limits,
mutation, and relevant APIs precisely enough to implement without the task. Keep traversal,
initialization, state updates, nested loops, and early exits in one Compute returning completed
results. Split only at useful completed intermediate values. Do not turn plan steps into nodes.

For distinct editable paths, Branch contains description, condition (an available bool name),
produces (one result), then and else. Each path has steps and return. It can read outer values;
its local values stay private. Both paths return the declared result type. Only one path runs.
Small conditionals stay inside Compute. For stdio use null entrypoint/signature, read the available
stdin string, and return formatted stdout text.

Emit contracts only: no node IDs, ports, edges, Input/Output nodes, graph examples, or Python code.
