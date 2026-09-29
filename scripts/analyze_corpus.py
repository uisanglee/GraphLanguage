#!/usr/bin/env python3
"""Analyze prompt and reference-code coverage to justify the GraphDSL kernel."""

from __future__ import annotations

import argparse
import ast
import json
import math
import re
import statistics
import warnings
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator


FEATURE_PATTERNS = {
    "stdin/stdout": r"\b(stdin|stdout|standard input|standard output|print the|input format|output format)\b",
    "file/resource": r"\b(file|directory|path|csv|jsonl?|yaml|xml|archive|zip)\b",
    "network/url": r"\b(http|https|url|request|socket|api endpoint)\b",
    "database/table": r"\b(database|sql|table|dataframe|pandas)\b",
    "regex/text": r"\b(regex|regular expression|substring|tokeni[sz]e|parse)\b",
    "graph/tree": r"\b(graph|tree|vertex|vertices|edge|node|ancestor|subtree)\b",
    "dynamic programming": r"\b(dynamic programming|\bdp\b|memoization)\b",
    "sorting/search": r"\b(sort|sorted|binary search|search)\b",
    "exception/error": r"\b(exception|error|raise|invalid|fail)\b",
    "class/object": r"\b(class|object|instance|method|attribute)\b",
    "async/concurrency": r"\b(async|await|concurren|thread|process|parallel)\b",
    "generator/iterator": r"\b(generator|iterator|yield|iterable)\b",
    "complexity/performance": r"\b(complexity|efficient|optimi[sz]|time limit|memory limit|O\()",
    "repository change": r"\b(issue|bug|regression|repository|module|backward compat|deprecat)\b",
}

AST_GROUPS = {
    "Branch": (ast.If, ast.IfExp, ast.Match),
    "Loop": (ast.For, ast.While, ast.AsyncFor),
    "Try/Raise": (ast.Try, ast.Raise, ast.Assert),
    "Context/Resource": (ast.With, ast.AsyncWith),
    "Call": (ast.Call,),
    "Access": (ast.Attribute, ast.Subscript, ast.Slice),
    "Construct": (ast.List, ast.Tuple, ast.Set, ast.Dict),
    "Comprehension": (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp),
    "Update": (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete, ast.NamedExpr),
    "Function": (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda),
    "Class": (ast.ClassDef,),
    "Import": (ast.Import, ast.ImportFrom),
    "Generator": (ast.Yield, ast.YieldFrom),
    "Async": (ast.Await,),
}


def iter_records(input_dir: Path) -> Iterator[dict[str, Any]]:
    for path in sorted(input_dir.glob("*.jsonl")):
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                yield json.loads(line)


def percentile(values: list[int], probability: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(probability * len(ordered)) - 1))
    return ordered[index]


def reference_python(record: dict[str, Any]) -> str:
    reference = record.get("reference", {})
    solution = reference.get("canonical_solution", "")
    if not solution:
        return ""
    if record["benchmark"] == "humaneval":
        return record.get("starter_code", "") + solution
    if record["benchmark"] == "bigcodebench":
        return record.get("starter_code", "") + solution
    return solution


def ast_presence(source: str) -> set[str]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source)
    except (SyntaxError, ValueError, TypeError):
        return set()
    nodes = tuple(ast.walk(tree))
    return {
        label
        for label, node_types in AST_GROUPS.items()
        if any(isinstance(node, node_types) for node in nodes)
    }


def patch_paths(patch: str) -> list[str]:
    return re.findall(r"^diff --git a/(.+?) b/(.+?)$", patch, flags=re.MULTILINE)


def markdown_table(headers: list[str], rows: Iterable[Iterable[Any]]) -> str:
    result = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    result.extend("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)
    return "\n".join(result)


def analyze(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(record["benchmark"] for record in records)
    interfaces = Counter(record["interface"] for record in records)
    lengths: dict[str, list[int]] = defaultdict(list)
    keyword_tasks: dict[str, Counter[str]] = defaultdict(Counter)
    ast_tasks: dict[str, Counter[str]] = defaultdict(Counter)
    ast_parsed = Counter()
    starter_present = Counter()
    variants = Counter()
    platform = Counter()
    difficulty = Counter()
    swe_repos = Counter()
    swe_files_per_task: list[int] = []

    for record in records:
        benchmark = record["benchmark"]
        prompt = record["prompt"]["primary"]
        lengths[benchmark].append(len(prompt))
        variants[benchmark] += len(record["prompt"].get("variants", {}))
        if record.get("starter_code"):
            starter_present[benchmark] += 1
        lower = prompt.lower()
        for feature, pattern in FEATURE_PATTERNS.items():
            if re.search(pattern, lower, flags=re.IGNORECASE):
                keyword_tasks[benchmark][feature] += 1

        source = reference_python(record)
        if source:
            present = ast_presence(source)
            if present:
                ast_parsed[benchmark] += 1
                for group in present:
                    ast_tasks[benchmark][group] += 1

        metadata = record.get("metadata", {})
        if benchmark == "livecodebench":
            platform[str(metadata.get("platform"))] += 1
            difficulty[str(metadata.get("difficulty"))] += 1
        elif benchmark == "swebench":
            swe_repos[str(metadata.get("repo"))] += 1
            swe_files_per_task.append(len(patch_paths(record.get("reference", {}).get("patch", ""))))

    return {
        "total": len(records),
        "counts": dict(counts),
        "interfaces": dict(interfaces),
        "prompt_lengths": {
            benchmark: {
                "min": min(values),
                "median": int(statistics.median(values)),
                "p95": percentile(values, 0.95),
                "max": max(values),
            }
            for benchmark, values in lengths.items()
        },
        "prompt_variants": dict(variants),
        "starter_code_present": dict(starter_present),
        "keyword_task_counts": {key: dict(value) for key, value in keyword_tasks.items()},
        "reference_ast_parsed": dict(ast_parsed),
        "reference_ast_task_counts": {key: dict(value) for key, value in ast_tasks.items()},
        "livecodebench_platforms": dict(platform),
        "livecodebench_difficulties": dict(difficulty),
        "swebench_repositories": dict(swe_repos),
        "swebench_changed_files": {
            "median": statistics.median(swe_files_per_task) if swe_files_per_task else 0,
            "p95": percentile(swe_files_per_task, 0.95),
            "max": max(swe_files_per_task, default=0),
        },
    }


def render(report: dict[str, Any]) -> str:
    benchmarks = ["humaneval", "mbpp", "bigcodebench", "livecodebench", "swebench"]
    sections = [
        "# Benchmark corpus analysis",
        "",
        "이 보고서는 `data/normalized/*.jsonl`의 모든 레코드를 읽어 생성했다. prompt keyword는",
        "요구사항의 하한을 보여주는 휴리스틱이며 의미 분류 정답으로 해석하면 안 된다. AST 표는",
        "reference solution이 공개된 corpus만 대상으로 task별 syntax 존재 여부를 집계한다.",
        "",
        "## Corpus",
        "",
        markdown_table(
            ["benchmark", "tasks", "interface", "prompt median", "p95", "max", "starter code"],
            (
                (
                    benchmark,
                    report["counts"].get(benchmark, 0),
                    "repository_patch" if benchmark == "swebench" else "stdio" if benchmark == "livecodebench" else "function",
                    report["prompt_lengths"].get(benchmark, {}).get("median", 0),
                    report["prompt_lengths"].get(benchmark, {}).get("p95", 0),
                    report["prompt_lengths"].get(benchmark, {}).get("max", 0),
                    report["starter_code_present"].get(benchmark, 0),
                )
                for benchmark in benchmarks
            ),
        ),
        "",
        f"총 task 수: **{report['total']}**. BigCodeBench의 complete/instruct와 기타 distinct prompt",
        "variant는 각 record 안에 함께 보존되므로 task 수와 prompt variant 수는 다를 수 있다.",
        "",
        "## Prompt requirement signals",
        "",
    ]

    features = list(FEATURE_PATTERNS)
    sections.append(
        markdown_table(
            ["signal", *benchmarks],
            (
                (feature, *(report["keyword_task_counts"].get(b, {}).get(feature, 0) for b in benchmarks))
                for feature in features
            ),
        )
    )
    sections.extend(["", "## Reference Python syntax coverage", ""])
    ast_groups = list(AST_GROUPS)
    code_benchmarks = ["humaneval", "mbpp", "bigcodebench"]
    sections.append(
        markdown_table(
            ["GraphDSL capability", *code_benchmarks],
            (
                (group, *(report["reference_ast_task_counts"].get(b, {}).get(group, 0) for b in code_benchmarks))
                for group in ast_groups
            ),
        )
    )
    sections.extend(
        [
            "",
            "집계 결과가 정당화하는 최소 kernel은 `Compute`, `Call`, `Access`, `Construct`,",
            "`Update`, `Branch`, `Loop`, `Try/Raise`, `Context/Resource`, `Function`, `Test`다.",
            "comprehension이나 generator는 별도 고정 node로 강제하지 않고 작은 경우 `Compute`,",
            "사용자가 내부 상태를 편집해야 하는 경우 `Loop`로 승격한다.",
            "",
            "## LiveCodeBench distribution",
            "",
            markdown_table(
                ["platform", "tasks"], sorted(report["livecodebench_platforms"].items())
            ),
            "",
            markdown_table(
                ["difficulty", "tasks"], sorted(report["livecodebench_difficulties"].items())
            ),
            "",
            "LiveCodeBench에는 reference solution이 포함되지 않으므로 AST 통계로 node coverage를",
            "추정하지 않는다. 문제 형식이 stdio와 method starter code를 모두 포함할 수 있으므로",
            "`interface.mode=stdio` 아래에서도 starter signature를 metadata로 보존해야 한다.",
            "",
            "## SWE-bench repository overlay",
            "",
            markdown_table(
                ["repository", "tasks"], sorted(report["swebench_repositories"].items())
            ),
            "",
            "Gold patch에서 관측한 변경 file 수는 중앙값 {median}, p95 {p95}, 최대 {max}다. 이 값은".format(
                **report["swebench_changed_files"]
            ),
            "graph가 저장소 전체를 복제하는 대신 관련 `SourceArtifact`와 `Edit`만 참조해야 한다는",
            "근거다. Gold patch는 분석에만 사용하며 Qwen 입력에는 절대 포함하지 않는다.",
            "",
            "## Design decisions",
            "",
            "1. edge schema는 `from`/`to`만 유지한다. corpus의 조건·반복·예외·API argument 의미는",
            "   edge 종류가 아니라 owning node와 port contract로 표현할 수 있다.",
            "2. BigCodeBench의 library 다양성은 API별 node kind가 아니라 동적 `Call` signature를",
            "   요구한다.",
            "3. LiveCodeBench는 `parse -> solve -> format` 분리를 요구하고, loop-carried state는",
            "   `Loop.config.carried`와 region boundary로 표현한다.",
            "4. SWE-bench는 `Locate -> SourceArtifact -> Edit -> Test -> Patch` overlay를 요구한다.",
            "5. reference가 없는 task에서도 요구사항 손실을 검사하도록 graph-level constraints와",
            "   examples를 원 prompt에서 그대로 보존한다.",
            "",
        ]
    )
    return "\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path("data/normalized"))
    parser.add_argument("--output", type=Path, default=Path("docs/CORPUS_ANALYSIS.md"))
    parser.add_argument("--json-output", type=Path, default=Path("data/corpus_analysis.json"))
    args = parser.parse_args()
    records = list(iter_records(args.input_dir))
    if not records:
        raise SystemExit(f"no JSONL records found in {args.input_dir}")
    report = analyze(records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(report), encoding="utf-8")
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"analyzed {len(records)} tasks")


if __name__ == "__main__":
    main()
