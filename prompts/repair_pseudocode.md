Repair an invalid Python-shaped pseudocode plan. Output only the complete revised
plan, with no Markdown fence or explanation.

Use the original public specification as authoritative. Keep the exact public
function name, parameters, parameter kinds, and defaults. Preserve every stated
behavior, boundary case, and public example. Correct every reported compiler
error, but do not rewrite valid algorithmic steps unnecessarily and do not invent
new requirements.

The revised plan must follow the same restricted profile: ordinary synchronous
top-level functions, docstrings/comments, expressions, assignments, for/while,
if/elif/else, break/continue, return, calls, comprehensions, and expression-level
lambdas. No imports, decorators, async/generators, nested functions, global or
nonlocal statements, ellipsis placeholders, tests, or module-level execution.
Every function must have an explicit return.
