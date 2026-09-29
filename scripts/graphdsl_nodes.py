"""GraphDSL node ABI, structural checks, and deterministic Python wiring.

Only node bodies are synthesized. The compiler never asks an LLM to assemble code.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

BOUNDARIES = {"Input", "Output", "Literal", "RegionInput", "RegionOutput"}
CONTROL = {"Loop", "Branch", "Try", "Context"}


def node_demonstrations(node, count):
    if count <= 0: return [], []
    bank = json.loads((Path(__file__).resolve().parents[1] / 'demonstrations/node_catalog.json').read_text())
    query = set(re.findall(r'\w+', node['description'].lower()))
    ranked = sorted((d for d in bank if d['kind'] == node['kind']),
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
    errors = []
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
        if kind in {"Try", "Context"} and config.get("body_region") not in owned:
            errors.append(f"{nid}: requires owned body_region")
        if kind in {'Branch','Try','Context'}:
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
    """No task text, other implementations, or whole-graph description is exposed."""
    by_id = {n["id"]: n for n in graph["nodes"]}
    adjacent = set()
    for edge in graph["edges"]:
        if edge["to"]["node"] == node["id"]:
            adjacent.add(edge["from"]["node"])
        if edge["from"]["node"] == node["id"]:
            adjacent.add(edge["to"]["node"])
    owned = [r["id"] for r in graph["regions"] if r.get("owner") == node["id"]]
    region_contracts = {}
    for rid in owned:
        members = [n for n in graph["nodes"] if n["region"] == rid]
        region_contracts[rid] = {
            "inputs": [n for n in members if n["kind"] == "RegionInput"],
            "outputs": [n for n in members if n["kind"] == "RegionOutput"],
            "contracts": [{"id": n["id"], "description": n["description"]} for n in members],
        }
    return {
        "signature": f"def {symbol(node['id'])}(inputs, regions):",
        "node": node,
        "connected_contracts": [by_id[key] for key in sorted(adjacent)],
        "region_callbacks": region_contracts,
    }


def check_node_source(source: str, node: dict) -> list[str]:
    try:
        compile(source, "<node>", "exec")
        tree = ast.parse(source)
    except SyntaxError as error:
        return [str(error)]
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        return ["output must contain exactly one function; imports/helpers go inside it"]
    fn = tree.body[0]
    if (fn.name != symbol(node["id"]) or [a.arg for a in fn.args.args] != ["inputs", "regions"]
        or fn.args.posonlyargs or fn.args.kwonlyargs or fn.args.vararg or fn.args.kwarg
        or fn.args.defaults or fn.decorator_list):
        return ["node function must preserve the exact ABI signature"]
    if any(isinstance(n, (ast.Global, ast.Nonlocal)) for n in ast.walk(fn)):
        return ["node functions must not modify compiler/module globals"]
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
        values.update({(nid, port): value for port, value in produced.items()})
    return result
'''


def compile_graph(graph: dict, implementations: dict[str, str]) -> str:
    from validate_graph import validate
    errors = validate(graph)
    if errors:
        raise ValueError('; '.join(errors))
    if graph["interface"]["mode"] == "repository_patch":
        raise ValueError("node ABI produces Python; repository patches require the explicit legacy mode")
    for node in graph["nodes"]:
        if node["kind"] not in BOUNDARIES:
            errors = check_node_source(implementations.get(node["id"], ""), node)
            if errors:
                raise ValueError(f"{node['id']}: {errors}")
    orders = {r["id"]: region_order(graph, r["id"]) for r in graph["regions"]}
    incoming = {}
    for edge in graph["edges"]:
        incoming.setdefault((edge['to']['node'], edge['to']['port']), []).append(
            (edge['from']['node'], edge['from']['port']))
    source = "from __future__ import annotations\n\n"
    source += "\n\n".join(f"# graphdsl:{nid}\n{code}" for nid, code in implementations.items())
    source += f"\n\n_gd_graph = {graph!r}\n_gd_orders = {orders!r}\n_gd_incoming = {incoming!r}\n"
    source += "_gd_functions = {" + ",".join(f"{nid!r}: {symbol(nid)}" for nid in implementations) + "}\n"
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
