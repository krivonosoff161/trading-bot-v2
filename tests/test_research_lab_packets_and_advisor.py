import json
from types import SimpleNamespace

import pytest

from src.research_lab.calculator_advisor import normalize_advice_payload, request_calculator_advice, validate_advice_payload
from src.research_lab.advisor_sweep_bridge import compile_sweep_proposals, schedule_advisor_sweep_tasks
from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path
from src.research_lab.feature_packet import (
    build_feature_packet,
    build_outcome_feature_packet,
    write_feature_packet,
    write_outcome_feature_packet,
)
from src.research_lab.human_feedback import create_feedback, feedback_summary, record_feedback
from src.research_lab.lineage_backfill import build_lineage_backfill
from src.research_lab.lineage_contract import scanner_event_from_intake, write_cycle_link, write_cycle_links
from src.research_lab.llm_provider import LLMUsage
from src.research_lab.market_data_packet import build_market_data_packet, write_market_data_packet
from src.research_lab.paper_signals.contract import PaperActionSignal
from src.research_lab.paper_signals.store import append_signal
from src.research_lab.pipeline_policy import classify_skip, default_caps
from src.research_lab.prompt_registry import prompt_registry_summary
from src.research_lab.provider_routes import provider_route_summary


def _candles(n: int = 220) -> list[dict]:
    rows = []
    for i in range(n):
        close = 100.0 + i * 0.1
        rows.append(
            {
                "ts": i * 900_000,
                "open": close - 0.05,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "vol": 1000 + i,
            }
        )
    return rows


def test_market_data_packet_live_has_no_future_window(tmp_path):
    packet = build_market_data_packet(
        scanner_event_id="se1",
        symbol="BTC_USDT_SWAP",
        instrument="BTC-USDT-SWAP",
        timeframe="15m",
        mode="live",
        candles=_candles(),
    )

    assert packet.no_lookahead is True
    assert packet.mode == "live"
    assert packet.future_window == []
    assert len(packet.ohlcv_window) == 192
    path = write_market_data_packet(tmp_path, packet)
    assert path.exists()
    index = tmp_path / "state" / "lineage" / "data_packets.jsonl"
    assert packet.data_packet_id in index.read_text(encoding="utf-8")


def test_feature_packet_is_deterministic_and_contains_geometry(tmp_path):
    data_packet = build_market_data_packet(
        scanner_event_id="se1",
        symbol="BTC_USDT_SWAP",
        instrument="BTC-USDT-SWAP",
        timeframe="15m",
        mode="live",
        candles=_candles(),
    )

    one = build_feature_packet(
        data_packet,
        side="long",
        entry_zone=[100.0, 101.0],
        stop_loss=98.0,
        take_profit_plan=[{"label": "tp1", "price": 105.0}],
    )
    two = build_feature_packet(
        data_packet,
        side="long",
        entry_zone=[100.0, 101.0],
        stop_loss=98.0,
        take_profit_plan=[{"label": "tp1", "price": 105.0}],
    )

    assert one.feature_packet_id == two.feature_packet_id
    assert one.geometry["rr_tp1"] > 0
    assert one.no_lookahead is True
    assert "mfe_pct" not in one.features
    path = write_feature_packet(tmp_path, one)
    assert path.exists()


def test_validation_packet_physically_separates_decision_and_outcome_features(tmp_path):
    data_packet = build_market_data_packet(
        scanner_event_id="se1",
        symbol="BTC_USDT_SWAP",
        instrument="BTC-USDT-SWAP",
        timeframe="15m",
        mode="validation",
        candles=_candles(320),
    )
    feature_packet = build_feature_packet(
        data_packet,
        side="long",
        entry_zone=[100.0, 101.0],
        stop_loss=98.0,
        take_profit_plan=[{"label": "tp1", "price": 105.0}],
    )

    outcome_packet = build_outcome_feature_packet(
        data_packet, feature_packet, side="long",
    )

    assert data_packet.future_window
    assert feature_packet.schema == "DecisionFeaturePacket.v1"
    assert feature_packet.no_lookahead is True
    assert "mfe_pct" not in feature_packet.features
    assert outcome_packet is not None
    assert outcome_packet.schema == "OutcomeFeaturePacket.v1"
    assert outcome_packet.outcome_features["mfe_pct"] >= 0
    assert outcome_packet.label_quality["decision_role_eligible"] is False
    assert write_feature_packet(tmp_path, feature_packet).parent.name == "decision"
    assert write_outcome_feature_packet(tmp_path, outcome_packet).parent.name == "outcome"


def test_calculator_advice_rejects_forbidden_trade_fields():
    ok, problems = validate_advice_payload({"situation_class": "late_entry", "entry": 100})
    assert ok is False
    assert "forbidden field: entry" in problems


def test_calculator_advice_normalizes_safe_aliases():
    payload = normalize_advice_payload({"classification": "late_entry", "suggested_dimensions": ["hold"]})

    assert payload == {"situation_class": "late_entry", "sweep_suggestions": ["hold"]}
    ok, problems = validate_advice_payload(payload)
    assert ok is True
    assert problems == []


def test_calculator_advice_normalizes_single_sweep_suggestion_string():
    payload = normalize_advice_payload({"sweep_suggestions": "hold"})

    assert payload == {"sweep_suggestions": ["hold"]}
    ok, problems = validate_advice_payload(payload)
    assert ok is True
    assert problems == []


def test_calculator_advice_normalizes_local_model_shape_without_granting_trade_authority():
    payload = normalize_advice_payload(
        {
            "classification": "late_entry",
            "suggestions": "hold",
            "additional_suggestions": "check slippage",
            "vendor_extra": "ignored",
            "entry": 100,
        }
    )

    assert payload["situation_class"] == "late_entry"
    assert payload["sweep_suggestions"] == ["hold"]
    assert payload["warnings"] == ["check slippage", "dropped_unknown_field:vendor_extra"]
    assert payload["entry"] == 100
    ok, problems = validate_advice_payload(payload)
    assert ok is False
    assert "forbidden field: entry" in problems


def test_calculator_advice_normalizes_missing_and_warning_strings():
    payload = normalize_advice_payload({"missing": "oi", "warnings": "thin sample"})

    assert payload == {"missing_data": ["oi"], "warnings": ["thin sample"]}
    ok, problems = validate_advice_payload(payload)
    assert ok is True
    assert problems == []


def test_calculator_advice_bounds_explicit_user_facing_analysis():
    ok, problems = validate_advice_payload(
        {
            "user_facing_analysis": (
                "Trend context remains uncertain and requires deterministic "
                "confirmation."
            )
        }
    )
    assert ok is True
    assert problems == []

    ok, problems = validate_advice_payload({"user_facing_analysis": "x" * 321})
    assert ok is False
    assert "user_facing_analysis must be 1..320 characters" in problems


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Buy at the next entry level.",
        "Use leverage and place an order.",
        "Target 105 after a long signal.",
        "Открыть лонг с плечом.",
        "Купить сейчас.",
        "Продать актив.",
        "Купи немедленно.",
        "Продавай при движении.",
        "Context at https://example.invalid",
        "<b>Trusted context</b>",
        "First line\nsecond line",
    ],
)
def test_calculator_advice_rejects_order_like_public_model_text(unsafe_text):
    ok, problems = validate_advice_payload({"user_facing_analysis": unsafe_text})

    assert ok is False
    assert problems == ["user_facing_analysis is not an approved statement"]


def test_calculator_advice_normalizes_non_string_list_fields():
    payload = normalize_advice_payload(
        {
            "missing_data": {"field": "oi", "reason": "not available"},
            "warnings": None,
            "sweep_suggestions": {"dimension": "hold_bars"},
        }
    )

    assert payload == {
        "missing_data": [{"field": "oi", "reason": "not available"}],
        "warnings": [],
        "sweep_suggestions": [{"dimension": "hold_bars"}],
    }
    ok, problems = validate_advice_payload(payload)
    assert ok is True
    assert problems == []


def test_calculator_advice_rejects_nested_trade_authority_fields():
    payload = normalize_advice_payload(
        {
            "situation_class": "late_entry",
            "warnings": [{"execution allowed": False}],
            "missing_data": [{"nested": {"Auto-Trade": True}}],
            "sweep_suggestions": [{"TakeProfitPlan": "promote"}],
            "confidence": 0.6,
        }
    )

    ok, problems = validate_advice_payload(payload)

    assert ok is False
    assert any("execution_allowed" in problem for problem in problems)
    assert any("auto_trade" in problem for problem in problems)
    assert any("take_profit_plan" in problem for problem in problems)


class _Provider:
    name = "synthetic"
    configured = True

    def generate(self, system: str, user: str):
        assert "must not set entry" in system
        assert "feature_packet" in user
        return json.dumps({"situation_class": "late_entry", "confidence": 0.4}), LLMUsage(
            provider="synthetic",
            model="offline",
        )


def test_calculator_advice_accepts_bounded_json(tmp_path):
    data_packet = build_market_data_packet(
        scanner_event_id="se1",
        symbol="BTC_USDT_SWAP",
        instrument="BTC-USDT-SWAP",
        timeframe="15m",
        mode="live",
        candles=_candles(),
    )
    feature_packet = build_feature_packet(data_packet)

    advice = request_calculator_advice(tmp_path, feature_packet, _Provider())

    assert advice.accepted is True
    assert advice.execution_allowed is False
    assert advice.advice["situation_class"] == "late_entry"
    assert advice.to_dict()["calculator_advice_id"] == advice.advisor_ref
    assert advice.to_dict()["prompt_version"] == "calculator_advisor_v2_feature_packet_json"
    assert advice.to_dict()["prompt_hash"]
    assert (tmp_path / "state" / "llm_advice" / "calculator_advice.jsonl").exists()


def test_calculator_advice_compiles_bounded_sweep_proposals(tmp_path):
    advice = SimpleNamespace(
        accepted=True,
        advisor_ref="advisor_1",
        feature_packet_id="fp1",
        advice={"sweep_suggestions": ["entry earlier/later", "hold duration", "set entry 100"]},
    )

    summary = compile_sweep_proposals(tmp_path, advice)
    rows = [
        json.loads(line)
        for line in (tmp_path / "state" / "derived" / "calculator_sweep_proposals.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert summary["rows"] == 2
    assert summary["rejected"] == 1
    assert {row["dimension"] for row in rows} == {"entry_timing", "hold"}
    assert all(row["execution_allowed"] is False for row in rows)


def test_calculator_indicator_suggestions_compile_to_regime_filter(tmp_path):
    advice = SimpleNamespace(
        accepted=True,
        advisor_ref="advisor_2",
        feature_packet_id="fp2",
        advice={"sweep_suggestions": ["RSI_14", "trend_atr", "volume_spike"]},
    )

    summary = compile_sweep_proposals(tmp_path, advice)
    rows = [
        json.loads(line)
        for line in (tmp_path / "state" / "derived" / "calculator_sweep_proposals.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert summary["rows"] == 3
    assert summary["rejected"] == 0
    assert {row["dimension"] for row in rows} == {"regime_filter"}
    assert all(row["execution_allowed"] is False for row in rows)


def test_calculator_dict_suggestions_compile_dimensions_without_numeric_levels(tmp_path):
    advice = SimpleNamespace(
        accepted=True,
        advisor_ref="advisor_3",
        feature_packet_id="fp3",
        advice={
            "sweep_suggestions": [
                {
                    "entry_timing": "mid",
                    "family": "up",
                    "hold": 7.0,
                    "regime_filter": "down",
                    "stop": 0.000225,
                    "take_profit": 0.000236,
                    "timeframe": "15m",
                    "trailing": True,
                }
            ]
        },
    )

    summary = compile_sweep_proposals(tmp_path, advice)
    rows = [
        json.loads(line)
        for line in (tmp_path / "state" / "derived" / "calculator_sweep_proposals.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert summary["rows"] == 8
    assert summary["rejected"] == 0
    assert {row["dimension"] for row in rows} == {
        "entry_timing",
        "family",
        "hold",
        "regime_filter",
        "stop",
        "take_profit",
        "timeframe",
        "trailing",
    }
    assert all("0.000" not in row["source_text"] for row in rows)
    assert all(row["execution_allowed"] is False for row in rows)


def test_advisor_sweep_tasks_bind_to_active_paper_signal(tmp_path):
    advice = SimpleNamespace(
        accepted=True,
        advisor_ref="advisor_4",
        feature_packet_id="fp4",
        advice={"sweep_suggestions": ["hold"]},
    )
    compile_sweep_proposals(tmp_path, advice)
    lineage = tmp_path / "state" / "lineage"
    lineage.mkdir(parents=True)
    (lineage / "decision_feature_packets.jsonl").write_text(
        json.dumps({
            "schema": "DecisionFeaturePacketIndex.v1",
            "feature_packet_id": "fp4",
            "symbol": "BTC_USDT_SWAP",
            "instrument": "BTC-USDT-SWAP",
            "timeframe": "1h",
            "paper_only": True,
            "execution_allowed": False,
        }) + "\n",
        encoding="utf-8",
    )
    derived = tmp_path / "state" / "derived"
    (derived / "paper_signals.json").write_text(
        json.dumps({
            "schema": "paper_signals.v1",
            "active": [
                {
                    "signal_id": "sig1",
                    "source": "farm",
                    "symbol": "BTC_USDT_SWAP",
                    "timeframe": "1h",
                    "setup_family": "momentum_breakout",
                    "feature_packet_id": "fp4",
                    "data_fingerprint": "abc123",
                    "status": "armed",
                }
            ],
        }),
        encoding="utf-8",
    )
    tasks = FarmTasksDB(tasks_db_path(tmp_path))

    summary = schedule_advisor_sweep_tasks(tmp_path, tasks, now=1.0)
    queued = tasks.tasks_in_state("queued", task_type="schedule_advisor_sweep")

    assert summary["scheduled"] == 1
    assert summary["deduped"] == 0
    assert queued[0]["symbol"] == "BTC_USDT_SWAP"
    assert queued[0]["family"] == "momentum_breakout"
    payload = json.loads(queued[0]["payload_json"])
    assert payload["proposal"]["dimension"] == "hold"
    assert payload["source_signal"]["signal_id"] == "sig1"
    assert payload["source_signal"]["executable_family"] == "momentum_breakout"
    assert payload["execution_allowed"] is False


def test_advisor_sweep_tasks_map_product_family_to_executable_family(tmp_path):
    advice = SimpleNamespace(
        accepted=True,
        advisor_ref="advisor_5",
        feature_packet_id="fp5",
        advice={"sweep_suggestions": ["take_profit"]},
    )
    compile_sweep_proposals(tmp_path, advice)
    lineage = tmp_path / "state" / "lineage"
    lineage.mkdir(parents=True)
    (lineage / "decision_feature_packets.jsonl").write_text(
        json.dumps({
            "schema": "DecisionFeaturePacketIndex.v1",
            "feature_packet_id": "fp5",
            "symbol": "HMSTR_USDT_SWAP",
            "instrument": "HMSTR-USDT-SWAP",
            "timeframe": "1h",
            "paper_only": True,
            "execution_allowed": False,
        }) + "\n",
        encoding="utf-8",
    )
    derived = tmp_path / "state" / "derived"
    (derived / "paper_signals.json").write_text(
        json.dumps({
            "schema": "paper_signals.v1",
            "active": [
                {
                    "signal_id": "sig5",
                    "source": "farm",
                    "symbol": "HMSTR_USDT_SWAP",
                    "timeframe": "1h",
                    "setup_family": "reversal_fade",
                    "feature_packet_id": "fp5",
                    "data_fingerprint": "market5",
                    "status": "armed",
                }
            ],
        }),
        encoding="utf-8",
    )
    tasks = FarmTasksDB(tasks_db_path(tmp_path))

    summary = schedule_advisor_sweep_tasks(tmp_path, tasks, now=1.0)
    queued = tasks.tasks_in_state("queued", task_type="schedule_advisor_sweep")

    assert summary["scheduled"] == 1
    payload = json.loads(queued[0]["payload_json"])
    assert payload["source_signal"]["setup_family"] == "reversal_fade"
    assert payload["source_signal"]["executable_family"] == "mean_reversion_fade"


def test_pipeline_policy_and_provider_routes_are_public_safe():
    caps = default_caps()
    assert caps.max_candles_per_packet == 512
    assert caps.max_telegram_sends_per_cycle == 0
    assert classify_skip("NEEDS_OI_DATA") == "oi_unavailable"
    summary = provider_route_summary(
        {
            "STRATEGY_LAB_LLM_ENABLED": "1",
            "STRATEGY_LAB_LLM_PROVIDER": "ollama",
            "STRATEGY_LAB_LLM_MODEL_CHEAP": "calculator",
            "LLM_PROVIDER": "alibaba",
        }
    )
    assert summary["secrets_exposed"] is False
    assert any(row["surface"] == "farm_calculator_advisor" and row["provider"] == "ollama" for row in summary["routes"])
    farm_route = next(row for row in summary["routes"] if row["surface"] == "farm_calculator_advisor")
    assert farm_route["prompt_version"] == "calculator_advisor_v2_feature_packet_json"
    prompts = prompt_registry_summary()
    assert prompts["schema"] == "PromptRegistry.v1"
    assert any(row["surface"] == "vip_screenshot" for row in prompts["rows"])


def test_scanner_intake_becomes_scanner_event_v1():
    event = scanner_event_from_intake(
        {
            "event_id": "watch_1",
            "symbol": "BTC_USDT_SWAP",
            "source": "scanner",
            "reason": "watch momentum",
            "observed_at": 1_700_000_000.0,
            "priority": 2,
            "asset_class": "crypto_major",
            "suggested_timeframes": ["15m", "1h"],
            "evidence": {"materiality_score": 0.8, "spread_bps": 3.0},
            "raw_ref": {"watch_id": "w1"},
        },
        mode="live",
    )

    assert event.schema == "ScannerEvent.v1"
    assert event.instrument == "BTC-USDT-SWAP"
    assert event.timeframe == "15m"
    assert event.mode == "live"
    assert event.liquidity["spread_bps"] == 3.0


def test_human_feedback_records_without_execution(tmp_path):
    feedback = create_feedback("bad_card", "telegram_admin", "tgcard_1", note="too terse")
    record_feedback(tmp_path, feedback)
    summary = feedback_summary(tmp_path)

    assert feedback.execution_allowed is False
    assert summary["by_label"] == {"bad_card": 1}


def test_lineage_backfill_writes_mapping_without_rewriting_logs(tmp_path):
    sig = PaperActionSignal(
        signal_id="sig1",
        source="farm",
        symbol="BTC_USDT_SWAP",
        okx_inst_id="BTC-USDT-SWAP",
        timeframe="15m",
        side="long",
        setup_family="early_tp_tactical",
        entry_zone=[100.0, 101.0],
        stop_loss=98.0,
        invalidation_rule="close below local support",
        take_profit_plan=[{"label": "tp1", "price": 105.0, "size_frac": 1.0}],
        max_hold_bars=12,
        max_hold_minutes=180,
        reason_now="fresh pullback",
        status="armed",
    )
    append_signal(tmp_path, sig)

    summary = build_lineage_backfill(tmp_path)
    mapping = tmp_path / "state" / "lineage" / "backfill_mapping.jsonl"

    assert summary["rows"] >= 1
    assert summary["non_destructive"] is True
    assert "sig1" in mapping.read_text(encoding="utf-8")


def test_cycle_link_write_is_idempotent(tmp_path):
    row = {
        "scanner_event_id": "se1",
        "data_packet_id": "mdp1",
        "feature_packet_id": "fp1",
        "paper_signal_id": "sig1",
        "source": "farm",
        "symbol": "BTC_USDT_SWAP",
        "timeframe": "15m",
    }

    write_cycle_link(tmp_path, row)
    write_cycle_link(tmp_path, row)

    lines = (tmp_path / "state" / "lineage" / "cycle_links.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "lineage_link_id" in lines[0]


def test_cycle_link_batch_write_is_idempotent(tmp_path):
    row = {
        "scanner_event_id": "se1",
        "data_packet_id": "mdp1",
        "feature_packet_id": "fp1",
        "paper_signal_id": "sig1",
        "source": "farm",
        "symbol": "BTC_USDT_SWAP",
        "timeframe": "15m",
    }
    second = {**row, "paper_signal_id": "sig2"}

    write_cycle_links(tmp_path, [row, row, second])
    write_cycle_links(tmp_path, [row, second])

    lines = (tmp_path / "state" / "lineage" / "cycle_links.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all("lineage_link_id" in line for line in lines)


def test_farm_loop_calculator_stage_records_disabled_state(tmp_path):
    from scripts.strategy_lab.farm_loop import _run_calculator_advisor_stage

    data_packet = build_market_data_packet(
        scanner_event_id="se1",
        symbol="BTC_USDT_SWAP",
        instrument="BTC-USDT-SWAP",
        timeframe="15m",
        mode="live",
        candles=_candles(),
    )
    write_feature_packet(tmp_path, build_feature_packet(data_packet))
    args = SimpleNamespace(
        calculator_provider="",
        calculator_model="",
        calculator_base_url="",
        calculator_timeout=0.0,
        calculator_advisor_max_calls=1,
        allow_public_output=False,
    )

    result = _run_calculator_advisor_stage(args, tmp_path, apply=True)

    assert result["requested"] == 1
    assert result["eligible"] == 1
    assert result["attempted"] == 1
    assert result["processed"] == 1
    assert result["accepted"] == 0
    assert result["fallback"] == 1
    assert result["blocked"] == 1
    assert result["reason_counts"] == {"provider_not_configured": 1}
    assert result["execution_allowed"] is False
    assert (tmp_path / "state" / "llm_advice" / "calculator_advice.jsonl").exists()
    assert "llm_interpretation_ref" in (
        tmp_path / "state" / "lineage" / "cycle_links.jsonl"
    ).read_text(encoding="utf-8")
