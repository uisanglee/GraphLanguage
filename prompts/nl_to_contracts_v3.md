Return one JSON atomic contract with contract_version "3.0". TASK overrides HIGH_LEVEL_PLAN.
With fixed_interface, omit interface: the compiler preserves its exact public signature.
Otherwise declare the function or stdio interface from TASK. Never rename parameters.

Return exactly one compute object with one description. Describe the complete behavior of the
public function so it can be independently implemented: algorithm, all input meanings and
formats, output meaning and format, boundary cases, ordering and tie rules, mutation, errors,
required libraries, and every explicit constraint. Preserve exact strings and units. Public
examples in TASK are behavioral evidence; generalize them and do not copy them into the output.

All loops, nested loops, local helpers, local state, recursion, and conditional control flow
belong inside this one Compute. Do not decompose initialization, traversal, state updates,
sorting, formatting, or branches into steps. Do not emit steps, needs, output types, value names,
references, ports, edges, examples, GraphIR nodes, plans, or Python code. The compiler derives
all inputs and the result type from the public interface and constructs Input/Output/edge wiring.

For stdio, use mode "stdio" with null entrypoint/signature and describe parsing and exact stdout.
