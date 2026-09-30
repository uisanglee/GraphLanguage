"""Copied into generated artifacts; type diagnostics run only inside the evaluator.

Unknown annotations are accepted. No annotation is evaluated as Python code.
"""
import ast as _gd_ast
import functools as _gd_functools

_gd_contract_runtime_version = 1
_gd_check_types = False


class GraphIRPortTypeError(TypeError):
    pass


@_gd_functools.lru_cache(maxsize=512)
def _gd_annotation(text):
    try:
        return _gd_ast.parse(text, mode='eval').body
    except (SyntaxError, ValueError):
        return None


def _gd_matches(value, annotation, depth=0):
    if annotation is None or depth > 20:
        return True
    if isinstance(annotation, _gd_ast.Constant):
        return value is None if annotation.value is None else True
    if isinstance(annotation, _gd_ast.BinOp) and isinstance(annotation.op, _gd_ast.BitOr):
        return (_gd_matches(value, annotation.left, depth+1)
                or _gd_matches(value, annotation.right, depth+1))
    name = annotation.id if isinstance(annotation, _gd_ast.Name) else None
    simple = {'str': str, 'bytes': bytes, 'int': int, 'bool': bool, 'float': (int, float),
              'complex': (int, float, complex), 'list': list, 'tuple': tuple, 'dict': dict,
              'set': set, 'frozenset': frozenset}
    if name:
        return isinstance(value, simple[name]) if name in simple else True
    if not isinstance(annotation, _gd_ast.Subscript):
        return True
    base = annotation.value
    name = base.id if isinstance(base, _gd_ast.Name) else None
    if isinstance(base, _gd_ast.Attribute) and isinstance(base.value, _gd_ast.Name) and base.value.id == 'typing':
        name = base.attr
    name = {'List': 'list', 'Tuple': 'tuple', 'Dict': 'dict', 'Set': 'set',
            'FrozenSet': 'frozenset'}.get(name, name)
    parts = annotation.slice.elts if isinstance(annotation.slice, _gd_ast.Tuple) else [annotation.slice]
    if name == 'Optional' and len(parts) == 1:
        return value is None or _gd_matches(value, parts[0], depth+1)
    if name == 'Union':
        return any(_gd_matches(value, part, depth+1) for part in parts)
    if name not in {'list', 'tuple', 'dict', 'set', 'frozenset'}:
        return True
    if not isinstance(value, simple[name]):
        return False
    if name == 'dict' and len(parts) == 2:
        return all(_gd_matches(k, parts[0], depth+1) and _gd_matches(v, parts[1], depth+1)
                   for k, v in value.items())
    if name == 'tuple':
        if len(parts) == 2 and isinstance(parts[1], _gd_ast.Constant) and parts[1].value is Ellipsis:
            return all(_gd_matches(v, parts[0], depth+1) for v in value)
        return len(value) == len(parts) and all(_gd_matches(v, t, depth+1) for v, t in zip(value, parts))
    if len(parts) == 1:
        return all(_gd_matches(v, parts[0], depth+1) for v in value)
    return True


def _gd_assert_ports(node, direction, values):
    if not _gd_check_types:
        return
    for port in node[direction]:
        if port['type'] == 'EffectToken' or port['id'] not in values:
            continue
        value = values[port['id']]
        annotation = port['type']
        if port.get('cardinality') == 'many':
            annotation = 'list[' + annotation + ']'
        if not _gd_matches(value, _gd_annotation(annotation)):
            raise GraphIRPortTypeError(
                f"{node['id']}.{port['id']} ({direction}): expected {annotation}; "
                f"received {type(value).__name__} with incompatible contents")
