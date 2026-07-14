from __future__ import annotations

import unittest

from src.research_lab.local_model_context import _public_corpus, build_local_model_context
from src.research_lab.local_model_eval import evaluate_role_output


class LocalModelGovernanceTest(unittest.TestCase):
    def test_public_rag_corpus_is_reused_within_process(self) -> None:
        _public_corpus.cache_clear()
        build_local_model_context("calculator", query="first")
        first = _public_corpus.cache_info()
        build_local_model_context("validator", query="second")
        second = _public_corpus.cache_info()

        self.assertEqual(first.misses, 1)
        self.assertEqual(second.misses, 1)
        self.assertGreater(second.hits, first.hits)

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
