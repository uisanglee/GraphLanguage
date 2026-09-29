#!/usr/bin/env python3
"""Deterministic syntax/format gate for synthesized Python or repository patches."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path


def strip_fence(text: str) -> str:
    value = text.strip()
    if not value.startswith("```"):
        return value
    lines = value.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def validate_artifact(text: str, interface_mode: str) -> list[str]:
    errors: list[str] = []
    value = text.strip()
    # Normalize one outer chat fence consistently with the GraphIR and Parsel readers.
    value = strip_fence(value)
    if not value:
        return errors + ["artifact is empty"]
    if interface_mode in {"function", "stdio"}:
        try:
            compile(value, "<candidate>", "exec")
        except SyntaxError as error:
            errors.append(
                f"Python syntax error at line {error.lineno}, column {error.offset}: {error.msg}"
            )
    elif interface_mode == "repository_patch":
        lines = value.splitlines()
        if not any(line.startswith("diff --git ") for line in lines):
            errors.append("unified diff must contain a 'diff --git' header")
        if not any(line.startswith("--- ") for line in lines):
            errors.append("unified diff must contain an old-file header")
        if not any(line.startswith("+++ ") for line in lines):
            errors.append("unified diff must contain a new-file header")
        if not any(line.startswith("@@ ") for line in lines):
            errors.append("unified diff must contain at least one hunk")
    else:
        errors.append(f"unknown interface mode {interface_mode!r}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--mode", required=True, choices=["function", "stdio", "repository_patch"])
    args = parser.parse_args()
    errors = validate_artifact(args.path.read_text(encoding="utf-8"), args.mode)
    for error in errors:
        print(error)
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
