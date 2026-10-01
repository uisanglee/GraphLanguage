Implement the supplied programming task as complete executable Python. Output
only Python source, including necessary imports and helper definitions.

Use the provided pseudocode or GraphIR as the algorithmic plan. GraphIR is a
compiler-produced compact view of that pseudocode AST, not a separate authored
language. Its nodes are Input/Resource, Assign, Update, Call, nested Loop/Branch,
Assert, Return and Control operations. Node config contains normalized expressions;
data edges connect definitions to uses, control edges preserve statement order,
and state edges expose values carried through a Loop or merged by a Branch.
Respect call arguments, recursion, nesting, state, early termination and region
fallthrough. Implement abstract helpers and required imports. Use ordinary Python
signatures and returns.
The compiled public interface is authoritative over annotations in plan source.

If source_specification is present, preserve its complete requirements, signature,
and starter helpers. Resolve a conflicting generated plan using the original
specification. If public_examples is present, treat it as a separate set of
non-exhaustive behavioral constraints: satisfy every example, do not hard-code its
particular inputs or outputs, and generalize consistently with the available plan
and source specification. The examples are evidence, not executable GraphIR nodes.
If source_specification is absent, implement the plan and public interface as
provided. For stdio output a complete program reading stdin and writing stdout.
For function tasks expose the required callable/class method. Do not output tests.
