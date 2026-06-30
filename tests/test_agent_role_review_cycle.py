import json

from scripts.strategy_lab.agent_role_review_cycle import run_cycle
from src.research_lab.llm_provider import LLMUsage


class _Provider:
    name = "fake"
    configured = True

    def generate(self, system, user):
        data = json.loads(user)
        role = data["role_id"]
        if role == "outcome_reviewer":
            payload = {
                "summary": "Loss gave back after favourable move.",
                "diagnosis": "bad_exit_gave_back",
                "confidence": 0.7,
                "next_test_dimensions": ["partial_be"],
            }
        elif role == "validator_reviewer":
            payload = {
                "summary": "Too few trades for a hard verdict.",
                "validator_class": "underpowered",
                "failure_mode": "thin_sample",
                "confidence": 0.8,
            }
        else:
            payload = {
                "summary": "Source needs later outcome confirmation.",
                "source_class": "unknown_trust",
                "trust_delta": 0,
                "confidence": 0.6,
            }
        return json.dumps(payload), LLMUsage(provider="fake", model="fake-json")


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_agent_role_review_cycle_uses_real_private_artifacts(monkeypatch, tmp_path):
    _write_jsonl(
        tmp_path / "state" / "derived" / "paper_signal_training.jsonl",
        [
            {
                "training_row_id": "training_1",
                "symbol": "BTC_USDT_SWAP",
                "timeframe": "15m",
                "family": "early_tp_tactical",
                "diagnosis": "bad_exit_gave_back",
                "net_pct": -1.2,
                "mfe_pct": 2.0,
                "paper_only": True,
                "execution_allowed": False,
            }
        ],
    )
    memory_path = tmp_path / "state" / "derived" / "setup_outcome_memory.json"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "symbol": "BTC_USDT_SWAP",
                        "timeframe": "15m",
                        "family": "momentum_breakout",
                        "outcome_class": "INSUFFICIENT_DATA",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    _write_jsonl(
        tmp_path / "state" / "lineage" / "scanner_events.jsonl",
        [
            {
                "scanner_event_id": "se_1",
                "symbol": "BTC_USDT_SWAP",
                "timeframe": "15m",
                "source": "farm",
                "reason": "test",
            }
        ],
    )

    monkeypatch.setattr("scripts.strategy_lab.agent_role_review_cycle._make_provider", lambda args: _Provider())
    args = type(
        "Args",
        (),
        {
            "private_root": tmp_path,
            "provider": "fake",
            "max_outcomes": 1,
            "max_validator": 1,
            "max_sources": 1,
            "sleep_seconds": 0,
        },
    )()
    summary = run_cycle(args)

    assert summary["reviews"] == 3
    assert summary["accepted"] == 3
    assert summary["execution_allowed"] is False
    assert (tmp_path / "reports" / "agent_role_review_cycle" / "summary.json").exists()
