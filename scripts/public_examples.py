"""Build evaluation-only tests from original public task data, never generated graphs."""
import ast
import doctest
import hashlib
import json


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


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return prefix + '.' + node.attr if prefix else None
    return None


def _json_literal(node):
    """Return a JSON-safe literal without evaluating submitted code."""
    try:
        value = ast.literal_eval(node)
        # JSON is the interchange format. Exact Python spelling is separately
        # retained in raw_source (notably tuple versus list).
        return json.loads(json.dumps(value, ensure_ascii=False)), True
    except (ValueError, TypeError, SyntaxError, OverflowError):
        return None, False


def _text_literal(text):
    """Decode one LiveCodeBench literal using JSON first, then Python literals."""
    text = text.strip()
    try:
        return json.loads(text), True
    except (json.JSONDecodeError, TypeError):
        try:
            node = ast.parse(text, mode='eval').body
        except SyntaxError:
            return None, False
        return _json_literal(node)


def _assert_example(source, example_id, provenance):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assert):
        return None
    test = tree.body[0].test
    if (not isinstance(test, ast.Compare) or len(test.ops) != 1
            or not isinstance(test.ops[0], ast.Eq) or len(test.comparators) != 1
            or not isinstance(test.left, ast.Call)):
        return None
    args, kwargs = [], {}
    for arg in test.left.args:
        value, valid = _json_literal(arg)
        if not valid:
            return None
        args.append(value)
    for item in test.left.keywords:
        if item.arg is None:
            return None
        value, valid = _json_literal(item.value)
        if not valid:
            return None
        kwargs[item.arg] = value
    expected, valid = _json_literal(test.comparators[0])
    if not valid:
        return None
    return {
        'id': example_id,
        'call': {'entrypoint': _call_name(test.left.func), 'args': args, 'kwargs': kwargs},
        'expected_return': expected,
        'raw_source': source.strip(),
        'provenance': provenance,
        'structured': True,
    }


def preserved_public_examples(record):
    """Extract literal public I/O pairs; never use references or hidden tests."""
    benchmark = record.get('benchmark')
    examples = []
    if benchmark == 'mbpp':
        for index, source in enumerate(record.get('public_tests', [])):
            example = _assert_example(source, f'public_{index}', 'normalized.public_tests')
            examples.append(example or {
                'id': f'public_{index}', 'raw_source': source.strip(),
                'provenance': 'normalized.public_tests', 'structured': False,
            })
    elif benchmark == 'humaneval':
        try:
            text = example_text(record)
            parsed = doctest.DocTestParser().get_examples(text)
        except (ValueError, SyntaxError):
            return []
        for index, item in enumerate(parsed):
            source = item.source.strip()
            want = item.want.strip()
            if not source:
                continue
            # Reuse the strict assert parser, so calls and expected values obey
            # exactly the same literal-only policy as MBPP.
            example = _assert_example(
                f'assert ({source}) == ({want or "None"})', f'public_{index}',
                'original_prompt_doctest')
            raw = f'>>> {source}' + (f'\n{want}' if want else '')
            if example:
                example['raw_source'] = raw
            examples.append(example or {
                'id': f'public_{index}', 'raw_source': raw,
                'provenance': 'original_prompt_doctest', 'structured': False,
            })
    elif benchmark == 'livecodebench':
        for index, case in enumerate(record.get('public_tests', [])):
            raw_input = case.get('input', '') if isinstance(case, dict) else ''
            raw_output = case.get('output', '') if isinstance(case, dict) else ''
            testtype = case.get('testtype') if isinstance(case, dict) else None
            raw = f'Input:\n{raw_input}\nOutput:\n{raw_output}'
            base = {
                'id': f'public_{index}', 'raw_source': raw,
                'provenance': 'normalized.public_tests',
            }
            if testtype == 'stdin':
                examples.append({
                    **base, 'io_mode': 'stdio', 'stdin': raw_input,
                    'expected_stdout': raw_output, 'structured': True,
                })
                continue
            if testtype == 'functional' and record.get('entrypoint'):
                args, valid = [], True
                for line in raw_input.splitlines():
                    value, parsed = _text_literal(line)
                    if not parsed:
                        valid = False
                        break
                    args.append(value)
                expected, expected_valid = _text_literal(raw_output)
                if valid and expected_valid:
                    examples.append({
                        **base, 'call': {'entrypoint': record['entrypoint'],
                                         'args': args, 'kwargs': {}},
                        'expected_return': expected, 'structured': True,
                    })
                    continue
            examples.append({**base, 'structured': False})
    return examples


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
