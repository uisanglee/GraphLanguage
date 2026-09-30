"""Build evaluation-only tests from original public task data, never generated graphs."""
import doctest
import hashlib


def public_checks(task, benchmark):
    if benchmark == 'mbpp':
        tests = task.get('public_tests', [])
        source = '\n'.join(tests)
        origin = 'normalized.public_tests'
        count = len(tests)
    elif benchmark == 'humaneval':
        prompt = task.get('prompt', {}).get('primary', '')
        try:
            count = len(doctest.DocTestParser().get_examples(prompt))
        except ValueError:
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
