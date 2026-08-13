"""Deterministic product proof from scanner hand-off through paper delivery.

The test uses only temporary storage, deterministic validation/model/public-data
adapters and a recording Telegram transport.  Production orchestration, fenced
DBs, immutable generation artifacts, Paper v2, outbox ACK, training and analyst
persistence remain real; focused bridge tests separately prove the statistical
validator implementation and its fail-closed evidence ceiling.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

from scripts.strategy_lab.farm_loop import (
    _generation_feature_packet_ids,
    _run_calculator_advisor_stage,
)
from src.research_lab.farm_coordinator import run_coordinator_cycle
from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path
from src.research_lab.intake_adapter import watch_to_intake
from src.research_lab.lineage_contract import utc_now
from src.research_lab.llm_provider import LLMUsage
from src.research_lab.llm_role_reviews import request_role_review
from src.research_lab.ownership import ProcessIdentity
from src.research_lab.paper_evidence_store import PaperEvidenceStore
from src.research_lab.paper_generation_run import run_paper_generation_v2
from src.research_lab.paper_signals.cycle import run_cycle as run_paper_signal_cycle
from src.research_lab.paper_signals.store import load_signals
from src.research_lab.paper_signals.training_export import export_training_rows
from src.research_lab.paper_telegram_preview import build_paper_telegram_preview
from src.research_lab.paper_telegram_sender import send_paper_telegram_previews
from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.system_analyst_cycle import (
    outcome_review_source_binding,
    run_system_analyst_cycle,
)
from src.research_lab.timeframes import load_timeframe_profiles
from src.research_lab.validation_generation import load_current_generation_snapshot
from src.research_lab.validation_orchestrator import run_due_validations


_PROFILES = load_timeframe_profiles()
_POLICY = load_resource_policy()
_NOW = 1_700_000_000.0


class _PublicProvider:
    name = "synthetic-public-e2e"

    def __init__(self) -> None:
        self.terminal_signal = None

    def fetch_ohlcv(self, _symbol, timeframe, start_ts, end_ts):
        if self.terminal_signal is not None:
            signal = self.terminal_signal
            entry = max(signal.entry_zone) if signal.side == "long" else min(signal.entry_zone)
            target = float(signal.take_profit_plan[-1]["price"])
            first_ts = max(int(start_ts), int(signal.boundary_ts) + 1)
            second_ts = first_ts + 3_600_000
            if signal.side == "long":
                rows = [
                    {
                        "ts": first_ts,
                        "open": entry,
                        "high": entry * 1.001,
                        "low": min(signal.entry_zone) * 0.999,
                        "close": entry,
                        "vol": 1_000.0,
                    },
                    {
                        "ts": second_ts,
                        "open": entry,
                        "high": target * 1.01,
                        "low": entry,
                        "close": target,
                        "vol": 1_001.0,
                    },
                ]
            else:
                rows = [
                    {
                        "ts": first_ts,
                        "open": entry,
                        "high": entry * 1.001,
                        "low": entry * 0.999,
                        "close": entry,
                        "vol": 1_000.0,
                    },
                    {
                        "ts": second_ts,
                        "open": entry,
                        "high": entry,
                        "low": target * 0.99,
                        "close": target,
                        "vol": 1_001.0,
                    },
                ]
            return [row for row in rows if start_ts <= row["ts"] <= end_ts]

        step = 3_600_000 if timeframe == "1h" else 900_000
        first_ts = max(int(start_ts), int(end_ts) - step * 199)
        rows = []
        for index in range(200):
            ts = first_ts + index * step
            price = 100.0 + index * 0.1
            rows.append(
                {
                    "ts": ts,
                    "open": price,
                    "high": price * 1.02,
                    "low": price * 0.98,
                    "close": price * 1.01,
                    "vol": 1_000.0 + index,
                }
            )
        return [row for row in rows if start_ts <= row["ts"] <= end_ts]


class _AdviceProvider:
    name = "ollama"
    model_name = "calculator-swarm"
    configured = True
    base_url = "http://127.0.0.1:11434/v1"

    def generate(self, system: str, _user: str):
        if "situation_class" in system:
            payload = {
                "situation_class": "trend",
                "missing_data": [],
                "confidence": 0.8,
                "warnings": [],
            }
        elif "user_facing_analysis" in system:
            payload = {
                "advisory_reason": "bounded synthetic reason",
                "user_facing_analysis": (
                    "Trend context remains uncertain and requires deterministic "
                    "confirmation."
                ),
                "sweep_suggestions": ["hold"],
                "confidence": 0.7,
                "warnings": [],
            }
        else:
            payload = {
                "proposal_quality": "accept",
                "rejection_reason": "",
                "confidence": 0.7,
                "warnings": [],
            }
        return json.dumps(payload), LLMUsage(
            provider=self.name,
            model=self.model_name,
        )


class _OutcomeReviewProvider:
    name = "ollama"
    model_name = "outcome-reviewer"
    configured = True

    def generate(self, _system: str, _user: str):
        return json.dumps(
            {
                "summary": "Synthetic bounded outcome review.",
                "review_kind": "win",
                "outcome_bucket": "clean_capture",
                "actionability": "retain_and_retest",
                "diagnosis": "good_signal",
                "confidence": 0.7,
                "learning_tags": ["synthetic_e2e"],
                "next_test_dimensions": ["hold"],
            }
        ), LLMUsage(provider=self.name, model=self.model_name)


def _seed_candles(root: Path) -> None:
    target = root / "market_data" / "1h"
    target.mkdir(parents=True)
    start = 1_700_000_000_000
    rows = []
    for index in range(200):
        ts = start + index * 3_600_000
        price = 100.0 + 10.0 * math.sin(index / 7.0) + index * 0.05
        rows.append(
            {
                "ts": ts,
                "date": str(ts),
                "open": price,
                "high": price * 1.02,
                "low": price * 0.98,
                "close": price * (1.01 if index % 5 else 0.99),
                "vol": 1_000.0 + index,
            }
        )
    end = rows[-1]["ts"]
    (target / f"AAA_USDT_SWAP_{start}_{end}_1h.json").write_text(
        json.dumps(rows), encoding="utf-8"
    )


def _scanner_intake() -> dict:
    watch = {
        "schema": "ScoutWatch.v1",
        "watch_id": "watch_product_e2e",
        "card_id": "card_product_e2e",
        "status": "open",
        "created_at": "2023-11-14T22:13:20Z",
        "scanner": {
            "verdict": "GO",
            "normalized_side": "buy",
            "event_type": "synthetic_public_event",
            "escalation_gate": "CHIEF_GO",
            "materiality_score": 0.9,
            "agent_confidence": 0.8,
        },
        "asset": {
            "symbol": "AAA",
            "okx_inst": "AAA-USDT-SWAP",
            "okx_asset_class": "crypto_major",
        },
        "farm": {"eligible": True, "selected_timeframe": "1h"},
        "trigger": {"source": "synthetic_public_scanner"},
        "levels": {},
        "execution_allowed": False,
    }
    event = watch_to_intake(watch)
    assert event is not None
    return event


def _paper_store(root: Path):
    store = PaperEvidenceStore(root / "paper-evidence.sqlite3", clock=lambda: 100.0)
    store.activate()
    lease = store.acquire_writer(
        owner_id="synthetic-product-e2e",
        identity=ProcessIdentity(101, 101.0, "python-test.exe", "sha256:test"),
        lease_seconds=30.0,
    )
    account = store.create_account_genesis(
        lease,
        {
            "currency": "USDT",
            "deposit": 70.0,
            "leverage": 3.0,
            "position_margin": 35.0,
            "allocation_policy": "one-primary-per-scenario.v1",
            "cost_policy": "net-pct-cost-inclusive.v1",
            "rounding_policy": "integer-microunits-half-even.v1",
            "method": "paper-account.v2",
        },
    )
    return store, lease, account


def test_scanner_to_acked_card_training_and_analyst_is_replay_safe(
    tmp_path, monkeypatch
):
    _seed_candles(tmp_path)
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    evidence_store = None
    try:
        farm = run_coordinator_cycle(
            tasks,
            private_root=tmp_path,
            profiles=_PROFILES,
            policy=_POLICY,
            intake_events=[_scanner_intake()],
            families=("momentum_breakout",),
            provider=None,
            apply=True,
            now=_NOW,
            run_worker=True,
            max_worker_jobs=4,
            backend="cpu",
        )
        assert farm["counters"]["events_ingested"] == 1
        assert farm["counters"]["runs_completed"] == 1
        assert farm["counters"]["exports_created"] == 6

        # The statistical bridge is independently covered with actual
        # untouched/search-family/dependence evidence.  This product E2E fixes
        # that boundary to a deterministic accepted validator so the remaining
        # orchestration is reproducible and causally linked.
        monkeypatch.setattr(
            "src.research_lab.honest_backtest_bridge._run_all_checks",
            lambda *_args, **_kwargs: [
                {
                    "check_name": "synthetic_product_e2e_validator",
                    "passed": True,
                    "details": {"boundary": "deterministic_test_adapter"},
                    "message": "schema-valid synthetic validator boundary",
                }
            ],
        )
        validated = run_due_validations(
            tasks, tmp_path, apply=True, limit=6, now=_NOW + 100
        )
        generation = load_current_generation_snapshot(tmp_path)
        assert validated["validated"] == 6
        assert generation.status == "ready"

        public_provider = _PublicProvider()
        paper = run_paper_signal_cycle(
            tmp_path,
            mode="live",
            timeframes=("1h",),
            max_new=1,
            apply=True,
            provider=public_provider,
            now=_NOW + 200,
            pfr_db_path=tmp_path / "state" / "strategy_lab.sqlite",
            max_pfr_scan=10,
            max_pfr_fetches=3,
            pfr_reserved_new=1,
            max_live_fetches=0,
            max_network_fetches=4,
            max_wall_seconds=20,
        )
        signals = load_signals(tmp_path)
        assert paper["generated"] == 1, json.dumps(
            {
                "paper": paper,
                "validated": validated,
                "verdicts": {
                    candidate_id: payloads["verdict"][1]
                    for candidate_id, payloads in generation.payloads.items()
                },
            },
            sort_keys=True,
            default=str,
        )
        assert len(signals) == 1
        public_provider.terminal_signal = signals[0]

        evidence_store, lease, account = _paper_store(tmp_path)
        generated = run_paper_generation_v2(
            tmp_path,
            store=evidence_store,
            lease=lease,
            account_generation_id=account,
            provider=public_provider,
            producer_id="synthetic-product-e2e",
            producer_sequence=1,
            code_identity="sha256:synthetic-product-e2e",
            producer_method_identity="synthetic-product-e2e.v1",
            simulator_manifest_id="simulator-synthetic-product-e2e",
            lifecycle_method_identity="paper-lifecycle.v2",
            required_validation_generation_id=generation.generation_id,
            now_ms=int((_NOW + 8_000) * 1_000),
        )
        run_id = generated["run_id"]
        assert generated["producer_membership"]["validation_bound_members"] == 1
        assert generated["observer"]["reviewed"] == 1, generated["observer"]

        feature_ids = _generation_feature_packet_ids(
            generated["queue"], expected_run_id=run_id, private_root=tmp_path
        )
        monkeypatch.setattr(
            "src.research_lab.llm_provider.load_provider",
            lambda _env: _AdviceProvider(),
        )
        advice = _run_calculator_advisor_stage(
            SimpleNamespace(
                calculator_provider="synthetic",
                calculator_model="offline",
                calculator_base_url="",
                calculator_timeout=1.0,
                calculator_advisor_max_calls=1,
                allow_public_output=False,
            ),
            tmp_path,
            True,
            feature_packet_ids=feature_ids,
        )
        assert advice["attempted"] == 1
        assert advice["accepted"] == 1, json.dumps(advice, sort_keys=True)
        assert advice["fallback"] == 0

        preview = build_paper_telegram_preview(
            tmp_path,
            fetch_public_chart_candles=False,
            evidence_database_path=evidence_store.path,
        )
        assert len(preview["items"]) == 1
        rendered = str(preview["items"][0]["text"])
        assert "Trend context remains uncertain" in rendered

        sent: list[str] = []

        async def send_text(_recipient: str, text: str) -> int:
            sent.append(text)
            return 101

        async def send_photo(_recipient: str, _image: bytes, caption: str) -> int:
            sent.append(caption)
            return 101

        first_delivery = send_paper_telegram_previews(
            tmp_path,
            apply=True,
            paper_chat_configured=True,
            paper_chat_ids_count=1,
            recipient_ids=["synthetic-test-recipient"],
            send_text=send_text,
            send_photo=send_photo,
            expected_generation_run_id=run_id,
        )
        replay_delivery = send_paper_telegram_previews(
            tmp_path,
            apply=True,
            paper_chat_configured=True,
            paper_chat_ids_count=1,
            recipient_ids=["synthetic-test-recipient"],
            send_text=send_text,
            send_photo=send_photo,
            expected_generation_run_id=run_id,
        )
        assert first_delivery["sent_messages"] == 1, json.dumps(
            first_delivery, sort_keys=True
        )
        assert replay_delivery["sent_messages"] == 0
        assert len(sent) == 1

        training = export_training_rows(
            tmp_path, force=True, evidence_database_path=evidence_store.path
        )
        training_payload = json.loads(
            (tmp_path / "state" / "derived" / "paper_signal_training.json").read_text(
                encoding="utf-8"
            )
        )
        row = training_payload["items"][0]
        assert training["rows"] == 1
        assert row["immutable_terminal_evidence"] is True

        outcome_review = request_role_review(
            tmp_path,
            role_id="outcome_reviewer",
            source_ref=row["training_row_id"],
            source_payload={
                "schema": "OutcomeLearningCase.v1",
                "paper_only": True,
                "execution_allowed": False,
            },
            provider=_OutcomeReviewProvider(),
            source_binding=outcome_review_source_binding(row),
        )
        assert outcome_review.accepted is True
        analyst_now = utc_now()
        analyst = run_system_analyst_cycle(
            tmp_path,
            apply=True,
            now=analyst_now,
            expected_generation_run_id=run_id,
            evidence_database_path=evidence_store.path,
        )
        analyst_state = tmp_path / "state"
        feedback_ledger = (
            analyst_state / "system_analyst_feedback" / "ledger.jsonl"
        )
        assert analyst["routed"] == 1, json.dumps(analyst, sort_keys=True)
        feedback_before_replay = feedback_ledger.read_bytes()
        role_state_before_replay = {
            path.relative_to(analyst_state).as_posix(): path.read_bytes()
            for root_name in ("role_requests", "role_environments")
            for path in sorted((analyst_state / root_name).rglob("*"))
            if path.is_file()
        }
        replay_analyst = run_system_analyst_cycle(
            tmp_path,
            apply=True,
            now=analyst_now,
            expected_generation_run_id=run_id,
            evidence_database_path=evidence_store.path,
        )
        # ``routed`` counts accepted route candidates.  The durable router is
        # idempotent: replaying that candidate must not append another feedback
        # event or materialize another role request/environment.
        assert replay_analyst["routed"] == 1
        assert feedback_ledger.read_bytes() == feedback_before_replay
        assert {
            path.relative_to(analyst_state).as_posix(): path.read_bytes()
            for root_name in ("role_requests", "role_environments")
            for path in sorted((analyst_state / root_name).rglob("*"))
            if path.is_file()
        } == role_state_before_replay

        assert tasks.pending_materializations() == []
        assert evidence_store.current_run_id() == run_id
    finally:
        if evidence_store is not None:
            evidence_store.close()
        tasks.close()
