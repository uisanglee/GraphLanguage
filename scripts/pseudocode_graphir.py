"""Compile restricted Python-shaped pseudocode into a compact program graph.

GraphIR is not a second model-authored language. It is a deterministic, compact
projection of the parsed pseudocode AST plus compiler-derived data, control, and
loop/branch state dependencies. This module parses but never executes model output.
"""
from __future__ import annotations

import ast
import copy
import io
import tokenize
from pathlib import Path

from public_interface import fixed_interface
from schema_validation import load_schema, validate_schema
from validate_artifact import strip_fence

VERSION = 'pseudocode-graphir-3.1'
SCHEMA = Path(__file__).resolve().parents[1] / 'schemas/pseudocode-graphir-v3.schema.json'
NODE_KINDS = {'Input', 'Resource', 'Assign', 'Update', 'Call', 'Loop', 'Branch',
              'Assert', 'Return', 'Control'}
TERMINATORS = (ast.Return, ast.Break, ast.Continue, ast.Raise)


def signature(fn):
    return '(' + ast.unparse(fn.args) + ')' + (
        ' -> ' + ast.unparse(fn.returns) if fn.returns else '')


def _docstring_statement(node):
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str))


def definitions(tree):
    result, names = {}, set()
    saw_module_check = False
    for item in tree.body:
        if isinstance(item, ast.Assert):
            if not result:
                raise ValueError('module-level plan checks must follow function definitions')
            saw_module_check = True
            continue
        if isinstance(item, (ast.FunctionDef, ast.ClassDef)):
            if saw_module_check:
                raise ValueError('function definitions must precede module-level plan checks')
            if item.name in names:
                raise ValueError('duplicate top-level definition: ' + item.name)
            names.add(item.name)
        if isinstance(item, ast.FunctionDef):
            entries = [(item.name, item)]
        elif isinstance(item, ast.ClassDef):
            if item.bases or item.keywords or item.decorator_list:
                raise ValueError('only a plain public interface class is supported')
            entries = []
            for method in item.body:
                if isinstance(method, ast.FunctionDef):
                    entries.append((item.name + '.' + method.name, method))
                elif not _docstring_statement(method):
                    raise ValueError('interface classes may contain only methods and docstrings')
        elif _docstring_statement(item):
            continue
        else:
            raise ValueError('pseudocode must contain function definitions, not module execution')
        for name, fn in entries:
            if name in result:
                raise ValueError('duplicate function: ' + name)
            result[name] = fn
    return result


def _params(fn):
    args = fn.args
    return [arg.arg for arg in args.posonlyargs + args.args + args.kwonlyargs] + (
        [args.vararg.arg] if args.vararg else []) + ([args.kwarg.arg] if args.kwarg else [])


def _target_names(node):
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        return set().union(*(_target_names(item) for item in node.elts), set())
    if isinstance(node, ast.Starred):
        return _target_names(node.value)
    if isinstance(node, (ast.Attribute, ast.Subscript)):
        root = node.value
        while isinstance(root, (ast.Attribute, ast.Subscript)):
            root = root.value
        return {root.id} if isinstance(root, ast.Name) else set()
    return set()


class _Reads(ast.NodeVisitor):
    """Read values, excluding a direct callee name such as ``len`` or ``helper``."""
    def __init__(self):
        self.names = set()
        self.bound = [set()]

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and not any(node.id in scope for scope in self.bound):
            self.names.add(node.id)

    def visit_AugAssign(self, node):
        # The Store-context target of ``x += y`` is also a read of the old x.
        self.names.update(_target_names(node.target))
        self.visit(node.value)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute):
            self.visit(node.func.value)
        elif not isinstance(node.func, ast.Name):
            self.visit(node.func)
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Lambda(self, node):
        # Defaults are evaluated outside the lambda scope; its parameters bind
        # names only inside the body.
        for default in list(node.args.defaults) + list(node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        bound = {arg.arg for arg in (node.args.posonlyargs + node.args.args + node.args.kwonlyargs)}
        if node.args.vararg:
            bound.add(node.args.vararg.arg)
        if node.args.kwarg:
            bound.add(node.args.kwarg.arg)
        self.bound.append(bound)
        self.visit(node.body)
        self.bound.pop()

    def _comprehension(self, generators, values):
        self.bound.append(set())
        for generator in generators:
            self.visit(generator.iter)
            self.bound[-1].update(_target_names(generator.target))
            for condition in generator.ifs:
                self.visit(condition)
        for value in values:
            self.visit(value)
        self.bound.pop()

    def visit_ListComp(self, node): self._comprehension(node.generators, [node.elt])
    def visit_SetComp(self, node): self._comprehension(node.generators, [node.elt])
    def visit_GeneratorExp(self, node): self._comprehension(node.generators, [node.elt])
    def visit_DictComp(self, node): self._comprehension(node.generators, [node.key, node.value])


def _reads(node):
    visitor = _Reads()
    visitor.visit(node)
    return visitor.names


def _writes(statement):
    if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        return set().union(*(_target_names(target) for target in targets), set())
    if isinstance(statement, ast.For):
        return _target_names(statement.target) | _block_writes(statement.body) | _block_writes(statement.orelse)
    if isinstance(statement, ast.While):
        return _block_writes(statement.body) | _block_writes(statement.orelse)
    if isinstance(statement, ast.If):
        return _block_writes(statement.body) | _block_writes(statement.orelse)
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
        return _target_names(statement.value.func.value) if isinstance(statement.value.func, ast.Attribute) else set()
    if isinstance(statement, ast.Delete):
        return set().union(*(_target_names(target) for target in statement.targets), set())
    return set()


def _block_writes(statements):
    return set().union(*(_writes(statement) for statement in statements), set())


def _free_reads(statements, initially_defined=()):
    defined, free = set(initially_defined), set()
    for statement in statements:
        free.update(_reads(statement) - defined)
        defined.update(_writes(statement))
    return free


def _falls_through(statements):
    """Conservative structured-control reachability for one statement block."""
    for statement in statements:
        if isinstance(statement, TERMINATORS):
            return False
        if isinstance(statement, ast.If):
            if statement.orelse and not _falls_through(statement.body) and not _falls_through(statement.orelse):
                return False
    return True


def _external_roots(fn):
    """Names used as namespace roots (math.inf, torch.tensor), never bare typos."""
    roots = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute):
            root = node.value
            while isinstance(root, (ast.Attribute, ast.Subscript)):
                root = root.value
            if isinstance(root, ast.Name) and isinstance(root.ctx, ast.Load):
                roots.add(root.id)
    return roots


def _comment_attachments(source, fn):
    """Attach comments to the nearest statement at the same indentation."""
    statements = [node for node in ast.walk(fn)
                  if isinstance(node, ast.stmt) and node is not fn and not _docstring_statement(node)]
    attached = {}
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for token in tokens:
        if token.type != tokenize.COMMENT or not (fn.lineno <= token.start[0] <= fn.end_lineno):
            continue
        row, column = token.start
        inline = [node for node in statements if node.lineno == row and node.col_offset <= column]
        if inline:
            target = max(inline, key=lambda node: node.col_offset)
        else:
            following = [node for node in statements
                         if node.lineno > row and node.col_offset == column]
            if not following:
                continue
            target = min(following, key=lambda node: node.lineno)
        attached.setdefault((target.lineno, target.col_offset), []).append(
            token.string.lstrip('#').strip())
    return attached


def _node_kind(statement):
    if isinstance(statement, ast.Return): return 'Return'
    if isinstance(statement, ast.If): return 'Branch'
    if isinstance(statement, (ast.For, ast.While)): return 'Loop'
    if isinstance(statement, ast.Assert): return 'Assert'
    if isinstance(statement, (ast.Break, ast.Continue, ast.Raise, ast.Delete, ast.Pass)): return 'Control'
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call): return 'Call'
    if isinstance(statement, (ast.Assign, ast.AnnAssign)):
        return 'Call' if isinstance(statement.value, ast.Call) else 'Assign'
    if isinstance(statement, ast.AugAssign): return 'Update'
    raise ValueError(type(statement).__name__ + ' statement is outside the pseudocode profile')


def _calls(node, definitions, owner, locals_):
    result = []
    for call in sorted((item for item in ast.walk(node) if isinstance(item, ast.Call)),
                       key=lambda item: (item.lineno, item.col_offset)):
        target = ast.unparse(call.func)
        resolved = target
        if owner and target.startswith(('self.', 'cls.')):
            resolved = owner + '.' + target.split('.', 1)[1]
        if resolved in locals_:
            resolved = None
        result.append({'target': target, 'definition': definitions.get(resolved),
                       'arguments': [ast.unparse(arg) for arg in call.args],
                       'keywords': {kw.arg or '**': ast.unparse(kw.value) for kw in call.keywords}})
    return result


def _assignment_parts(statement):
    targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
    return [ast.unparse(target) for target in targets], ast.unparse(statement.value)


class _BodyCompiler:
    def __init__(self, definitions, owner, function_locals, comments=None):
        self.definitions = definitions
        self.owner = owner
        self.function_locals = function_locals
        self.comments = comments or {}
        self.counter = 0

    def fresh(self, kind):
        value = f'{kind.lower()}_{self.counter}'
        self.counter += 1
        return value

    @staticmethod
    def edge(kind, source, target):
        return {'kind': kind, 'from': source, 'to': target}

    def compile(self, statements, incoming, required_outputs=(), *, in_loop=False,
                require_outputs_on_termination=False):
        env = dict(incoming)
        nodes, edges = [], []
        previous = ['$entry', '$control']
        falls_through = True
        for statement in statements:
            if _docstring_statement(statement):
                continue
            if not falls_through:
                raise ValueError(f'unreachable statement after terminating control flow: {ast.unparse(statement)}')
            if isinstance(statement, (ast.Break, ast.Continue)) and not in_loop:
                raise ValueError(type(statement).__name__ + ' outside loop')

            kind = _node_kind(statement)
            node_id = self.fresh(kind)
            reads, writes = sorted(_reads(statement)), sorted(_writes(statement))
            references = {name: self.definitions[name] for name in reads
                          if name in self.definitions and name not in self.function_locals}
            inputs, unresolved = {}, []
            for name in reads:
                if name in references:
                    continue
                if name in env:
                    inputs[name] = 'Any'
                    edges.append(self.edge('data', env[name], [node_id, name]))
                else:
                    unresolved.append(name)
            outputs = {name: 'Any' for name in writes}
            config = {'ast_kind': type(statement).__name__}
            comments = self.comments.get((statement.lineno, statement.col_offset))
            if comments:
                config['comments'] = comments
            call_scope = (statement.test if isinstance(statement, (ast.If, ast.While))
                          else statement.iter if isinstance(statement, ast.For) else statement)
            calls = _calls(call_scope, self.definitions, self.owner, self.function_locals)
            if calls:
                config['calls'] = calls
            if references:
                config['function_references'] = references

            node = {'id': node_id, 'kind': kind, 'inputs': inputs,
                    'outputs': outputs, 'config': config}
            edges.append(self.edge('control', previous, [node_id, '$control']))

            if kind == 'Return':
                config['value'] = ast.unparse(statement.value) if statement.value is not None else None
                node['outputs'] = {'result': 'Any'}
            elif kind in {'Assign', 'Call'} and isinstance(statement, (ast.Assign, ast.AnnAssign)):
                targets, value = _assignment_parts(statement)
                config.update(targets=targets, value=value)
            elif kind == 'Call':
                config['value'] = ast.unparse(statement.value)
                if not outputs:
                    node['outputs'] = {'effect': 'Effect'}
            elif kind == 'Update':
                config.update(target=ast.unparse(statement.target), operator=type(statement.op).__name__,
                              value=ast.unparse(statement.value))
            elif kind == 'Assert':
                config.update(condition=ast.unparse(statement.test),
                              message=ast.unparse(statement.msg) if statement.msg else None)
            elif kind == 'Control':
                config['operation'] = type(statement).__name__
                if isinstance(statement, ast.Raise):
                    config['value'] = ast.unparse(statement.exc) if statement.exc else None
                elif isinstance(statement, ast.Delete):
                    config['targets'] = [ast.unparse(x) for x in statement.targets]
            elif kind == 'Branch':
                condition_reads = _reads(statement.test)
                then_writes, else_writes = _block_writes(statement.body), _block_writes(statement.orelse)
                live_writes = []
                if _falls_through(statement.body):
                    live_writes.append(then_writes)
                if _falls_through(statement.orelse):
                    live_writes.append(else_writes)
                newly_defined = set.intersection(*live_writes) if live_writes else set()
                merged = ((then_writes | else_writes) & set(env)) | newly_defined
                branch_inputs = sorted(condition_reads | _free_reads(statement.body)
                                       | _free_reads(statement.orelse) | (merged & set(env)))
                node['inputs'], node['outputs'] = {}, {name: 'Any' for name in sorted(merged)}
                edges = [e for e in edges if not (e['to'][0] == node_id and e['kind'] == 'data')]
                unresolved = []
                for name in branch_inputs:
                    if name in env:
                        node['inputs'][name] = 'Any'
                        edges.append(self.edge('data', env[name], [node_id, name]))
                    elif name not in references:
                        unresolved.append(name)
                config['condition'] = ast.unparse(statement.test)
                branches = []
                for label, block in [('then', statement.body), ('else', statement.orelse)]:
                    branch_env = {name: ['$input', name] for name in branch_inputs if name in env}
                    region = self.compile(block, branch_env, sorted(merged), in_loop=in_loop)
                    branches.append({'label': label, 'when': config['condition'] if label == 'then' else None,
                                     'body': region})
                node['branches'] = branches
                falls_through = any(branch['body']['falls_through'] for branch in branches)
            elif kind == 'Loop':
                if isinstance(statement, ast.For):
                    mode, expression = 'for', ast.unparse(statement.iter)
                    targets, header_reads = sorted(_target_names(statement.target)), _reads(statement.iter)
                else:
                    mode, expression, targets = 'while', ast.unparse(statement.test), []
                    header_reads = _reads(statement.test)
                carried = sorted(_writes(statement) & set(env))
                loop_inputs = sorted(header_reads | _free_reads(statement.body, targets) | set(carried))
                node['inputs'], node['outputs'] = {}, {name: 'Any' for name in carried}
                edges = [e for e in edges if not (e['to'][0] == node_id and e['kind'] == 'data')]
                unresolved = []
                for name in loop_inputs:
                    if name in env:
                        node['inputs'][name] = 'Any'
                        edges.append(self.edge('data', env[name], [node_id, name]))
                    elif name not in targets and name not in references:
                        unresolved.append(name)
                config.update(mode=mode, expression=expression, targets=targets, carried=carried)
                body_inputs = {name: ['$input', name] for name in loop_inputs if name in env}
                for target in targets:
                    body_inputs[target] = ['$input', target]
                node['body'] = self.compile(statement.body, body_inputs, carried, in_loop=True,
                                            require_outputs_on_termination=True)
                if statement.orelse:
                    else_env = {name: ['$input', name] for name in loop_inputs if name in env}
                    node['else_body'] = self.compile(statement.orelse, else_env, carried, in_loop=in_loop)

            if unresolved:
                raise ValueError(f'{node_id}: undefined values: {", ".join(sorted(set(unresolved)))}')
            nodes.append(node)
            for name in node['outputs']:
                if kind not in {'Return', 'Control'} and name != 'effect':
                    env[name] = [node_id, name]
            if kind in {'Return', 'Control'} and isinstance(statement, TERMINATORS):
                previous = [node_id, '$control']
                falls_through = False
            elif kind != 'Branch' or falls_through:
                previous = [node_id, '$control']

        if falls_through or require_outputs_on_termination:
            edges.append(self.edge('control', previous, ['$exit', '$control']))
        outputs = {}
        if falls_through or require_outputs_on_termination:
            for name in required_outputs:
                if name not in env:
                    raise ValueError('region does not define required output: ' + name)
                outputs[name] = 'Any'
                edges.append(self.edge('state' if name in incoming else 'data',
                                       env[name], ['$output', name]))
        return {'inputs': {name: 'Any' for name in incoming}, 'outputs': outputs,
                'nodes': nodes, 'edges': edges, 'falls_through': falls_through}


def _validate_profile(funcs):
    unsupported = (ast.AsyncFunctionDef, ast.AsyncFor, ast.Await, ast.Yield, ast.YieldFrom,
                   ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal,
                   ast.Try, ast.With, ast.Match)
    for fn in funcs.values():
        if fn.decorator_list:
            raise ValueError('decorators are outside the pseudocode profile')
        for statement in ast.walk(fn):
            if isinstance(statement, ast.Constant) and statement.value is Ellipsis:
                raise ValueError('ellipsis placeholders are not algorithmic plans')
            if isinstance(statement, unsupported):
                raise ValueError(type(statement).__name__ + ' is outside the pseudocode profile')
            if isinstance(statement, (ast.FunctionDef, ast.ClassDef)) and statement is not fn:
                raise ValueError('declare helper functions at module level')
        if not any(isinstance(item, ast.Return) for item in ast.walk(fn)):
            raise ValueError(fn.name + ': explicit return required')


def compile_pseudocode(raw, task):
    source = strip_fence(raw)
    if not source:
        raise ValueError('empty pseudocode')
    tree = ast.parse(source, filename='<pseudocode>')
    compile(tree, '<pseudocode>', 'exec')
    funcs = definitions(tree)
    if not funcs:
        raise ValueError('no pseudocode functions')
    _validate_profile(funcs)

    mode = task['interface_mode']
    if mode not in {'function', 'stdio'}:
        raise ValueError('pseudocode profile supports function and stdio tasks only')
    entry = task.get('entrypoint') if mode == 'function' else 'solve'
    if not entry or entry not in funcs:
        raise ValueError('missing entrypoint: ' + str(entry))
    expected = fixed_interface(task) if mode == 'function' else {
        'mode': 'stdio', 'entrypoint': 'solve', 'signature': '(stdin: str) -> str'}
    if expected:
        original = ast.parse('def _entry' + expected['signature'] + ':\n    pass').body[0]
        def api(args):
            args = copy.deepcopy(args)
            for arg in ast.walk(args):
                if isinstance(arg, ast.arg):
                    arg.annotation = None
                    arg.type_comment = None
            return ast.dump(args, include_attributes=False)
        if api(funcs[entry].args) != api(original.args):
            raise ValueError('entrypoint parameters/defaults differ from the fixed public signature')
        entry_signature = expected['signature']
    else:
        entry_signature = signature(funcs[entry])

    ids = {name: f'function_{index}' for index, name in enumerate(funcs)}
    functions = []
    for name, fn in funcs.items():
        parameters = _params(fn)
        function_locals = set(parameters) | _block_writes(fn.body)
        compiler = _BodyCompiler(ids, name.rsplit('.', 1)[0] if '.' in name else None,
                                 function_locals, _comment_attachments(source, fn))
        incoming, input_nodes = {}, []
        for index, parameter in enumerate(parameters):
            node_id = f'input_{index}'
            input_nodes.append({'id': node_id, 'kind': 'Input', 'inputs': {},
                                'outputs': {parameter: 'Any'}, 'config': {'parameter': parameter}})
            incoming[parameter] = [node_id, parameter]

        free = _free_reads(fn.body, parameters) - set(ids) - set(function_locals)
        external_names = sorted(free & _external_roots(fn))
        undefined = sorted(free - set(external_names))
        if undefined:
            raise ValueError(name + ': undefined values: ' + ', '.join(undefined))
        for external in external_names:
            node_id = f'resource_{len(input_nodes)}'
            input_nodes.append({'id': node_id, 'kind': 'Resource', 'inputs': {},
                                'outputs': {external: 'Any'}, 'config': {'symbol': external}})
            incoming[external] = [node_id, external]

        body = compiler.compile(fn.body, incoming)
        body['nodes'] = input_nodes + body['nodes']
        body['inputs'] = {parameter: 'Any' for parameter in parameters}
        functions.append({'id': ids[name], 'name': name,
                          'signature': entry_signature if name == entry else signature(fn),
                          'description': ast.get_docstring(fn, clean=False) or '', 'body': body})

    ranges = [(fn.lineno, fn.end_lineno) for fn in funcs.values()]
    graph = {'graphir_version': VERSION, 'representation': 'compact-pseudocode-ast-graph',
             'interface': {'mode': mode, 'entrypoint': entry, 'signature': entry_signature},
             'entry_function': ids[entry], 'functions': functions,
             'module_description': ast.get_docstring(tree, clean=False) or '',
             'class_descriptions': {n.name: ast.get_docstring(n, clean=False) or ''
                                    for n in tree.body if isinstance(n, ast.ClassDef)},
             'notes': [token.string for token in tokenize.generate_tokens(io.StringIO(source).readline)
                       if token.type == tokenize.COMMENT
                       and not any(start <= token.start[0] <= end for start, end in ranges)]}
    schema_errors = validate_schema(graph, load_schema(SCHEMA))
    if schema_errors:
        raise ValueError('; '.join(schema_errors))
    validate_graph(graph)
    return graph


def validate_graph(graph):
    if graph.get('graphir_version') != VERSION:
        raise ValueError('unsupported pseudocode GraphIR version')
    functions = {fn['id']: fn for fn in graph.get('functions', [])}
    if len(functions) != len(graph.get('functions', [])) or graph.get('entry_function') not in functions:
        raise ValueError('invalid function IDs or entry function')

    def body(value):
        nodes = {node['id']: node for node in value.get('nodes', [])}
        if len(nodes) != len(value.get('nodes', [])):
            raise ValueError('duplicate node ID in body')
        endpoints = {'$input': set(value.get('inputs', {})), '$output': set(value.get('outputs', {})),
                     '$entry': {'$control'}, '$exit': {'$control'}}
        for node_id, node in nodes.items():
            if node.get('kind') not in NODE_KINDS:
                raise ValueError('unknown node kind')
            nested = set(node) & {'body', 'else_body', 'branches'}
            if node['kind'] == 'Loop' and 'body' not in nested:
                raise ValueError('Loop requires a body')
            if node['kind'] == 'Branch' and nested != {'branches'}:
                raise ValueError('Branch owns branches only')
            if node['kind'] not in {'Loop', 'Branch'} and nested:
                raise ValueError('only Loop/Branch may own regions')
            for definition in node.get('config', {}).get('function_references', {}).values():
                if definition not in functions:
                    raise ValueError('missing referenced function')
            for call in node.get('config', {}).get('calls', []):
                if call.get('definition') is not None and call['definition'] not in functions:
                    raise ValueError('missing call definition')
            endpoints[node_id] = set(node.get('outputs', {})) | {'$control'}
            if node['kind'] == 'Loop':
                body(node['body'])
                if set(node['body']['outputs']) != set(node['outputs']):
                    raise ValueError('Loop body must expose all carried outputs')
                if 'else_body' in node:
                    body(node['else_body'])
            if node['kind'] == 'Branch':
                if len(node.get('branches', [])) != 2 or node['branches'][-1].get('when') is not None:
                    raise ValueError('Branch requires then and final else regions')
                for branch in node['branches']:
                    body(branch['body'])
                    if branch['body']['falls_through'] and set(branch['body']['outputs']) != set(node['outputs']):
                        raise ValueError('fallthrough Branch region must expose all merged outputs')
        driven = set()
        for edge in value.get('edges', []):
            if set(edge) != {'kind', 'from', 'to'} or edge['kind'] not in {'data', 'control', 'state'}:
                raise ValueError('invalid edge')
            src, port = edge['from']
            dst, target = edge['to']
            if edge['kind'] == 'control' and (port != '$control' or target != '$control'):
                raise ValueError('control edge must connect control ports')
            if edge['kind'] != 'control' and (port == '$control' or target == '$control'):
                raise ValueError('value edge cannot connect control ports')
            if edge['kind'] == 'state' and dst != '$output':
                raise ValueError('state edge must expose a region output')
            if src not in endpoints or port not in endpoints[src]:
                raise ValueError('missing source port')
            if dst == '$output':
                valid = target in value.get('outputs', {})
            elif dst == '$exit':
                valid = target == '$control'
            else:
                valid = dst in nodes and (target == '$control' or target in nodes[dst].get('inputs', {}))
            if not valid:
                raise ValueError('missing destination port')
            key = (dst, target)
            if key in driven:
                raise ValueError('multiple sources for one input port')
            driven.add(key)
        for node in nodes.values():
            required = set(node.get('inputs', {}))
            if node['kind'] not in {'Input', 'Resource'}:
                required.add('$control')
            for port in required:
                if (node['id'], port) not in driven:
                    raise ValueError('unconnected node input')
        if value.get('falls_through') and ('$exit', '$control') not in driven:
            raise ValueError('fallthrough body lacks control exit')
        for port in value.get('outputs', {}):
            if ('$output', port) not in driven:
                raise ValueError('unconnected region output')

    for fn in functions.values():
        body(fn['body'])


def artifact_errors(code, task):
    """Syntax and public-interface gate. Correctness is measured only in sandbox."""
    from validate_artifact import validate_artifact
    errors = validate_artifact(code, task['interface_mode'])
    if errors or task['interface_mode'] != 'function':
        return errors
    source = strip_fence(code)
    tree = ast.parse(source)
    name = task.get('entrypoint', '')
    parts = name.split('.')
    owners = tree.body
    if len(parts) == 2:
        owners = next((n.body for n in owners if isinstance(n, ast.ClassDef) and n.name == parts[0]), [])
    fn = next((n for n in owners if isinstance(n, ast.FunctionDef) and n.name == parts[-1]), None)
    if fn is None:
        return ['missing public callable ' + name]
    expected = fixed_interface(task)
    if expected:
        original = ast.parse('def _entry' + expected['signature'] + ':\n    pass').body[0]
        a, b = copy.deepcopy(fn.args), copy.deepcopy(original.args)
        for arg in list(ast.walk(a)) + list(ast.walk(b)):
            if isinstance(arg, ast.arg):
                arg.annotation = None
                arg.type_comment = None
        if ast.dump(a) != ast.dump(b):
            errors.append('generated callable changed public parameters/defaults')
    return errors


def main():
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pseudocode', type=Path, required=True)
    parser.add_argument('--task', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    graph = compile_pseudocode(args.pseudocode.read_text(), json.loads(args.task.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(graph, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
