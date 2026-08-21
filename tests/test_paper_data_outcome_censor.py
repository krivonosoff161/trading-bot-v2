from __future__ import annotations

import json

import pytest

from scripts.strategy_lab import agent_role_review_cycle
from src.research_lab import outcome_learning, paper_projection_reader
from src.research_lab.paper_signals import cycle, outcome_evidence, store
from src.research_lab.paper_signals.contract import PaperActionSignal
from src.research_lab.paper_signals.training_export import export_training_rows
from src.research_lab.product_signal_training import export_product_signal_training


def _signal(*, status: str = "armed", outcome: dict | None = None) -> PaperActionSignal:
    return PaperActionSignal(
        signal_id="sig-1",
        source="farm",
        symbol="AAA_USDT_SWAP",
        okx_inst_id="AAA-USDT-SWAP",
        timeframe="15m",
        side="long",
        setup_family="continuation",
        entry_zone=[100.0, 101.0],
        stop_loss=98.0,
        invalidation_rule="close below 98",
        take_profit_plan=[{"label": "tp1", "price": 104.0}],
        max_hold_bars=20,
        max_hold_minutes=300,
        reason_now="synthetic paper test",
        status=status,
        created_at=1_000.0,
        expires_at=10_000_000.0,
        boundary_ts=900_000,
        data_fingerprint="fp",
        dedup_key="AAA|15m|continuation",
        outcome=dict(outcome or {}),
    )


class _Raises:
    def fetch_ohlcv(self, *_args):
        raise TimeoutError("synthetic")


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetch_ohlcv(self, *_args):
        return list(self.rows)


def _candles(start: int = 0, count: int = 4) -> list[dict]:
    return [
        {
            "ts": start + index * 900_000,
            "open": 102.0,
            "high": 102.5,
            "low": 101.5,
            "close": 102.0,
        }
        for index in range(count)
    ]


def test_fetch_taxonomy_distinguishes_provider_empty_and_gap():
    failed = cycle._fetch(_Raises(), "AAA_USDT_SWAP", "15m", 9_000_000)
    empty = cycle._fetch(_Rows([]), "AAA_USDT_SWAP", "15m", 9_000_000)
    gap_rows = _candles()
    gap_rows[2]["ts"] += 900_000
    gap = cycle._fetch(_Rows(gap_rows), "AAA_USDT_SWAP", "15m", 9_000_000)

    assert failed.status == outcome_evidence.STATUS_PROVIDER_ERROR
    assert empty.status == outcome_evidence.STATUS_GENUINE_NO_MARKET_DATA
    assert gap.status == outcome_evidence.STATUS_DATA_GAP


def test_operational_outage_preserves_active_signal_and_deduplicates_incident(tmp_path):
    store.append_signal(tmp_path, _signal())

    first = cycle.run_cycle(
        tmp_path,
        provider=_Raises(),
        apply=True,
        now=2_000.0,
        max_new=0,
        require_known_bad_authority=False,
    )
    second = cycle.run_cycle(
        tmp_path,
        provider=_Raises(),
        apply=True,
        now=2_100.0,
        max_new=0,
        require_known_bad_authority=False,
    )

    current = store.load_signals(tmp_path)[0]
    incidents = (tmp_path / "state" / "incidents" / "paper_market_data_incidents.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert current.status == "armed"
    assert current.outcome["market_data_status"] == "provider_error"
    assert current.outcome["market_data_failure_count"] == 2
    assert first["gate_counts"]["observe_provider_error"] == 1
    assert second["gate_counts"]["observe_provider_error"] == 1
    assert len(incidents) == 1
    assert not (tmp_path / "state" / "derived" / "paper_signal_memory.jsonl").exists()


def test_incident_append_is_idempotent_if_signal_update_was_interrupted(tmp_path):
    observation = outcome_evidence.provider_failure()
    cycle._record_data_unavailable(
        tmp_path, _signal(), observation, now=2_000.0, apply=True
    )
    cycle._record_data_unavailable(
        tmp_path, _signal(), observation, now=2_100.0, apply=True
    )

    incidents = (tmp_path / "state" / "incidents" / "paper_market_data_incidents.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(incidents) == 1


@pytest.mark.parametrize(
    ("provider", "expected_status"),
    [
        (_Rows([]), "genuine_no_market_data"),
        (_Rows([_candles()[0], _candles()[2]]), "data_gap"),
    ],
)
def test_successful_empty_or_gapped_data_does_not_close_observation(
    tmp_path, provider, expected_status
):
    store.append_signal(tmp_path, _signal())

    cycle.run_cycle(
        tmp_path,
        provider=provider,
        apply=True,
        now=2_000.0,
        max_new=0,
        require_known_bad_authority=False,
    )

    current = store.load_signals(tmp_path)[0]
    assert current.status == "armed"
    assert current.outcome["market_data_status"] == expected_status


def test_recovery_is_recorded_once_and_observation_resumes(tmp_path):
    signal = _signal()
    store.append_signal(tmp_path, signal)
    cycle.run_cycle(
        tmp_path,
        provider=_Raises(),
        apply=True,
        now=2_000.0,
        max_new=0,
        require_known_bad_authority=False,
    )

    provider = _Rows(_candles(start=1_800_000, count=4))
    cycle.run_cycle(
        tmp_path,
        provider=provider,
        apply=True,
        now=2_100.0,
        max_new=0,
        require_known_bad_authority=False,
    )
    cycle.run_cycle(
        tmp_path,
        provider=provider,
        apply=True,
        now=2_200.0,
        max_new=0,
        require_known_bad_authority=False,
    )

    rows = [
        json.loads(line)
        for line in (tmp_path / "state" / "incidents" / "paper_market_data_incidents.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [row["status"] for row in rows] == ["provider_error", "recovered"]
    assert store.load_signals(tmp_path)[0].outcome["market_data_status"] == "usable"


@pytest.mark.parametrize(
    ("result", "diagnosis"),
    [
        ("provider_error", "data_issue"),
        ("data_gap", "data_issue"),
        ("genuine_no_market_data", "data_issue"),
        ("no_data", "data_issue"),
    ],
)
def test_technical_terminal_rows_are_censored_from_memory_and_training(
    tmp_path, result, diagnosis
):
    signal = _signal(
        status="invalidated",
        outcome={"result": result, "market_data_status": result},
    )
    signal.review = {"diagnosis": diagnosis}
    store.append_signal(tmp_path, signal)

    assert cycle.record_memory(tmp_path, signal) is False
    summary = export_training_rows(tmp_path, force=True)
    assert summary["rows"] == 0
    assert summary["source_terminal_rows_technical_censored"] == 1
    assert (tmp_path / "state" / "derived" / "paper_signal_training.jsonl").read_text(
        encoding="utf-8"
    ) == ""


def test_provider_error_trade_reference_censors_otherwise_market_terminal(tmp_path):
    signal = _signal(status="reviewed", outcome={"result": "take", "net_pct": 1.0})
    signal.review = {"diagnosis": "good_signal"}
    store.append_signal(tmp_path, signal)
    derived = tmp_path / "state" / "derived"
    (derived / "main_paper_trades.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "source_signal_id": signal.signal_id,
                        "status": "provider_error",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    summary = export_training_rows(tmp_path, force=True)

    assert summary["rows"] == 0
    assert summary["referenced_operational_rows_censored"] == 1


def test_legacy_technical_memory_is_not_used_for_family_ranking(tmp_path):
    path = tmp_path / "state" / "derived" / "paper_signal_memory.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "symbol": "AAA_USDT_SWAP",
                "timeframe": "15m",
                "family": "continuation",
                "result": "no_data",
                "diagnosis": "data_issue",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert cycle.load_memory(tmp_path) == []
    assert cycle.learn_known_bad(cycle.load_memory(tmp_path)) == set()


def test_projection_outcome_learning_and_role_review_fail_closed_on_technical_row(tmp_path):
    technical = {
        "training_row_id": "training-sig-1",
        "paper_signal_id": "sig-1",
        "result": "provider_error",
        "diagnosis": "data_issue",
        "paper_only": True,
        "execution_allowed": False,
    }
    generation = {
        "current": True,
        "display_only": False,
        "generation_status": "completed",
        "paper_only": True,
        "execution_allowed": False,
        "paper_generation_run_id": "run-1",
        "account_generation_id": "account-1",
        "paper_subject_generation_ids": ["subject-1"],
        "items": [],
    }

    selected = paper_projection_reader.select_current_terminal_training_rows(
        [technical], generation
    )
    assert selected["eligible_rows"] == 0
    assert selected["rejection_counts"] == {
        "operational_incident_not_training_evidence": 1
    }
    with pytest.raises(ValueError, match="operational incident"):
        outcome_learning.build_outcome_review_pack(technical)
    assert outcome_learning.learning_summary([technical])["operational_incidents_censored"] == 1
    assert agent_role_review_cycle._unreviewed_training_rows(
        tmp_path, [technical], 10
    ) == []


def test_product_training_censors_operational_event_but_preserves_market_event(tmp_path):
    source = tmp_path / "signal_events.jsonl"
    events = [
        {
            "schema": "signal_event.v1",
            "signal_id": "technical",
            "status": "provider_error",
        },
        {
            "schema": "signal_event.v1",
            "signal_id": "market",
            "status": "delivered",
            "symbol": "AAA_USDT_SWAP",
        },
    ]
    source.write_text(
        "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8"
    )

    summary = export_product_signal_training(tmp_path, source_log=source)
    assert summary["rows"] == 1
    assert summary["operational_incidents_censored"] == 1
