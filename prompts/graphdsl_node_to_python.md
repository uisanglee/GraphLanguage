Implement exactly the target GraphDSL node, assuming connected nodes and owned region callbacks
are already implemented. Output one Python function with the exact supplied signature and nothing
else. Do not implement connected nodes, a whole solution, tests, or Markdown fences.

ABI: inputs is a dictionary keyed by the node's input port IDs. Return a dictionary containing
exactly its output port IDs. Put imports and local helpers inside your function. No globals or
future imports (the assembled module already enables postponed annotations).
Connected node outputs have already been computed and routed to inputs by the compiler. Do not
call upstream nodes again. Their contracts explain the incoming values; their implementations
are intentionally hidden.

For Loop, Branch, Try, or Context, regions is a dictionary of already-implemented callbacks keyed
by owned region ID. Call regions[region_id](bindings), where bindings maps every RegionInput
output endpoint 'node_id.port_id' to a value. The callback returns a dict keyed by RegionOutput
input endpoints 'node_id.port_id'. Only invoke the region when control flow requires it.
Implement the owner's control flow only; never copy region internals into this function.
For Loop, follow config.body_region, iteration.item_boundary, and carried mappings exactly:
initial_port -> input_boundary; output_boundary -> next iteration and final_port.
For Branch, test each condition_port in config.branches; null means else. For other nodes,
honor the complete local behavioral contract, imports, exact APIs, effects, and exceptions.
EffectToken outputs should propagate incoming tokens (or None when no incoming token exists).
