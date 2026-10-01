"""Extract public signatures using syntax alone; never execute starter code."""
import ast


def fixed_interface(task):
    """Extract only the named public definition from starter syntax; never execute it."""
    if not task or task.get('interface_mode') != 'function' or not task.get('entrypoint'):
        return None
    starter = task.get('starter_code', '')
    if not starter.strip():
        return None
    try:
        tree = ast.parse(starter)
    except SyntaxError:
        # Common completion prefix ends at a signature with no body yet.
        try:
            tree = ast.parse(starter + '\n' + ('        ' if '.' in task['entrypoint'] else '    ') + 'pass\n')
        except SyntaxError:
            return None
    parts = task['entrypoint'].split('.')
    if len(parts) not in (1, 2):
        return None
    owners = tree.body if len(parts) == 1 else next(
        (n.body for n in tree.body if isinstance(n, ast.ClassDef) and n.name == parts[0]), [])
    original = next((n for n in owners if isinstance(n, ast.FunctionDef) and n.name == parts[-1]), None)
    if original is None:
        return None
    signature = '(' + ast.unparse(original.args) + ')'
    if original.returns:
        signature += ' -> ' + ast.unparse(original.returns)
    interface = dict(mode='function', entrypoint=task['entrypoint'], signature=signature)
    return interface
