# -*- coding: utf-8 -*-
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout import source_quality_report as SQR  # noqa: E402


def test_source_quality_summary_aggregates_phase_and_drop_metrics():
    report = SQR.summarize(
        ingest_rows=[
            {"source": "decrypt"},
            {"source": "decrypt"},
            {"source": "sec_edgar"},
        ],
        drop_rows=[
            {"source": "decrypt", "drop_reason": "context_commentary"},
            {"source": "decrypt", "drop_reason": "stale_article"},
        ],
        journal_rows=[
            {
                "source": "decrypt",
                "verdict": "NO_GO",
                "event_phase": "REALIZED",
                "lead_class": "LAGGING",
                "layer": 2,
                "low_confidence": True,
                "chief_called": False,
            },
            {
                "source": "sec_edgar",
                "verdict": "GO",
                "event_phase": "REALIZED",
                "lead_class": "LEADING",
                "layer": 5,
                "low_confidence": False,
                "chief_called": True,
            },
        ],
        routing_rows=[
            {
                "source": "decrypt",
                "source_phase_prior": "mixed",
                "headline_phase": "FUTURE",
                "final_phase": "REALIZED",
                "layer": 2,
                "skipped": "context_commentary",
            },
            {
                "source": "sec_edgar",
                "source_phase_prior": "realized",
                "headline_phase": "REALIZED",
                "final_phase": "REALIZED",
                "layer": 5,
                "chief_called": True,
                "verdict": "GO",
            },
        ],
    )

    decrypt = report["sources"]["decrypt"]
    sec = report["sources"]["sec_edgar"]

    assert decrypt["ingested"] == 2
    assert decrypt["drops"]["context_commentary"] == 1
    assert decrypt["routing_skips"]["context_commentary"] == 1
    assert decrypt["headline_vs_final"]["FUTURE->REALIZED"] == 1
    assert decrypt["low_confidence"] == 1
    assert sec["chief_called"] == 1
    assert sec["phase_prior_vs_headline"]["REALIZED->REALIZED"] == 1


def test_source_quality_render_text_includes_source_rows():
    text = SQR.render_text(
        {
            "totals": {"sources": 1, "ingested": 2, "cards": 1, "chief_called": 1, "routing_attempts": 2},
            "sources": {
                "decrypt": {
                    "ingested": 2,
                    "cards": 1,
                    "card_rate": 0.5,
                    "chief_called": 1,
                    "chief_rate": 1.0,
                    "low_confidence": 0,
                    "low_confidence_rate": 0.0,
                    "verdicts": {"NO_GO": 1},
                    "phases": {"REALIZED": 1},
                    "lead_classes": {"LAGGING": 1},
                    "drops": {"stale_article": 1},
                    "routing_attempts": 2,
                    "routing_skips": {"stale_article": 1},
                    "phase_prior_vs_headline": {},
                    "headline_vs_final": {"REALIZED->REALIZED": 1},
                    "layer_hits": {"2": 1},
                }
            },
        }
    )
    assert "[decrypt]" in text
    assert "stale_article" in text
    assert "REALIZED->REALIZED" in text
