from __future__ import annotations

from dataclasses import dataclass
import json

from scripts.strategy_lab import agent_role_review_cycle
from src.research_lab.outcome_promotion_gate import build_outcome_promotion_gate
from src.research_lab.outcome_retest import write_outcome_retest_specs
from src.research_lab.system_analyst_cycle import run_system_analyst_cycle


def _write_jsonl(path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _legacy_training_row() -> dict:
    return {
        "schema": "TrainingRow.v2",
        "training_row_id": "training-legacy",
        "paper_signal_id": "signal-legacy",
        "candidate_id": "candidate-legacy",
        "symbol": "BTC_USDT_SWAP",
        "okx_inst_id": "BTC-USDT-SWAP",
        "timeframe": "15m",
        "family": "momentum_breakout",
        "side": "long",
        "boundary_ts": 1_700_000_000_000,
        "entry_mid": 100.0,
        "stop_loss": 98.0,
        "tp1": 104.0,
        "max_hold_bars": 8,
        "net_pct": -0.5,
        "paper_pnl_usdt": -0.5,
        "diagnosis": "bad_exit_gave_back",
        "paper_only": True,
        "execution_allowed": False,
    }


def _accepted_review() -> dict:
    return {
        "role_id": "outcome_reviewer",
        "review_id": "review-legacy",
        "source_ref": "training-legacy",
        "accepted": True,
        "created_at": "2026-07-11T10:00:00+00:00",
        "payload": {
            "summary": "Synthetic bounded review.",
            "outcome_bucket": "gave_back",
            "actionability": "retest_exit_or_capture",
            "next_test_dimensions": ["earlier_profit_lock"],
        },
        "paper_only": True,
        "execution_allowed": False,
    }


def test_unversioned_training_file_cannot_create_adaptive_side_effects(tmp_path):
    _write_jsonl(
        tmp_path / "state" / "derived" / "paper_signal_training.jsonl",
        [_legacy_training_row()],
    )
    _write_jsonl(
        tmp_path / "state" / "llm_advice" / "outcome_reviews.jsonl",
        [_accepted_review()],
    )

    analyst = run_system_analyst_cycle(
        tmp_path,
        apply=True,
        now="2026-07-11T12:00:00+00:00",
    )
    retests = write_outcome_retest_specs(tmp_path)
    promotion = build_outcome_promotion_gate(tmp_path)

    assert analyst["feedback_candidates_total"] == 0
    assert analyst["routed"] == 0
    assert analyst["accepted_role_requests"] == {
        "farm": 0,
        "validator": 0,
        "trader": 0,
    }
    assert analyst["training_evidence"]["source_rows"] == 1
    assert analyst["training_evidence"]["eligible_rows"] == 0
    assert retests["eligible_total"] == 0
    assert retests["specs"] == 0
    assert promotion["verdicts"] == 0


@dataclass
class _Args:
    private_root: object
    provider: str = "synthetic"
    max_outcomes: int = 1
    max_validator: int = 0
    max_sources: int = 0
    max_analyst: int = 0
    sleep_seconds: float = 0.0


class _UnexpectedProvider:
    name = "synthetic"
    configured = True

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, system, user):
        self.calls += 1
        raise AssertionError("unversioned row reached the provider")


def test_unversioned_training_row_does_not_reach_outcome_provider(
    tmp_path, monkeypatch
):
    _write_jsonl(
        tmp_path / "state" / "derived" / "paper_signal_training.jsonl",
        [_legacy_training_row()],
    )
    provider = _UnexpectedProvider()
    monkeypatch.setattr(
        agent_role_review_cycle,
        "_make_provider",
        lambda _args: provider,
    )

    summary = agent_role_review_cycle.run_cycle(_Args(tmp_path))

    assert provider.calls == 0
    assert summary["inputs"]["outcomes"] == 0
    assert summary["reviews"] == 0
    assert summary["training_evidence"]["source_rows"] == 1
    assert summary["training_evidence"]["eligible_rows"] == 0
