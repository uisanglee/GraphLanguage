Implement only the supplied node. Output one Python function with the exact supplied signature;
no Markdown or module-level code. Put imports and helpers inside the function.
Bind every needed local value from inputs explicitly; port names are not Python variables.
Import every non-builtin API used, inside this function (including math and itertools).

inputs maps input port IDs directly to values: use inputs['port'], not inputs['port']['value']
unless that port's actual value is a dictionary with that key. input_bindings describes sources,
not runtime wrappers. Return a dict with exactly the declared output port keys and value types.
Follow return_template (replace its placeholders); do not return internal intermediates or inputs.
When node_examples is present, it contains exact original public I/O pairs for this node. Generalize
the local contract consistently with every pair; never hard-code their literal values. When
program_examples is present, it describes whole-program acceptance behavior, not this node's ABI.
Use it only to clarify the node contract. Never access program_inputs in code, invent intermediate
values from them, call other nodes, or implement behavior owned by another node. If neither field
is present, rely only on the local contract. public_example_evidence contains an original public
example that could not be converted into a literal I/O pair without executing code; treat it only
as whole-program behavioral evidence and follow the same ABI restrictions as program_examples.
Connected nodes are already implemented; never call or reimplement them. Follow the local contract,
including limits, tie rules, reference state, effects, and errors. Loops and small conditionals
belong inside Compute.

For Branch only: config.branches selects the first true condition_port, or null for else.
Use config.region_bindings to map inputs to the selected callback's boundary endpoint keys.
Call regions[region_id](bindings), then map its returned endpoint keys to your output ports.
Only call callbacks listed in region_callbacks; never execute unselected branches.
