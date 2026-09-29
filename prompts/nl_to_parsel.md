You will aim to solve the supplied task in Parsel. Parsel is a natural-language intermediate
language, not Python source code. If HIGH_LEVEL_PLAN is present, translate that solution plan into
Parsel; otherwise translate the task directly.

Each line should contain either a function description or a function reference.
A function description should be of the form:
function_name(arg1, arg2): Description of the function
A function reference should be of the form:
function_name
Use indentation to indicate dependencies between functions. For example, if function A calls
function B, then function B should be indented under function A.
Make sure that the top-level function matches the name of the function in the solution plan.

Output rules:
- Output Parsel source only, with no Markdown fence or surrounding explanation.
- Never output `def`, Python statements, imports, comments, pseudocode steps, or function bodies.
- Every non-empty line must be a Parsel function description or an indented function reference.
- Preserve the exact public function name and arguments available in the task.
- Descriptions must be implementation-independent behavioral contracts: state inputs, outputs,
  boundary cases, required effects, and complexity constraints needed to reimplement the function.
- Use a small decomposition. Add a child only when it is a reusable semantic responsibility, and
  make the parent description say how the child participates.

Before answering, silently verify that removing the natural-language text after each colon would
leave function signatures and references, not executable Python.

Benchmark interface adapter: output Parsel source only. For a free-function task retain its public
name and arguments. For a class method, define that method as a free root function with the same
arguments except self; a deterministic external wrapper delegates to this root. For a stdio task,
the root takes no arguments, reads stdin and writes stdout. Only supplied problem information is
available; do not assume access to private tests or reference implementations.
