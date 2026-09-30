Implement exactly the target GraphDSL node, assuming connected nodes and owned region callbacks
are already implemented. Output one Python function with the exact supplied signature and nothing
else. Do not implement connected nodes, a whole solution, tests, or Markdown fences.

ABI: inputs is a dictionary keyed by the node's input port IDs. Return a dictionary containing
exactly its output port IDs. Put imports and local helpers inside your function. No globals or
future imports (the assembled module already enables postponed annotations).
Connected node outputs have already been computed and routed to inputs by the compiler. Do not
call upstream nodes again. Their contracts explain the incoming values; their implementations
are intentionally hidden.
input_values describes the actual values in inputs. input_bindings gives their source and contract;
the source port name is NOT a wrapper in memory. A float arriving from source.value is accessed as
inputs['number'], never inputs['number']['value']. A dictionary-valued port may legitimately be
indexed again. Return only the target node's declared output keys.
regions contains ONLY entries listed in region_callbacks. Ordinary connected nodes are not
callbacks. A Compute with no owned body receives an empty regions dict; never call a neighbor.

For Loop, Branch, and controllers with owned bodies, regions is a dictionary of already-implemented callbacks keyed
by owned region ID. Call regions[region_id](bindings), where bindings maps every RegionInput
output endpoint 'node_id.port_id' to a value. The callback returns a dict keyed by RegionOutput
input endpoints 'node_id.port_id'. Only invoke the region when control flow requires it.
Implement the owner's control flow only; never copy region internals into this function.
For Loop, follow config.body_region, iteration.item_boundary, and carried mappings exactly:
initial_port -> input_boundary; output_boundary -> next iteration and final_port.
For Branch, test each condition_port in config.branches; null means else. For other nodes,
honor the complete local behavioral contract, imports, exact APIs, effects, and exceptions.
EffectToken outputs should propagate incoming tokens (or None when no incoming token exists).

Core 0.2 nested bodies have been expanded into the same callback ABI deterministically. Implement
only the controller; call its body callbacks and never reimplement their computations. In every mode,
return a dictionary whose literal keys exactly equal the target node's output port IDs. Do not return
an adjacent node's ports and do not implement an adjacent responsibility.
