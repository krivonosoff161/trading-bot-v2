from __future__ import annotations

import unittest

from src.research_lab.local_model_context import build_local_model_context
from src.research_lab.local_model_eval import evaluate_role_output


class LocalModelGovernanceTest(unittest.TestCase):
    def test_context_is_versioned_and_unsafe_output_fails(self) -> None:
        context = build_local_model_context(
            "calculator_context_classifier", "paper only missing data",
        )
        self.assertEqual(len(context["toolset_hash"]), 64)
        self.assertEqual(len(context["rag_manifest_hash"]), 64)
        self.assertTrue(context["tools"])
        self.assertTrue(context["retrieved_chunks"])
        self.assertFalse(context["execution_allowed"])

        accepted = evaluate_role_output(
            "calculator_context_classifier",
            {
                "situation_class": "trend",
                "missing_data": [],
                "confidence": 0.8,
                "warnings": [],
            },
        )
        rejected = evaluate_role_output(
            "calculator_context_classifier",
            {"side": "long", "confidence": 2},
        )
        self.assertTrue(accepted["passed"])
        self.assertFalse(rejected["passed"])


if __name__ == "__main__":
    unittest.main()
