#!/usr/bin/env python3
"""Small deterministic JSON Schema evaluator for the GraphDSL schema subset.

The project deliberately keeps the runtime dependency-free.  This module implements the
Draft 2020-12 keywords used by ``schemas/graphdsl.schema.json``; it is not intended to be a
general replacement for the ``jsonschema`` package.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _resolve_ref(root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise ValueError(f"only local JSON pointers are supported: {reference}")
    current: Any = root
    for part in reference[2:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        current = current[key]
    if not isinstance(current, dict):
        raise ValueError(f"schema reference is not an object: {reference}")
    return current


def validate_schema(instance: Any, schema: dict[str, Any]) -> list[str]:
    """Return stable, path-qualified violations for the supported schema subset."""

    errors: list[str] = []

    def visit(value: Any, rule: dict[str, Any], path: str) -> None:
        if "$ref" in rule:
            visit(value, _resolve_ref(schema, rule["$ref"]), path)
            return

        if "const" in rule and value != rule["const"]:
            errors.append(f"{path}: must equal {rule['const']!r}")
        if "enum" in rule and value not in rule["enum"]:
            errors.append(f"{path}: must be one of {rule['enum']!r}")

        expected = rule.get("type")
        if expected is not None:
            choices = expected if isinstance(expected, list) else [expected]
            if not any(_type_matches(value, choice) for choice in choices):
                errors.append(f"{path}: expected {' or '.join(choices)}, got {type(value).__name__}")
                return

        if isinstance(value, str):
            if len(value) < rule.get("minLength", 0):
                errors.append(f"{path}: string is shorter than {rule['minLength']}")
            pattern = rule.get("pattern")
            if pattern and re.search(pattern, value) is None:
                errors.append(f"{path}: does not match pattern {pattern!r}")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in rule and value < rule["minimum"]:
                errors.append(f"{path}: must be >= {rule['minimum']}")

        if isinstance(value, list):
            if len(value) < rule.get("minItems", 0):
                errors.append(f"{path}: requires at least {rule['minItems']} items")
            item_rule = rule.get("items")
            if isinstance(item_rule, dict):
                for index, item in enumerate(value):
                    visit(item, item_rule, f"{path}[{index}]")

        if isinstance(value, dict):
            properties = rule.get("properties", {})
            for name in rule.get("required", []):
                if name not in value:
                    errors.append(f"{path}: missing required property {name!r}")
            additional = rule.get("additionalProperties")
            if additional is False:
                for name in sorted(set(value) - set(properties)):
                    errors.append(f"{path}: additional property {name!r} is not allowed")
            for name, child in value.items():
                child_rule = properties.get(name)
                if isinstance(child_rule, dict):
                    visit(child, child_rule, f"{path}.{name}")
                elif isinstance(additional, dict):
                    visit(child, additional, f"{path}.{name}")

    visit(instance, schema, "$")
    return errors


def load_schema(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"schema must be an object: {path}")
    return value
