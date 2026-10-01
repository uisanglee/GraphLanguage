"""Compile ordered value contracts to editable Core graphs without LLM wiring.

Names identify values, not Python expressions. Each scope is single assignment.
No generated code or annotation is evaluated by this compiler.
"""
import ast
import copy
import keyword
from pathlib import Path

from graphir_core import normalize_type
from schema_validation import load_schema, validate_schema
from validate_graph import validate

SCHEMA = Path(__file__).resolve().parents[1] / 'schemas/graphir-contracts.schema.json'
COMPILER_VERSION = 'contracts-5-positional-shared-records'


def identifier(name):
    if not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name) or name.startswith('__'):
        raise ValueError(f'invalid value name: {name!r}')
    return name


def signature_info(interface):
    if interface['mode'] == 'stdio':
        if interface['entrypoint'] is not None or interface['signature'] is not None:
            raise ValueError('stdio requires null entrypoint and signature')
        return {'stdin': 'str'}, 'str'
    entry = interface['entrypoint'] or ''
    parts = entry.split('.')
    if len(parts) not in (1, 2):
        raise ValueError('invalid function entrypoint')
    for part in parts:
        identifier(part)
    signature = interface['signature'] or ''
    try:
        tree = ast.parse(f'def {parts[-1]}{signature}:\n    pass')
    except SyntaxError as error:
        raise ValueError('invalid public signature') from error
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise ValueError('expected one function signature')
    fn = tree.body[0]
    if len(fn.body) != 1 or not isinstance(fn.body[0], ast.Pass):
        raise ValueError('signature may not contain a body')
    params = {}
    public_args = fn.args.posonlyargs + fn.args.args
    if len(parts) == 2 and public_args and public_args[0].arg in {'self', 'cls'}:
        public_args = public_args[1:]
    for arg in public_args + fn.args.kwonlyargs:
        identifier(arg.arg)
        params[arg.arg] = normalize_type(ast.unparse(arg.annotation)) if arg.annotation else 'Any'
    if fn.args.vararg:
        arg = fn.args.vararg
        params[identifier(arg.arg)] = 'tuple[' + (normalize_type(ast.unparse(arg.annotation)) if arg.annotation else 'Any') + ', ...]'
    if fn.args.kwarg:
        arg = fn.args.kwarg
        params[identifier(arg.arg)] = 'dict[str, ' + (normalize_type(ast.unparse(arg.annotation)) if arg.annotation else 'Any') + ']'
    return params, normalize_type(ast.unparse(fn.returns)) if fn.returns else 'Any'


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
    signature_info(interface)
    return interface


def prepare_contract_task(task):
    task = dict(task)
    interface = fixed_interface(task)
    if interface:
        task['fixed_interface'] = interface
        task['available_inputs'] = signature_info(interface)[0]
    return task


def contract_schema(schema, task):
    """The model cannot emit an interface when the public starter supplies one."""
    schema = copy.deepcopy(schema)
    if fixed_interface(task):
        schema['required'] = [key for key in schema['required'] if key != 'interface']
        schema['properties'].pop('interface', None)
        if 'ref' in schema.get('$defs', {}):
            params = signature_info(fixed_interface(task))[0]
            schema['$defs']['ref']['anyOf'][0] = {'enum': ['input:' + name for name in params]}
    return schema


def compile_contracts(document, task=None):
    authoritative = fixed_interface(task)
    if authoritative and isinstance(document, dict):
        document = dict(document, interface=authoritative)
    shapes = {}
    if document.get('contract_version') == '2.0':
        from graphir_contracts_v2 import lower_contracts_v2
        document, shapes = lower_contracts_v2(document, signature_info(document['interface'])[0])
    errors = validate_schema(document, load_schema(SCHEMA))
    if errors:
        raise ValueError('; '.join(errors))
    interface = dict(document['interface'])
    params, return_type = signature_info(interface)
    if task:
        if task.get('interface_mode') and interface['mode'] != task['interface_mode']:
            raise ValueError('public interface mode differs from task')
        if task.get('entrypoint') and interface['entrypoint'] != task['entrypoint']:
            raise ValueError('public entrypoint differs from task')

    counter = 0
    reserved = set(params)
    def fresh():
        nonlocal counter
        while True:
            name = f'node_{counter}'
            counter += 1
            if name not in reserved:
                reserved.add(name)
                return name

    def compatible(a, b):
        return a == b or 'Any' in (a, b)

    def scope(program, external, root=False, output_name='value', expected='Any'):
        # external: value name -> (type, producer endpoint).
        env = dict(external)
        local = set()
        captures = {}
        nodes, edges = [], []
        def use(name):
            identifier(name)
            if name not in env:
                raise ValueError(f'unknown or forward value reference: {name}')
            typ, endpoint = env[name]
            if not root and name not in local:
                captures[name] = typ
                endpoint = '$input.' + name
            return typ, endpoint
        for step in program['steps']:
            nid = fresh()
            produced = step['produces']
            for name in produced:
                identifier(name)
                if name in env:
                    raise ValueError(f'value already defined: {name}; use a new name for updated values')
            outputs = {name: normalize_type(typ) for name, typ in produced.items()}
            if not outputs:
                raise ValueError('a contract must produce a named result')
            node = dict(id=nid, kind=step['kind'], description=step['description'], inputs={}, outputs=outputs)
            if step['kind'] == 'Compute':
                if 'needs' not in step or any(k in step for k in ('condition', 'then', 'else')):
                    raise ValueError('Compute requires needs and cannot contain branches')
                needs = step['needs']
                if len(needs) != len(set(needs)):
                    raise ValueError('duplicate value in needs')
                for name in needs:
                    typ, endpoint = use(name)
                    node['inputs'][name] = typ
                    edges.append({'from': endpoint, 'to': nid + '.' + name})
            else:
                if len(outputs) != 1 or 'needs' in step or not all(k in step for k in ('condition', 'then', 'else')):
                    raise ValueError('Branch requires condition, then, else and one result; captures are inferred')
                condition = step['condition']
                typ, _ = use(condition)
                if typ != 'bool':
                    raise ValueError('Branch condition must be a bool value')
                result_name, result_type = next(iter(outputs.items()))
                node['branches'] = []
                capture_names = {condition}
                for key, when in [('then', condition), ('else', None)]:
                    body = scope(step[key], env, output_name=result_name, expected=result_type)
                    capture_names.update(body['inputs'])
                    node['branches'].append({'when': when, 'body': body})
                for name in sorted(capture_names):
                    typ, endpoint = use(name)
                    node['inputs'][name] = typ
                    edges.append({'from': endpoint, 'to': nid + '.' + name})
            nodes.append(node)
            if shapes:
                node['metadata'] = {'value_contracts': {
                    'inputs': {k: copy.deepcopy(shapes[k]) for k in node['inputs'] if k in shapes},
                    'outputs': {k: copy.deepcopy(shapes[k]) for k in node['outputs'] if k in shapes},
                }}
            for name, typ in outputs.items():
                env[name] = (typ, nid + '.' + name)
                local.add(name)
        typ, endpoint = use(program['return'])
        if not compatible(typ, expected):
            raise ValueError(f'return type mismatch: {typ} -> {expected}')
        if root:
            output_id = fresh()
            nodes.append(dict(id=output_id, kind='Output', description='Return the selected final value.', inputs={'value': typ}, outputs={}))
            edges.append({'from': endpoint, 'to': output_id + '.value'})
        else:
            edges.append({'from': endpoint, 'to': '$output.' + output_name})
        return dict(inputs=captures, outputs={output_name: expected}, nodes=nodes, edges=edges)

    env = {name: (typ, name + '.value') for name, typ in params.items()}
    compiled = scope(document, env, root=True, expected=return_type)
    inputs = [dict(id=name, kind='Input', description=f'Public input {name}.', inputs={}, outputs={'value': typ}) for name, typ in params.items()]
    graph_examples = []
    preserved = copy.deepcopy((task or {}).get('preserved_public_examples', []))
    parameter_names = list(params)
    for example in preserved:
        if interface['mode'] == 'stdio' and example.get('io_mode') == 'stdio':
            graph_examples.append({
                'inputs': {'stdin': example.get('stdin', '')},
                'outputs': {'stdout': example.get('expected_stdout', '')},
                'description': example.get('raw_source', example.get('id', 'public example')),
            })
            continue
        call = example.get('call', {})
        if not example.get('structured') or 'expected_return' not in example:
            continue
        called = (call.get('entrypoint') or '').split('.')[-1]
        if called != (interface.get('entrypoint') or '').split('.')[-1]:
            continue
        args, kwargs = call.get('args', []), call.get('kwargs', {})
        if len(args) > len(parameter_names) or any(name not in params for name in kwargs):
            continue
        bound = dict(zip(parameter_names, args))
        if set(bound) & set(kwargs):
            continue
        bound.update(kwargs)
        graph_examples.append({
            'inputs': bound,
            'outputs': {'return': example.get('expected_return')},
            'description': example.get('raw_source', example.get('id', 'public example')),
        })
    graph = dict(
        graphir_version='0.2.0', interface=interface,
        nodes=inputs + compiled['nodes'], edges=compiled['edges'],
        **({'examples': graph_examples} if graph_examples else {}),
        **({'metadata': {'preserved_public_examples': preserved}} if preserved else {}),
    )
    errors = validate(graph)
    if errors:
        raise ValueError('; '.join(errors))
    return graph
