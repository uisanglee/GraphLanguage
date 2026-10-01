Write one algorithmic plan as Python-shaped pseudocode. Output only the plan.

Use the public function name and exact parameters/defaults. Keep supplied type
annotations; otherwise optional annotations are sufficient. For stdio use
`def solve(stdin: str) -> str:` and return the complete stdout text.

Use familiar Python syntax: def, a short docstring, assignments, for/while,
if/elif/else, break/continue, return and calls. Preserve nesting, state updates,
early returns and boundary cases. Use descriptive abstract helper calls with
comments describing their intended behavior when implementation details are
unnecessary. Such calls need not have definitions. Declare independent reusable
helpers at module level only when useful; keep iteration and state together.
For a class-method interface, use the supplied class and method signature.

The plan must parse as Python but is not executed. No imports, decorators,
async/generators, lambda, nested functions, global/nonlocal, ellipsis, tests or
module-level execution. Include an explicit return. Preserve all original
requirements and public examples; do not invent restrictions or use hidden tests.
