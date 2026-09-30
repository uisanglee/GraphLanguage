"""Build evaluation-only tests from original public task data, never generated graphs."""
import ast
import doctest
import hashlib


def public_tree(source):
    """Parse a public completion prefix without executing it."""
    try:
        return ast.parse(source)
    except SyntaxError:
        return ast.parse(source + '\n    pass\n')


def humaneval_setup(task):
    """Preserve public helpers/imports, excluding the target stub and all references."""
    source = task.get('starter_code') or task.get('prompt', {}).get('primary', '')
    tree = public_tree(source)
    tree.body = [node for node in tree.body if not (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == task['entrypoint'])]
    return ast.unparse(tree)


def example_text(task):
    prompt = task.get('prompt', {}).get('primary', '')
    # HumanEval supplies a Python prefix. Only the target docstring is doctest text.
    # Plain-text fixtures/tasks remain supported, but malformed Python is not guessed.
    if task.get('entrypoint'):
        tree = public_tree(prompt or task.get('starter_code', ''))
        target = next((node for node in tree.body if isinstance(node, ast.FunctionDef)
                       and node.name == task['entrypoint']), None)
        return (ast.get_docstring(target) or '') if target is not None else ''
    return prompt


def public_checks(task, benchmark):
    if benchmark == 'mbpp':
        tests = task.get('public_tests', [])
        source = '\n'.join(tests)
        origin = 'normalized.public_tests'
        count = len(tests)
    elif benchmark == 'humaneval':
        try:
            prompt = example_text(task)
            count = len(doctest.DocTestParser().get_examples(prompt))
        except (ValueError, SyntaxError):
            return {'source': '', 'count': 0, 'origin': 'original_prompt_doctest', 'status': 'unparseable'}
        source = (
            'import doctest as _public_doctest\n'
            f'_public_case = _public_doctest.DocTestParser().get_doctest({prompt!r}, globals(), "public", "prompt", 0)\n'
            '_public_runner = _public_doctest.DocTestRunner()\n'
            '_public_result = _public_runner.run(_public_case)\n'
            'assert _public_result.failed == 0, "original public examples failed"\n'
        ) if count else ''
        origin = 'original_prompt_doctest'
    else:
        return {'source': '', 'count': 0, 'origin': None, 'status': 'unsupported'}
    return {'source': source, 'count': count, 'origin': origin,
            'status': 'available' if count else 'unavailable',
            'sha256': hashlib.sha256(source.encode()).hexdigest()}
