from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from retrieve_demonstrations import load_catalog, retrieve  # noqa: E402
from run_qwen_pipeline import make_chat_payload  # noqa: E402
from schema_validation import load_schema  # noqa: E402
from validate_artifact import validate_artifact  # noqa: E402
from validate_graph import validate  # noqa: E402
from export_predictions import export_functional  # noqa: E402
from build_qwen_eval import is_official_evaluation_task, safe_task  # noqa: E402
from run_qwen_pipeline import with_high_level_plan  # noqa: E402


class PipelineComponentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_schema(ROOT / "schemas" / "graphdsl.schema.json")
        cls.graph = json.loads(
            (ROOT / "examples" / "function_loop.graph.json").read_text(encoding="utf-8")
        )

    def test_valid_example_passes_schema_and_semantics(self) -> None:
        self.assertEqual(validate(self.graph, self.schema), [])

    def test_schema_and_semantic_errors_are_both_reported(self) -> None:
        graph = copy.deepcopy(self.graph)
        del graph["title"]
        graph["edges"][0]["label"] = "illegal edge meaning"
        errors = validate(graph, self.schema)
        self.assertTrue(any("schema:" in error and "title" in error for error in errors))
        self.assertTrue(any("semantic:" in error and "label" in error for error in errors))

    def test_retrieval_filters_by_interface(self) -> None:
        catalog = load_catalog(ROOT / "demonstrations" / "catalog.json")
        selected = retrieve(catalog, "parse stdin and print", "stdio", "livecodebench", 1)
        self.assertEqual([item["id"] for item in selected], ["stdio-pipeline"])

    def test_response_format_contains_full_schema(self) -> None:
        payload = make_chat_payload(
            "qwen", [{"role": "user", "content": "task"}], 100, 0.0,
            self.schema, "response_format",
        )
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertIs(payload["response_format"]["json_schema"]["schema"], self.schema)

    def test_artifact_gate_rejects_invalid_python(self) -> None:
        self.assertTrue(validate_artifact("def broken(:\n", "function"))
        self.assertEqual(validate_artifact("def ok():\n    return 1\n", "function"), [])

    def test_humaneval_export_adds_official_check_invocation(self) -> None:
        task = {
            "id": "humaneval:HumanEval/0",
            "entrypoint": "candidate",
            "starter_code": "",
            "reference": {"test": "def check(fn):\n    assert fn() == 1"},
        }
        result = {"generated_artifact": "def candidate():\n    return 1"}
        jobs = export_functional({task["id"]: task}, {task["id"]: result}, "humaneval")
        self.assertEqual(jobs[0]["invocation"], "check(candidate)")
        self.assertNotIn("reference", jobs[0])

    def test_mbpp_official_split(self) -> None:
        def record(task_id: int) -> dict:
            return {"benchmark": "mbpp", "metadata": {"task_id": task_id}, "split": "test"}

        self.assertFalse(is_official_evaluation_task(record(10)))
        self.assertTrue(is_official_evaluation_task(record(11)))
        self.assertTrue(is_official_evaluation_task(record(510)))
        self.assertFalse(is_official_evaluation_task(record(511)))

    def test_safe_task_contains_no_reference_solution(self) -> None:
        record = {
            "id": "x", "benchmark": "humaneval", "interface": "function",
            "starter_code": "def f():", "entrypoint": "f", "metadata": {},
            "reference": {"canonical_solution": "secret"},
        }
        task = safe_task(record, "prompt", "primary")
        self.assertEqual(task["task"], "prompt")
        self.assertNotIn("reference", task)

    def test_plan_is_added_to_task_object(self) -> None:
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": '{"TASK":"solve"}'},
        ]
        updated = with_high_level_plan(messages, "linear scan")
        self.assertEqual(json.loads(updated[-1]["content"])["HIGH_LEVEL_PLAN"], "linear scan")
        self.assertNotIn("HIGH_LEVEL_PLAN", json.loads(messages[-1]["content"]))


if __name__ == "__main__":
    unittest.main()
