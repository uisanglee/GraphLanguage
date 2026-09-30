Implement only the supplied node. Output one Python function with the exact supplied signature;
no Markdown or module-level code. Put imports and helpers inside the function.

inputs maps input port IDs directly to values: use inputs['port'], not inputs['port']['value']
unless that port's actual value is a dictionary with that key. input_bindings describes sources,
not runtime wrappers. Return a dict with exactly the declared output port keys and value types.
Connected nodes are already implemented; never call or reimplement them. Follow the local contract,
including limits, tie rules, reference state, effects, and errors. Loops and small conditionals
belong inside Compute.

For Branch only: config.branches selects the first true condition_port, or null for else.
Use config.region_bindings to map inputs to the selected callback's boundary endpoint keys.
Call regions[region_id](bindings), then map its returned endpoint keys to your output ports.
Only call callbacks listed in region_callbacks; never execute unselected branches.
