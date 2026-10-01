"""GraphDSL node ABI, structural checks, and deterministic Python wiring.

Only node bodies are synthesized. The compiler never asks an LLM to assemble code.
"""
from __future__ import annotations

import ast
import builtins
import hashlib
import json
import re
import symtable
from pathlib import Path
from typing import Any

BOUNDARIES = {"Input", "Output", "Literal", "RegionInput", "RegionOutput"}
CONTROL = {"Loop", "Branch", "Try", "Context"}


def node_demonstrations(node, count, dialect=None):
    if count <= 0: return [], []
    bank = json.loads((Path(__file__).resolve().parents[1] / 'demonstrations/node_catalog.json').read_text())
    query = set(re.findall(r'\w+', node['description'].lower()))
    candidates = (d for d in bank if d['kind'] == node['kind'])
    if node['kind'] in CONTROL:
        candidates = (d for d in candidates if d.get('dialect', 'legacy-0.1') == dialect)
    ranked = sorted(candidates,
                    key=lambda d: (-len(query & set(re.findall(r'\w+',json.dumps(d['request']).lower()))),d['id']))[:count]
    messages = []
    for demo in ranked:
        messages.extend([{'role':'user','content':json.dumps(demo['request'])},
                         {'role':'assistant','content':demo['code']}])
    return messages, [d['id'] for d in ranked]


def symbol(node_id: str) -> str:
    return "_gd_n_" + hashlib.sha256(node_id.encode()).hexdigest()[:16]


def region_order(graph: dict, region: str) -> list[dict]:
    nodes = {n["id"]: n for n in graph["nodes"] if n["region"] == region}
    dependencies = {key: set() for key in nodes}
    for edge in graph["edges"]:
        a, b = edge["from"]["node"], edge["to"]["node"]
        if a in nodes and b in nodes:
            dependencies[b].add(a)
    ordered = []
    while dependencies:
        ready = sorted(key for key, values in dependencies.items() if not values)
        if not ready:
            raise ValueError(f"region {region}: cyclic edges; use Loop.carried")
        for key in ready:
            ordered.append(nodes[key])
            del dependencies[key]
        for values in dependencies.values():
            values.difference_update(ready)
    return ordered


def validate_structure(graph: dict) -> list[str]:
    from graphir_core import CanonicalCore
    errors = []
    core = isinstance(graph, CanonicalCore)
    nodes = {n["id"]: n for n in graph["nodes"]}
    regions = {r["id"]: r for r in graph["regions"]}
    if regions.get("root", {}).get("owner") is not None:
        errors.append("root region must not have an owner")
    for region in regions:
        try:
            region_order(graph, region)
        except ValueError as error:
            errors.append(str(error))
        visited = set()
        current = region
        while current != "root" and current in regions:
            if current in visited:
                errors.append(f"region ownership cycle at {region}")
                break
            visited.add(current)
            owner = nodes.get(regions[current].get("owner"), {})
            current = owner.get("region")
    for node in nodes.values():
        config = node["config"]
        nid, kind = node["id"], node["kind"]
        owned = {r["id"] for r in regions.values() if r.get("owner") == nid}
        inputs = {p["id"] for p in node["inputs"]}
        outputs = {p["id"] for p in node["outputs"]}
        def boundary(endpoint, direction, region):
            for other in nodes.values():
                for port in other[direction]:
                    if endpoint == other["id"] + "." + port["id"]:
                        required_kind = "RegionInput" if direction == "outputs" else "RegionOutput"
                        return other["region"] == region and other["kind"] == required_kind
            return False
        if kind == "Loop":
            body = config.get("body_region")
            mode = config.get("mode")
            if body not in owned or mode not in {"for_each", "while", "range", "async_for"}:
                errors.append(f"{nid}: Loop requires valid mode and owned body_region")
            iteration = config.get("iteration", {})
            if mode in {"for_each", "range", "async_for"} and not boundary(
                iteration.get("item_boundary"), "outputs", body
            ):
                errors.append(f"{nid}: missing/invalid iteration.item_boundary")
            if mode == "for_each" and config.get("iterable_port", "iterable") not in inputs:
                errors.append(f"{nid}: missing iterable input")
            if mode == "while" and not config.get("termination"):
                errors.append(f"{nid}: while requires termination contract")
            carried = config.get("carried")
            if not isinstance(carried, list):
                errors.append(f"{nid}: carried must be an explicit array")
                carried = []
            for item in carried:
                if not isinstance(item, dict) or not (
                    item.get("initial_port") in inputs and item.get("final_port") in outputs
                    and boundary(item.get("input_boundary"), "outputs", body)
                    and boundary(item.get("output_boundary"), "inputs", body)
                ):
                    errors.append(f"{nid}: invalid carried binding")
            expected_boundaries = {n['id'] + '.' + p['id'] for n in nodes.values()
                                   if n['region'] == body and n['kind'] == 'RegionInput' for p in n['outputs']}
            provided = {item.get('input_boundary') for item in carried if isinstance(item,dict)}
            if mode in {'for_each','range','async_for'}:
                provided.add(iteration.get('item_boundary'))
            provided.update(config.get('bindings',{}))
            if expected_boundaries != provided:
                errors.append(f'{nid}: every body RegionInput needs an iteration/carried/invariant binding')
            for endpoint, port in config.get('bindings',{}).items():
                if port not in inputs or not boundary(endpoint,'outputs',body):
                    errors.append(f'{nid}: invalid invariant binding')
        if kind == "Branch":
            branches = config.get("branches")
            if not isinstance(branches, list) or not branches:
                errors.append(f"{nid}: Branch requires branches")
            else:
                for branch in branches:
                    if not isinstance(branch, dict) or branch.get("region") not in owned:
                        errors.append(f"{nid}: invalid branch region")
                    elif branch.get("condition_port") is not None and branch["condition_port"] not in inputs:
                        errors.append(f"{nid}: invalid condition_port")
        if kind in {"Try", "Context"} and not core and config.get("body_region") not in owned:
            errors.append(f"{nid}: requires owned body_region")
        if kind == 'Branch' or (kind in {'Try','Context'} and not core):
            mappings = config.get('region_bindings',{})
            for rid in owned:
                mapping = mappings.get(rid,{})
                expected = {n['id'] + '.' + p['id'] for n in nodes.values()
                            if n['region'] == rid and n['kind'] == 'RegionInput' for p in n['outputs']}
                if set(mapping.get('inputs',{})) != expected:
                    errors.append(f'{nid}: missing region input bindings for {rid}')
                for endpoint, port in mapping.get('inputs',{}).items():
                    if port not in inputs:
                        errors.append(f'{nid}: invalid input port binding {port}')
                for port, endpoint in mapping.get('outputs',{}).items():
                    if port not in outputs or not boundary(endpoint,'inputs',rid):
                        errors.append(f'{nid}: invalid output binding for {rid}')
                if kind == 'Branch' and set(mapping.get('outputs',{})) != outputs:
                    errors.append(f'{nid}: all branches must produce the owner output ports')
        if kind in {'RegionInput','RegionOutput'} and node['region'] == 'root':
            errors.append(f'{nid}: region boundary cannot be in root')
        if kind == 'Input' and graph['interface']['mode'] != 'repository_patch' and config.get('mode') != 'stdin' and not isinstance(config.get('parameter'),str):
            errors.append(f'{nid}: Input needs parameter or mode=stdin')
        if kind == 'Literal' and 'value' not in config:
            errors.append(f'{nid}: Literal requires value')
        if kind == 'Output' and config.get('mode') not in {'return','stdout','patch'}:
            errors.append(f'{nid}: Output requires mode')
        if kind == "Call" and not config.get("callable"):
            errors.append(f"{nid}: Call requires callable contract")
    return errors


def node_request(graph: dict, node: dict) -> dict:
    """Expose local contracts plus immutable public evidence when applicable."""
    by_id = {n["id"]: n for n in graph["nodes"]}
    incoming = []
    for edge in graph["edges"]:
        if edge["to"]["node"] == node["id"]:
            source = by_id[edge['from']['node']]
            port = next(p for p in source['outputs'] if p['id'] == edge['from']['port'])
            incoming.append({
                'input_port': edge['to']['port'],
                'access': f"inputs[{edge['to']['port']!r}]",
                'source_node': source['id'], 'source_port': port['id'],
                'value_type': port['type'], 'description': source['description'],
                'constraints': source.get('constraints', []),
            })
    owned = [r["id"] for r in graph["regions"] if r.get("owner") == node["id"]]
    region_contracts = {}
    for rid in owned:
        members = [n for n in graph["nodes"] if n["region"] == rid]
        region_contracts[rid] = {
            "inputs": [n for n in members if n["kind"] == "RegionInput"],
            "outputs": [n for n in members if n["kind"] == "RegionOutput"],
            "contracts": [{"id": n["id"], "description": n["description"]} for n in members],
        }
    request = {
        "dialect": "core-0.2" if graph.get("metadata", {}).get("graphir_core_version") else "legacy-0.1",
        "signature": f"def {symbol(node['id'])}(inputs, regions):",
        "return_template": 'return {' + ', '.join(repr(p['id']) + ': <' + p['id'] + '>' for p in node['outputs']) + '}',
        "node": node,
        "input_bindings": incoming,
        "input_values": {p['id']: {'type': p['type'], 'access': f"inputs[{p['id']!r}]"}
                         for p in node['inputs']},
        "region_callbacks": region_contracts,
    }
    contracts = node.get('metadata', {}).get('value_contracts')
    if contracts:
        request['value_contracts'] = contracts
    examples = graph.get('examples') or []
    preserved = graph.get('metadata', {}).get('preserved_public_examples', [])
    if not examples and not preserved:
        return request
    synthesized = [n for n in graph['nodes'] if n['kind'] not in BOUNDARIES]
    exact = len(synthesized) == 1 and synthesized[0]['id'] == node['id']
    input_routes = {}
    if exact:
        for edge in graph['edges']:
            if edge['to']['node'] != node['id']:
                continue
            source = by_id[edge['from']['node']]
            if source['kind'] != 'Input':
                exact = False
                break
            parameter = source.get('config', {}).get('parameter')
            if source.get('config', {}).get('mode') == 'stdin':
                parameter = 'stdin'
            if not isinstance(parameter, str):
                exact = False
                break
            input_routes[edge['to']['port']] = parameter
        exact = exact and set(input_routes) == {p['id'] for p in node['inputs']}
    output_routes = []
    for edge in graph['edges']:
        if edge['from']['node'] == node['id'] and by_id[edge['to']['node']]['kind'] == 'Output':
            output_routes.append(edge['from']['port'])
    exact = exact and len(output_routes) == 1
    evidence_by_source = {item.get('raw_source'): item for item in preserved}
    observable = 'stdout' if graph.get('interface', {}).get('mode') == 'stdio' else 'return'
    if exact and examples:
        request['node_examples'] = []
        for example in examples:
            item = {
                'inputs': {local: example['inputs'][public]
                           for local, public in input_routes.items() if public in example['inputs']},
                'outputs': {output_routes[0]: example.get('outputs', {}).get(observable)},
                'scope': 'exact_node_io',
            }
            evidence = evidence_by_source.get(example.get('description'))
            if evidence:
                item['raw_source'] = evidence.get('raw_source', '')
                item['provenance'] = evidence.get('provenance')
            request['node_examples'].append(item)
    elif output_routes and examples:
        # These constrain the composed program, not this node's runtime ABI.
        request['program_examples'] = []
        for example in examples:
            item = {
                'program_inputs': example.get('inputs', {}),
                'scope': 'whole_program_acceptance',
            }
            item['expected_' + observable] = example.get('outputs', {}).get(observable)
            evidence = evidence_by_source.get(example.get('description'))
            if evidence:
                item['raw_source'] = evidence.get('raw_source', '')
                item['provenance'] = evidence.get('provenance')
            request['program_examples'].append(item)
    if output_routes:
        materialized_sources = {example.get('description') for example in examples}
        raw_only = [
            {'raw_source': item.get('raw_source', ''),
             'provenance': item.get('provenance'), 'scope': 'whole_program_public_evidence'}
            for item in preserved if item.get('raw_source') not in materialized_sources
        ]
        if raw_only:
            request['public_example_evidence'] = raw_only
    return request


def _node_declarations(tree, node):
    """Accept a declarative node module; never evaluate generated expressions.

    The compiler places these declarations in a private closure, so imports and
    helper names cannot leak into other nodes or the graph runtime.
    """
    names = set()
    for statement in tree.body:
        bindings = []
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
            continue
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            if isinstance(statement, ast.ImportFrom) and (statement.level or statement.module == '__future__'):
                return None, names, ['relative and __future__ imports are unsupported in node modules']
            if any(alias.name == '*' for alias in statement.names):
                return None, names, ['star imports are unsupported in node modules']
            bindings = [alias.asname or (alias.name.split('.')[0] if isinstance(statement, ast.Import) else alias.name)
                        for alias in statement.names]
        elif isinstance(statement, ast.FunctionDef):
            if statement.decorator_list:
                return None, names, ['top-level node functions/helpers must not have decorators']
            # Default expressions run when declarations are initialized. Accept
            # literal defaults only; calls belong inside the generated function.
            try:
                for default in statement.args.defaults + [d for d in statement.args.kw_defaults if d is not None]:
                    ast.literal_eval(default)
            except (ValueError, TypeError, SyntaxError):
                return None, names, ['top-level helper defaults must be literals']
            bindings = [statement.name]
        elif isinstance(statement, ast.Assign) and all(isinstance(t, ast.Name) for t in statement.targets):
            try:
                ast.literal_eval(statement.value)
            except (ValueError, TypeError, SyntaxError):
                return None, names, ['top-level node constants must be literals']
            bindings = [t.id for t in statement.targets]
        else:
            return None, names, ['node module supports only imports, functions and literal constants; no top-level execution']
        for name in bindings:
            if name in names:
                return None, names, [f'duplicate node module binding: {name}']
            names.add(name)
    entries = [s for s in tree.body if isinstance(s, ast.FunctionDef) and s.name == symbol(node['id'])]
    if len(entries) != 1:
        return None, names, ['node module must define exactly one function with the supplied ABI name']
    return entries[0], names, []


def check_node_source(source: str, node: dict, graph: dict | None = None) -> list[str]:
    try:
        compile(source, "<node>", "exec")
        tree = ast.parse(source)
    except SyntaxError as error:
        return [str(error)]
    fn, module_names, errors = _node_declarations(tree, node)
    if errors:
        return errors
    if (fn.name != symbol(node["id"]) or [a.arg for a in fn.args.args] != ["inputs", "regions"]
        or fn.args.posonlyargs or fn.args.kwonlyargs or fn.args.vararg or fn.args.kwarg
        or fn.args.defaults or fn.decorator_list):
        return ["node function must preserve the exact ABI signature"]
    if any(isinstance(n, (ast.Global, ast.Nonlocal)) for n in ast.walk(tree)):
        return ["node functions must not modify compiler/module globals"]
    # Python's symbol table understands closures/comprehensions, unlike a flat AST
    # name scan. Only explicit node declarations and builtins are available;
    # task variables and undeclared imports are never guessed.
    allowed_globals = set(vars(builtins)) | module_names
    def undefined_globals(table):
        missing = {s.get_name() for s in table.get_symbols()
                   if s.is_referenced() and s.is_global() and s.get_name() not in allowed_globals}
        for child in table.get_children():
            missing.update(undefined_globals(child))
        return missing
    table = symtable.symtable(source, '<node>', 'exec')
    missing = set().union(*(undefined_globals(child) for child in table.get_children()))
    if missing:
        return ['undefined node globals (bind inputs or import locally): ' + ', '.join(sorted(missing))]
    # Check only direct ABI expressions in the outer scope. Unknown types, aliases,
    # dynamic keys and shadowed ABI names are deliberately left to sandbox execution.
    def outer_nodes(item):
        yield item
        for child in ast.iter_child_nodes(item):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
                                      ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                yield from outer_nodes(child)
    body_nodes = [child for statement in fn.body
                  if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  for child in outer_nodes(statement)]
    rebound = {n.id for n in body_nodes if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    from graphir_core import normalize_type
    types = {p['id']: normalize_type(p['type']) for p in node.get('inputs', [])}
    owned = ({r['id'] for r in graph['regions'] if r.get('owner') == node['id']}
             if graph is not None else None)
    def abi_key(expr, name):
        if (name not in rebound and isinstance(expr, ast.Subscript)
                and isinstance(expr.value, ast.Name) and expr.value.id == name
                and isinstance(expr.slice, ast.Constant) and isinstance(expr.slice.value, str)):
            return expr.slice.value
        return None
    for expr in body_nodes:
        key = abi_key(expr, 'inputs')
        if key is not None and key not in types:
            return [f'unknown input port: {key}']
        key = abi_key(expr, 'regions')
        if key is not None and owned is not None and key not in owned:
            return [f'unknown owned region callback: {key}']
        if isinstance(expr, ast.Subscript):
            port = abi_key(expr.value, 'inputs')
            typ = types.get(port, '')
            if typ in {'int', 'float', 'bool', 'complex', 'None'}:
                return [f"input port {port} has non-subscriptable type {typ}; use inputs[{port!r}] directly"]
            if (typ.split('[', 1)[0] in {'str', 'bytes', 'list', 'tuple'}
                    and isinstance(expr.slice, ast.Constant) and isinstance(expr.slice.value, str)):
                return [f'input port {port} has type {typ}, which cannot be indexed by a string']
    expected = {port["id"] for port in node["outputs"]}
    def own_returns(statements):
        for item in statements:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(item, ast.Return):
                yield item.value
            else:
                yield from own_returns(ast.iter_child_nodes(item))
    for returned in own_returns(fn.body):
        if isinstance(returned, ast.Dict) and all(
            isinstance(key, ast.Constant) and isinstance(key.value, str)
            for key in returned.keys
        ):
            actual = {key.value for key in returned.keys}
            if actual != expected:
                return [
                    "literal return keys must equal output ports: "
                    f"expected {sorted(expected)}, got {sorted(actual)}"
                ]
    return []


# Copied into generated artifacts as source, never executed by the generation runner.
RUNTIME = '''
def _gd_region(region_id, bindings):
    values = {}
    result = {}
    for node in _gd_orders[region_id]:
        nid, kind, config = node['id'], node['kind'], node['config']
        args = {}
        for port in node['inputs']:
            sources = _gd_incoming.get((nid, port['id']), [])
            resolved = [values[(a, b)] for a, b in sources]
            args[port['id']] = (resolved if port.get('cardinality') == 'many' else resolved[0]) if sources else port.get('default')
        _gd_assert_ports(node, 'inputs', args)
        if kind == 'RegionInput':
            produced = {p['id']: bindings[nid + '.' + p['id']] for p in node['outputs']}
        elif kind == 'Input':
            if config.get('mode') == 'stdin':
                import sys
                text = sys.stdin.read()
                produced = {p['id']: None if p['type'] == 'EffectToken' else text for p in node['outputs']}
            else:
                produced = {p['id']: bindings[config['parameter']] for p in node['outputs']}
        elif kind == 'Literal':
            import copy
            literal = copy.deepcopy(config['value'])
            produced = {p['id']: literal for p in node['outputs']}
        elif kind == 'RegionOutput':
            result.update({nid + '.' + key: value for key, value in args.items()})
            produced = {}
        elif kind == 'Output':
            observable = [args[p['id']] for p in node['inputs'] if p['type'] != 'EffectToken']
            if config.get('mode') == 'stdout':
                print(*observable, end='\\n' if config.get('newline', True) else '')
            else:
                result['return'] = observable[0] if len(observable) == 1 else tuple(observable)
            produced = {p['id']: None for p in node['outputs']}
        else:
            callbacks = {r['id']: (lambda b, rid=r['id']: _gd_region(rid, b)) for r in _gd_graph['regions'] if r.get('owner') == nid}
            produced = _gd_functions[nid](args, callbacks)
        expected = {p['id'] for p in node['outputs']}
        if not isinstance(produced, dict) or set(produced) != expected:
            raise ValueError('GraphDSL node output contract violated: ' + nid)
        _gd_assert_ports(node, 'outputs', produced)
        values.update({(nid, port): value for port, value in produced.items()})
    return result
'''


def compile_graph(graph: dict, implementations: dict[str, str]) -> str:
    from graphir_core import canonicalize_graph
    from validate_graph import validate
    errors = validate(graph)
    if errors:
        raise ValueError('; '.join(errors))
    graph = canonicalize_graph(graph)
    if graph["interface"]["mode"] == "repository_patch":
        raise ValueError("node ABI produces Python; repository patches require the explicit legacy mode")
    for node in graph["nodes"]:
        if node["kind"] not in BOUNDARIES:
            errors = check_node_source(implementations.get(node["id"], ""), node, graph)
            if errors:
                raise ValueError(f"{node['id']}: {errors}")
    orders = {r["id"]: region_order(graph, r["id"]) for r in graph["regions"]}
    incoming = {}
    for edge in graph["edges"]:
        incoming.setdefault((edge['to']['node'], edge['to']['port']), []).append(
            (edge['from']['node'], edge['from']['port']))
    # Examples are synthesis-time evidence and remain in the GraphIR result/UI.
    # They are not runtime state and should not bloat the emitted Python module.
    runtime_graph = dict(graph, examples=[])
    runtime_graph['metadata'] = {
        key: value for key, value in graph.get('metadata', {}).items()
        if key != 'preserved_public_examples'
    }
    source = "from __future__ import annotations\n\n"
    for nid, code in implementations.items():
        tree = ast.parse(code)
        if len(tree.body) == 1 and isinstance(tree.body[0], ast.FunctionDef):
            source += f"# graphdsl:{nid}\n{code}\n\n"
        else:
            # Keep entry/helper scopes intact: nesting declarations inside the
            # entry itself would accidentally capture its inputs/local variables.
            factory = symbol(nid) + '_module'
            wrapper = ast.parse(f'def {factory}():\n    pass').body[0]
            wrapper.body = tree.body + [ast.Return(value=ast.Name(id=symbol(nid), ctx=ast.Load()))]
            source += f"# graphdsl:{nid}\n" + ast.unparse(ast.fix_missing_locations(wrapper))
            source += f"\n{symbol(nid)} = {factory}()\ndel {factory}\n\n"
    source += f"\n\n_gd_graph = {runtime_graph!r}\n_gd_orders = {orders!r}\n_gd_incoming = {incoming!r}\n"
    source += "_gd_functions = {" + ",".join(f"{nid!r}: {symbol(nid)}" for nid in implementations) + "}\n"
    source += '\n' + (Path(__file__).parent / 'graphir_types.py').read_text() + '\n'
    source += RUNTIME
    interface = graph["interface"]
    if interface["mode"] == "stdio":
        source += "\nif __name__ == '__main__':\n    _gd_region('root', {})\n"
    else:
        entrypoint = interface.get("entrypoint", "")
        parts = entrypoint.split(".")
        if not all(p.isidentifier() for p in parts) or len(parts) not in {1, 2}:
            raise ValueError("invalid function/class entrypoint")
        signature = interface.get("signature") or "()"
        fn = ast.parse(f"def {parts[-1]}{signature}:\n    pass").body[0]
        names = [a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs]
        if fn.args.vararg: names.append(fn.args.vararg.arg)
        if fn.args.kwarg: names.append(fn.args.kwarg.arg)
        bindings = '{' + ','.join(f'{name!r}: {name}' for name in names) + '}'
        wrapper = f"def {parts[-1]}{signature}:\n    return _gd_region('root', {bindings}).get('return')\n"
        if len(parts) == 2:
            wrapper = f"class {parts[0]}:\n" + '\n'.join('    ' + line for line in wrapper.splitlines())
        source += '\n' + wrapper + '\n'
    compile(source, '<graphdsl>', 'exec')
    return source
