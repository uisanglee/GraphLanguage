#!/usr/bin/env python3
"""Deterministic schema and semantic validator for GraphDSL 0.1 documents."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from schema_validation import load_schema, validate_schema


ALLOWED_TOP_LEVEL = {
    "graphdsl_version", "id", "title", "description", "target", "interface",
    "regions", "nodes", "edges", "constraints", "examples", "metadata",
}
ALLOWED_EDGE_KEYS = {"from", "to"}
ALLOWED_NODE_KINDS = {
    "Input", "Output", "Literal", "Function", "Module", "RegionInput", "RegionOutput",
    "Compute", "Custom", "Call", "Access", "Construct", "Update", "Branch", "Loop", "Try",
    "Raise", "Exit", "Effect", "Resource", "Context", "Assert", "Test", "SourceArtifact",
    "Locate", "Edit", "AddArtifact", "DeleteArtifact", "Patch",
}


def duplicates(values: list[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


DEFAULT_SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "graphdsl.schema.json"


def validate_semantics(document: dict[str, Any]) -> list[str]:
    # Shape errors are terminal model failures, never Python exceptions/retry triggers.
    shape_errors = validate_schema(document, load_schema(DEFAULT_SCHEMA))
    if shape_errors:
        return ["cannot validate semantics: " + error for error in shape_errors]
    errors: list[str] = []
    if document.get("graphdsl_version") != "0.1.0":
        errors.append("graphdsl_version must equal 0.1.0")
    unknown = set(document) - ALLOWED_TOP_LEVEL
    if unknown:
        errors.append(f"unknown top-level fields: {sorted(unknown)}")

    regions = document.get("regions", [])
    if not isinstance(regions, list):
        return errors + ["regions must be an array"]
    region_ids = [
        region.get("id") for region in regions
        if isinstance(region, dict) and isinstance(region.get("id"), str)
    ]
    if "root" not in region_ids:
        errors.append("regions must contain root")
    if repeated := duplicates(region_ids):
        errors.append(f"duplicate region ids: {repeated}")

    raw_nodes = document.get("nodes", [])
    if not isinstance(raw_nodes, list):
        return errors + ["nodes must be an array"]
    node_ids = [
        node.get("id") for node in raw_nodes
        if isinstance(node, dict) and isinstance(node.get("id"), str)
    ]
    if repeated := duplicates(node_ids):
        errors.append(f"duplicate node ids: {repeated}")
    nodes = {
        node["id"]: node for node in raw_nodes
        if isinstance(node, dict) and isinstance(node.get("id"), str)
    }

    ports: dict[str, dict[str, dict[str, Any]]] = {}
    for node_id, node in nodes.items():
        if not node_id:
            errors.append("node without id")
            continue
        if node.get("region") not in region_ids:
            errors.append(f"node {node_id}: unknown region {node.get('region')!r}")
        if node.get("kind") not in ALLOWED_NODE_KINDS:
            errors.append(f"node {node_id}: unknown kind {node.get('kind')!r}")
        inputs = node.get("inputs", [])
        outputs = node.get("outputs", [])
        if not isinstance(inputs, list) or not isinstance(outputs, list):
            errors.append(f"node {node_id}: inputs and outputs must be arrays")
            continue
        input_ids = [port.get("id") for port in inputs if isinstance(port, dict)]
        output_ids = [port.get("id") for port in outputs if isinstance(port, dict)]
        if repeated := duplicates(input_ids):
            errors.append(f"node {node_id}: duplicate input ports {repeated}")
        if repeated := duplicates(output_ids):
            errors.append(f"node {node_id}: duplicate output ports {repeated}")
        if overlap := sorted(set(input_ids) & set(output_ids)):
            errors.append(f"node {node_id}: port ids used as both input and output {overlap}")
        ports[node_id] = {
            "inputs": {port["id"]: port for port in inputs if isinstance(port, dict) and port.get("id")},
            "outputs": {port["id"]: port for port in outputs if isinstance(port, dict) and port.get("id")},
        }

    incoming: Counter[tuple[str, str]] = Counter()
    seen_edges: set[tuple[str, str, str, str]] = set()
    raw_edges = document.get("edges", [])
    if not isinstance(raw_edges, list):
        return errors + ["edges must be an array"]
    for index, edge in enumerate(raw_edges):
        if not isinstance(edge, dict):
            errors.append(f"edge {index}: must be an object")
            continue
        if set(edge) != ALLOWED_EDGE_KEYS:
            errors.append(f"edge {index}: only from/to fields are allowed")
            continue
        source = edge.get("from", {})
        target = edge.get("to", {})
        if not isinstance(source, dict) or not isinstance(target, dict):
            errors.append(f"edge {index}: from/to must be objects")
            continue
        source_node, source_port = source.get("node"), source.get("port")
        target_node, target_port = target.get("node"), target.get("port")
        if not all(isinstance(value, str) for value in (source_node, source_port, target_node, target_port)):
            errors.append(f"edge {index}: endpoint node/port values must be strings")
            continue
        if source_node not in nodes:
            errors.append(f"edge {index}: unknown source node {source_node!r}")
        elif source_port not in ports[source_node]["outputs"]:
            errors.append(f"edge {index}: {source_node}.{source_port} is not an output")
        if target_node not in nodes:
            errors.append(f"edge {index}: unknown target node {target_node!r}")
        elif target_port not in ports[target_node]["inputs"]:
            errors.append(f"edge {index}: {target_node}.{target_port} is not an input")
        key = (source_node, source_port, target_node, target_port)
        if key in seen_edges:
            errors.append(f"edge {index}: duplicate connection {key}")
        seen_edges.add(key)
        incoming[(target_node, target_port)] += 1
        if source_node in nodes and target_node in nodes:
            source_region = nodes[source_node].get("region")
            target_region = nodes[target_node].get("region")
            if source_region != target_region:
                errors.append(
                    f"edge {index}: crosses regions {source_region!r} -> {target_region!r}; "
                    "use RegionInput/RegionOutput bindings"
                )
            source_type = ports[source_node]["outputs"].get(source_port, {}).get("type")
            target_type = ports[target_node]["inputs"].get(target_port, {}).get("type")
            if (
                source_type and target_type and source_type != target_type
                and source_type != "Any" and target_type != "Any"
            ):
                errors.append(
                    f"edge {index}: incompatible port types {source_type!r} -> {target_type!r}"
                )

    for (node_id, port_id), count in incoming.items():
        port = ports.get(node_id, {}).get("inputs", {}).get(port_id, {})
        if count > 1 and port.get("cardinality", "one") != "many":
            errors.append(f"{node_id}.{port_id}: {count} incoming edges but cardinality is one")

    for node_id, node_ports in ports.items():
        for port_id, port in node_ports["inputs"].items():
            if (
                port.get("required", True)
                and "default" not in port
                and incoming[(node_id, port_id)] == 0
            ):
                errors.append(f"{node_id}.{port_id}: required input has no edge or default")

    owners = {
        region.get("owner") for region in regions
        if isinstance(region, dict) and isinstance(region.get("owner"), str)
    }
    for owner in sorted(owners):
        if owner not in nodes:
            errors.append(f"region owner {owner!r} is not a node")
    for region in regions:
        if not isinstance(region, dict) or region.get("id") == "root":
            continue
        region_owner = region.get("owner")
        owner = nodes.get(region_owner, {}) if isinstance(region_owner, str) else {}
        if owner.get("kind") not in {"Branch", "Loop", "Try", "Context"}:
            errors.append(
                f"region {region.get('id')}: owner must be Branch, Loop, Try, or Context"
            )

    interface = document.get("interface", {})
    mode = interface.get("mode") if isinstance(interface, dict) else None
    if mode not in {"function", "stdio", "repository_patch"}:
        errors.append(f"invalid interface mode {mode!r}")
    if mode == "repository_patch" and not any(
        isinstance(n, dict) and n.get("kind") == "Patch" for n in raw_nodes
    ):
        errors.append("repository_patch graph must contain a Patch node")
    from graphdsl_nodes import validate_structure
    try:
        errors.extend(validate_structure(document))
    except (TypeError, KeyError, AttributeError, ValueError) as error:
        errors.append(f'malformed node configuration: {error}')
    return errors


def validate(
    document: dict[str, Any], schema: dict[str, Any] | None = None
) -> list[str]:
    """Run the JSON Schema gate first and semantic checks second."""

    active_schema = schema if schema is not None else load_schema(DEFAULT_SCHEMA)
    schema_errors = [f"schema: {error}" for error in validate_schema(document, active_schema)]
    semantic_errors = [f"semantic: {error}" for error in validate_semantics(document)]
    return schema_errors + semantic_errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", type=Path, nargs="+")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    arguments = parser.parse_args()
    schema = load_schema(arguments.schema)
    failed = False
    for path in arguments.paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        errors = validate(document, schema)
        if errors:
            failed = True
            print(f"{path}: INVALID")
            for error in errors:
                print(f"  - {error}")
        else:
            print(f"{path}: valid")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
