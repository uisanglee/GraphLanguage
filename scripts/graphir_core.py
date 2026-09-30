"""GraphIR Core 0.2 normalization into the stable executable graph ABI.

The model emits the compact, human-editable form.  This module performs only
deterministic expansion of defaults and syntax; it never repairs graph meaning.
"""
from __future__ import annotations

import re
import ast
from typing import Any


CORE_VERSION = "0.2.0"


class CanonicalCore(dict):
    """Trusted in-memory lowering result; JSON metadata cannot select validation rules."""


def normalize_type(value: str) -> str:
    """Canonicalize equivalent built-in typing spellings."""
    result = value.strip()
    aliases = {
        "List": "list", "Tuple": "tuple", "Dict": "dict", "Set": "set",
        "FrozenSet": "frozenset", "Type": "type",
    }
    try:
        tree = ast.parse(result, mode='eval')
        class Types(ast.NodeTransformer):
            def visit_Name(self, node):
                return ast.Name(id=aliases.get(node.id, node.id), ctx=ast.Load())

            def visit_Attribute(self, node):
                if isinstance(node.value, ast.Name) and node.value.id == 'typing':
                    return ast.Name(id=aliases.get(node.attr, node.attr), ctx=ast.Load())
                return self.generic_visit(node)

            def visit_Subscript(self, node):
                node = self.generic_visit(node)
                if isinstance(node.value, ast.Name) and node.value.id == 'Optional':
                    return ast.BinOp(left=node.slice, op=ast.BitOr(), right=ast.Constant(None))
                return node
        return ast.unparse(ast.fix_missing_locations(Types().visit(tree)))
    except SyntaxError:
        return result


def endpoint(value: str) -> dict[str, str]:
    if not isinstance(value, str) or "." not in value:
        raise ValueError(f"endpoint must be 'node.port': {value!r}")
    node, port = value.rsplit(".", 1)
    if not node or not port:
        raise ValueError(f"endpoint must be 'node.port': {value!r}")
    return {"node": node, "port": port}


def _ports(value: Any, node_id: str, direction: str) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        raise ValueError(f"node {node_id}: {direction} must be a type map")
    ports = []
    for port_id, port_type in value.items():
        if not isinstance(port_id, str) or not isinstance(port_type, str):
            raise ValueError(f"node {node_id}: {direction} must map string ids to string types")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:/-]*", port_id) is None:
            raise ValueError(f"node {node_id}: invalid {direction} port id {port_id!r}")
        ports.append({"id": port_id, "type": normalize_type(port_type)})
    return ports


def canonicalize_graph(document: dict[str, Any]) -> dict[str, Any]:
    """Expand a Core graph for the existing validator/compiler.

    Legacy 0.1 documents are returned unchanged so old runs remain reproducible.
    """
    if document.get("graphdsl_version") == "0.1.0":
        return document
    if document.get("graphir_version") != CORE_VERSION:
        raise ValueError(f"unsupported GraphIR version: {document.get('graphir_version')!r}")

    interface = dict(document.get("interface") or {})
    mode = interface.get("mode")
    raw_nodes, expanded_edges, regions = expand_bodies(document)
    nodes = []
    for raw in raw_nodes:
        node = dict(raw)
        node_id = node.get("id")
        kind = node.get("kind")
        config = dict(node.get("config") or {})
        if kind == "Input" and mode != "repository_patch":
            if mode == "stdio":
                config.setdefault("mode", "stdin")
            else:
                config.setdefault("parameter", node_id)
        elif kind == "Output":
            config.setdefault(
                "mode", "patch" if mode == "repository_patch" else "stdout" if mode == "stdio" else "return"
            )
        nodes.append({
            "id": node_id,
            "kind": kind,
            "label": node_id.replace("_", " ") if isinstance(node_id, str) else "node",
            "description": node.get("description", ""),
            "region": node.get('_region', 'root'),
            "inputs": _ports(node.get("inputs"), node_id, "inputs"),
            "outputs": _ports(node.get("outputs"), node_id, "outputs"),
            "config": config,
            **({"constraints": node["constraints"]} if "constraints" in node else {}),
            **({"examples": node["examples"]} if "examples" in node else {}),
            **({"metadata": node["metadata"]} if "metadata" in node else {}),
        })

    metadata = dict(document.get("metadata") or {})
    metadata["graphir_core_version"] = CORE_VERSION
    return CanonicalCore({
        "graphdsl_version": "0.1.0",
        "id": document.get("id") or "graphir:program",
        "title": document.get("title") or document.get("id") or "GraphIR program",
        "description": document.get("description") or "GraphIR Core program",
        "target": {"language": "python", "python_version": ">=3.10", **(document.get("target") or {})},
        "interface": interface,
        "regions": regions,
        "nodes": nodes,
        "edges": expanded_edges,
        "constraints": list(document.get("constraints") or []),
        "examples": list(document.get("examples") or []),
        "metadata": metadata,
    })


def is_core_graph(document: dict[str, Any]) -> bool:
    return document.get("graphir_version") == CORE_VERSION


def expand_bodies(document):
    """Lower lexical subgraphs. $input/$output are pins, never authored nodes."""
    nodes, edges = [], []
    regions = [{'id': 'root', 'owner': None, 'role': 'root'}]

    def types(ports):
        return {p['id']: p['type'] for p in _ports(ports, 'body', 'ports')}

    def visit(graph, region='root', prefix='', owner=None, role='body'):
        local_ids = [n['id'] for n in graph['nodes']]
        if len(set(local_ids)) != len(local_ids) or any(n.startswith('__') or '.' in n for n in local_ids):
            raise ValueError('node ids must be unique in each body; dots and __ prefix are reserved')
        if owner is not None:
            regions.append({'id': region, 'owner': owner, 'role': role})
            for name, kind, ins, outs in [
                ('__input', 'RegionInput', {}, graph['inputs']),
                ('__output', 'RegionOutput', graph['outputs'], {}),
            ]:
                nodes.append({'id': prefix + name, 'kind': kind, 'description': 'Generated body boundary',
                              'inputs': ins, 'outputs': outs, 'config': {}, '_region': region})
        for raw in graph['nodes']:
            node = dict(raw)
            nid = prefix + raw['id']
            node.update(id=nid, _region=region)
            kind = node['kind']
            if owner is not None and kind in {'Input', 'Output'}:
                raise ValueError('nested bodies use $input/$output pins instead of Input/Output nodes')
            if kind not in {'Loop', 'Branch'} and any(k in raw for k in ('body', 'control', 'branches')):
                raise ValueError(f'{nid}: only controllers may own bodies')
            if kind in {'Loop', 'Branch'} and raw.get('config'):
                raise ValueError(f'{nid}: controller bindings are inferred; config must be omitted')
            ins, outs = types(raw['inputs']), types(raw['outputs'])
            if kind == 'Loop':
                if 'body' not in raw or 'control' not in raw or 'branches' in raw:
                    raise ValueError(f'{nid}: Loop requires control and body')
                ctl, body = raw['control'], raw['body']
                iterable, item, state = ctl['for_each'], ctl['item'], ctl['state']
                bi, bo = types(body['inputs']), types(body['outputs'])
                if len(set(state)) != len(state) or item in ins or item in state or iterable in state:
                    raise ValueError(f'{nid}: item, iterable and unique state names must not conflict')
                if iterable not in ins or set(state) != set(outs) or not set(state) <= set(ins):
                    raise ValueError(f'{nid}: outputs must equal state names with initial inputs')
                if set(bi) != (set(ins) - {iterable}) | {item} or bo != outs:
                    raise ValueError(f'{nid}: body must expose item, state, invariants and matching state outputs')
                if any(bi[k] != ins[k] for k in bi if k != item) or any(ins[k] != outs[k] for k in state):
                    raise ValueError(f'{nid}: state/invariant types must match')
                try:
                    iterable_ast = ast.parse(ins[iterable], mode='eval').body
                except SyntaxError as error:
                    raise ValueError(f'{nid}: invalid iterable type annotation') from error
                if isinstance(iterable_ast, ast.Subscript) and isinstance(iterable_ast.value, ast.Name):
                    if iterable_ast.value.id in {'list', 'set', 'Iterable', 'Sequence', 'Iterator'}:
                        if normalize_type(ast.unparse(iterable_ast.slice)) != bi[item]:
                            raise ValueError(f'{nid}: iteration item type does not match iterable')
                rid = nid + '.body'
                pin, pout = rid + '.__input.', rid + '.__output.'
                node['config'] = {'mode': 'for_each', 'iterable_port': iterable, 'body_region': rid,
                    'iteration': {'item_boundary': pin + item},
                    'carried': [{'initial_port': k, 'input_boundary': pin+k, 'output_boundary': pout+k,
                                 'final_port': k} for k in state],
                    'bindings': {pin+k: k for k in bi if k != item and k not in state}}
                visit(body, rid, rid+'.', nid)
            elif kind == 'Branch':
                branches = raw.get('branches', [])
                if not branches or 'body' in raw or 'control' in raw:
                    raise ValueError(f'{nid}: Branch requires ordered branches')
                if branches[-1].get('when') is not None or any(b.get('when') is None for b in branches[:-1]):
                    raise ValueError(f'{nid}: last branch must be the only else (when: null)')
                config = {'branches': [], 'region_bindings': {}}
                for index, branch in enumerate(branches):
                    condition, body = branch['when'], branch['body']
                    bi, bo = types(body['inputs']), types(body['outputs'])
                    if condition is not None and ins.get(condition) != 'bool':
                        raise ValueError(f'{nid}: branch condition must name a bool input')
                    if bo != outs or any(k not in ins or ins[k] != v for k,v in bi.items()):
                        raise ValueError(f'{nid}: branch ports must match owner ports by name and type')
                    rid = nid + '.branch_' + str(index)
                    config['branches'].append({'region': rid, 'condition_port': condition})
                    config['region_bindings'][rid] = {
                        'inputs': {rid+'.__input.'+k: k for k in bi},
                        'outputs': {k: rid+'.__output.'+k for k in bo}}
                    visit(body, rid, rid+'.', nid, 'else' if condition is None else 'then')
                node['config'] = config
            nodes.append(node)
        for edge in graph['edges']:
            lowered = {}
            for direction in ('from', 'to'):
                ep = endpoint(edge[direction])
                if ep['node'] in {'$input', '$output'}:
                    expected = '$input' if direction == 'from' else '$output'
                    if owner is None or ep['node'] != expected:
                        raise ValueError('body boundary endpoint used in wrong direction or at root')
                    ep['node'] = '__input' if direction == 'from' else '__output'
                elif ep['node'] not in local_ids:
                    raise ValueError(f'{region}: unknown local node {ep["node"]}')
                ep['node'] = prefix + ep['node']
                lowered[direction] = ep
            edges.append(lowered)
    visit(document)
    return nodes, edges, regions
