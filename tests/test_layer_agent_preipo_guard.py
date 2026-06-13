# -*- coding: utf-8 -*-
import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.agents import layer_agent as L  # noqa: E402


def test_l5_preipo_market_mechanics_promoted_to_watch(monkeypatch):
    async def fake_call(*args, **kwargs):
        return json.dumps({
            "asset": "SPACEX",
            "event_type": "unknown",
            "phase": "ambiguous",
            "direction": "none",
            "materiality": 0.0,
            "confidence": 0.0,
            "key_facts": [],
            "numbers": [],
            "veto_flags": [],
            "no_edge_flags": ["generic IPO chatter"],
            "mechanics": [],
            "pre_verdict": "DROP",
            "should_escalate": False,
            "escalation_reason": "",
            "no_go_reason": "generic",
            "trigger_text": "",
            "suggested_horizon_hours": 24,
            "reason_to_escalate": "",
        }), {"role": "cheap", "total_tokens": 10}

    monkeypatch.setattr(L.llm_client, "call", fake_call)
    out = asyncio.run(L.analyze(
        {
            "headline": "SpaceX IPO: Whale Opens $22.3M SPCX Long as Synthetic Price Hits 30% premium",
            "text": "",
            "date": "2026-06-12",
        },
        5,
        "SPACEX",
    ))

    assert out["event_type"] == "pre_ipo_market_mechanics"
    assert out["materiality"] >= 0.65
    assert out["confidence"] >= 0.55
    assert out["pre_verdict"] == "WATCH_CANDIDATE"
    assert out["should_escalate"] is True
    assert out["trigger_text"]


def test_l5_generic_ipo_explainer_is_not_promoted(monkeypatch):
    async def fake_call(*args, **kwargs):
        return json.dumps({
            "asset": "SPACEX",
            "event_type": "unknown",
            "phase": "context",
            "direction": "none",
            "materiality": 0.0,
            "confidence": 0.0,
            "key_facts": [],
            "numbers": [],
            "veto_flags": [],
            "no_edge_flags": ["generic explainer"],
            "mechanics": [],
            "pre_verdict": "DROP",
            "should_escalate": False,
            "escalation_reason": "",
            "no_go_reason": "generic",
            "trigger_text": "",
            "suggested_horizon_hours": 24,
            "reason_to_escalate": "",
        }), {"role": "cheap", "total_tokens": 10}

    monkeypatch.setattr(L.llm_client, "call", fake_call)
    out = asyncio.run(L.analyze(
        {
            "headline": "The SpaceX IPO is Coming Soon. What Does It Mean for Crypto?",
            "text": "",
            "date": "2026-06-07",
        },
        5,
        "SPACEX",
    ))

    assert out["pre_verdict"] == "DROP"
    assert out["materiality"] == 0.0
