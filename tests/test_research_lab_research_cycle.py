# -*- coding: utf-8 -*-

import json

from scripts.strategy_lab.research_cycle import run_research_cycle
from src.research_lab.candidate_registry import registry_path
from src.research_lab.experiment import ExperimentSpec
from src.research_lab.event_microscope import MicroscopeLimits  # noqa: F401 (kept for clarity)
from src.research_lab.paths import one_minute_data_dir
from src.research_lab.research_cycle import (
    COUNT_KEYS,
    ResearchCycleConfig,
    cycle_report_path,
    cycle_summary,
    read_cycle_report,
)
from src.research_lab.runtime_policy import cadence_path, record_start
from src.research_lab.state_db import connect, default_db_path, ensure_experiment_queued, init_db


# ---- seeding helpers -------------------------------------------------------

def _seed_candidates(root, n=4):
    path = registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    symbols = ["BTC_USDT_SWAP", "ETH_USDT_SWAP", "SOL_USDT_SWAP", "BNB_USDT_SWAP"]
    rows = []
    for i in range(n):
        rows.append({
            "candidate_id": f"c{i}", "symbol": symbols[i % len(symbols)],
            "strategy_id": "momentum_breakout", "params": {"lookback": 20, "hold_bars": 5},
            "validation_status": "FORWARD_PAPER",
            "metrics_summary": {"n_trades": 25, "avg_net_pct": 1.0, "test_avg_net_pct": 0.8,
                                "profit_factor": 1.3, "entry_timing": {"avg_capture_ratio": 0.4}},
            "risk_flags": [], "next_action": "keep", "artifact_label": "experiments/completed/run_x",
            "next_review": "2026-07-01",
        })
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _seed_event_spec(root):
    d = root / "plans" / "event_specs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "evt.json").write_text(json.dumps({
        "experiment_id": "evt1", "symbols": ["BTC_USDT_SWAP"],
        "event_context": {"anchor_symbol": "BTC_USDT_SWAP",
                          "move_start_ts": 1_700_000_000_000, "move_end_ts": 1_700_000_600_000, "direction": "up"},
    }), encoding="utf-8")


def _write_daily_candles(path):
    rows = []
    price = 100.0
    for i in range(80):
        price *= 1.12 if i in {20, 40, 60} else (1.01 if i % 3 else 0.995)
        rows.append({"ts": 1_700_000_000_000 + i * 86_400_000, "date": f"2026-01-{(i % 28) + 1:02d}",
                     "open": round(price * 0.99, 4), "high": round(price * 1.03, 4),
                     "low": round(price * 0.97, 4), "close": round(price, 4), "vol": 1000 + i})
    path.write_text(json.dumps(rows), encoding="utf-8")


def _seed_queued_job(root):
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    _write_daily_candles(data / "ABC_USDT_SWAP_80d.json")
    spec_dir = root / "plans" / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    sp = spec_dir / "unit_cycle_job.json"
    ExperimentSpec(
        experiment_id="unit_cycle_job",
        data_glob=str(data / "{symbol}_*.json"),
        symbols=["ABC_USDT_SWAP"],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{"lookback": 5, "hold_bars": 2}]},
        min_trades=1,
        max_runs=1,
    ).write_json(sp)
    conn = connect(default_db_path(root))
    init_db(conn)
    ensure_experiment_queued(conn, sp.resolve(), priority=80)
    conn.close()


# ---- config model ---------------------------------------------------------

def test_config_defaults_safe():
    c = ResearchCycleConfig()
    assert c.apply is False and c.provider == "null"
    assert c.mode == "dry_run" and c.llm_send_allowed is False and c.network_fetch_allowed is False


def test_network_fetch_only_with_full_optin():
    assert ResearchCycleConfig(apply=True, prepare_1m=True, prepare_1m_apply=True,
                               provider="okx-public").network_fetch_allowed is True
    assert ResearchCycleConfig(apply=True, prepare_1m=True, prepare_1m_apply=True,
                               provider="synthetic").network_fetch_allowed is False  # offline
    assert ResearchCycleConfig(apply=True, prepare_1m=True, prepare_1m_apply=True,
                               provider="null").network_fetch_allowed is False
    assert ResearchCycleConfig(prepare_1m=True, prepare_1m_apply=True,
                               provider="okx-public").network_fetch_allowed is False  # not apply


# ---- dry-run safety -------------------------------------------------------

def test_dry_run_writes_nothing_but_report(tmp_path):
    _seed_candidates(tmp_path)
    result = run_research_cycle(ResearchCycleConfig(apply=False), private_root=tmp_path)
    assert result.mode == "dry_run"
    # report is the only write
    from src.research_lab.research_cycle import write_cycle_report
    write_cycle_report(tmp_path, result)
    assert cycle_report_path(tmp_path).exists()
    # no proposals stored, no queued specs, no worker run, no market data
    assert not (tmp_path / "proposals" / "proposals.jsonl").exists()
    assert not (tmp_path / "proposals" / "queued_specs").exists()
    assert not one_minute_data_dir(tmp_path).exists()
    worker_step = next(s for s in result.steps if s.name == "worker")
    assert worker_step.status == "skipped"
    assert result.counts["proposals_queued"] == 0


def test_prepare_skipped_by_default(tmp_path):
    result = run_research_cycle(ResearchCycleConfig(apply=True, run_worker=False), private_root=tmp_path)
    prep = next(s for s in result.steps if s.name == "prepare_1m")
    assert prep.status == "skipped"
    assert not one_minute_data_dir(tmp_path).exists()


def test_prepare_dry_run_provider_null_no_network(tmp_path):
    _seed_event_spec(tmp_path)
    # prepare enabled but apply=False -> prepare runs dry-run, no fetch, no write
    result = run_research_cycle(
        ResearchCycleConfig(apply=False, prepare_1m=True, prepare_1m_apply=True, provider="null"),
        private_root=tmp_path,
    )
    assert result.counts["data_prepared"] == 0
    assert not one_minute_data_dir(tmp_path).exists()


def test_prepare_apply_provider_null_blocked_clean(tmp_path):
    _seed_event_spec(tmp_path)
    result = run_research_cycle(
        ResearchCycleConfig(apply=True, prepare_1m=True, prepare_1m_apply=True, provider="null", run_worker=False),
        private_root=tmp_path,
    )
    assert result.counts["data_prepared"] == 0  # null provider writes nothing
    assert not list(one_minute_data_dir(tmp_path).glob("*.json")) if one_minute_data_dir(tmp_path).exists() else True


def test_prepare_apply_synthetic_writes(tmp_path):
    _seed_event_spec(tmp_path)
    result = run_research_cycle(
        ResearchCycleConfig(apply=True, prepare_1m=True, prepare_1m_apply=True,
                            provider="synthetic", allow_synthetic=True, run_worker=False),
        private_root=tmp_path,
    )
    assert result.counts["data_prepared"] >= 1
    assert list(one_minute_data_dir(tmp_path).glob("*_1m.json"))


# ---- queue cap + worker ---------------------------------------------------

def test_apply_queues_capped(tmp_path):
    _seed_candidates(tmp_path, n=4)
    result = run_research_cycle(
        ResearchCycleConfig(apply=True, max_queue=2, run_worker=False), private_root=tmp_path,
    )
    assert result.counts["proposals_queued"] <= 2  # cap respected
    queued = list((tmp_path / "proposals" / "queued_specs").glob("*.json")) if (tmp_path / "proposals" / "queued_specs").exists() else []
    assert len(queued) == result.counts["proposals_queued"]


def test_worker_deferred_is_recorded_not_success(tmp_path):
    import time
    _seed_queued_job(tmp_path)
    record_start(cadence_path(tmp_path), time.time())  # recent start -> throttle defers
    result = run_research_cycle(
        ResearchCycleConfig(apply=True, generate_proposals=False, max_worker_jobs=2), private_root=tmp_path,
    )
    assert result.counts["worker_deferred"] >= 1
    assert result.counts["worker_completed"] == 0
    worker_step = next(s for s in result.steps if s.name == "worker")
    assert worker_step.status == "deferred"


def test_worker_runs_at_most_max_jobs(tmp_path):
    _seed_queued_job(tmp_path)
    result = run_research_cycle(
        ResearchCycleConfig(apply=True, generate_proposals=False, max_worker_jobs=1), private_root=tmp_path,
    )
    total = result.counts["worker_completed"] + result.counts["worker_deferred"] + result.counts["worker_failed"]
    assert total <= 1
    assert result.counts["worker_completed"] == 1  # fresh cadence -> the one job runs


def test_cycle_passes_night_mode_to_worker(tmp_path, monkeypatch):
    import scripts.strategy_lab.research_cycle as rc

    captured = {}

    def _fake_worker(private_root, *, allow_public_output=False, night_mode=False):
        captured["night_mode"] = night_mode
        return {"status": "queue_empty"}

    monkeypatch.setattr(rc, "run_worker_once", _fake_worker)
    run_research_cycle(
        ResearchCycleConfig(apply=True, generate_proposals=False, queue=False, night_mode=True),
        private_root=tmp_path,
    )

    assert captured["night_mode"] is True


# ---- result schema + visibility -------------------------------------------

def test_result_json_schema_and_no_abs_paths(tmp_path):
    _seed_candidates(tmp_path)
    result = run_research_cycle(ResearchCycleConfig(apply=False), private_root=tmp_path)
    d = result.to_dict()
    assert d["schema"] == "strategy_lab_research_cycle.v1"
    assert set(COUNT_KEYS) <= set(d["counts"])
    assert d["safety"]["no_live_trading"] is True and d["safety"]["llm_send"] is False
    assert {s["name"] for s in d["steps"]} >= {"inspect", "generate_proposals", "data_check", "queue_proposals", "worker"}
    assert str(tmp_path) not in json.dumps(d)


def test_queue_readiness_gate_skips_not_ready(tmp_path, monkeypatch):
    import scripts.strategy_lab.queue_validated_proposals as q
    from src.research_lab.data_readiness import MISSING_DATA, ProposalReadiness
    from src.research_lab.proposal_schema import VALIDATED, coerce_proposal
    from src.research_lab.proposal_store import proposals_path, upsert_proposals

    p = coerce_proposal({
        "proposal_id": "p1", "created_by": "rule_based", "hypothesis": "h",
        "requested_timeframe": "1d", "setup_family": "momentum_breakout",
        "symbols": ["BTC_USDT_SWAP"], "parameter_grid": {
            "momentum_breakout": [{"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16}]},
        "status": VALIDATED, "created_at": "2026-06-13T00:00:00+00:00",
    })
    upsert_proposals(proposals_path(tmp_path), [p])
    # force "data not ready" deterministically (no dependence on the archive)
    monkeypatch.setattr(q, "assess_proposal", lambda *a, **k: ProposalReadiness(MISSING_DATA, (), "prep cmd"))

    res = q.queue_validated(tmp_path, apply=True, require_data_ready=True)
    assert res["queued"] == 0
    assert res["skipped_missing_data"] == 1
    assert res["skipped_not_ready"][0]["status"] == MISSING_DATA


def test_cycle_records_step_failure_without_crashing(tmp_path, monkeypatch):
    import scripts.strategy_lab.research_cycle as rc

    def _boom(*a, **k):
        raise RuntimeError("generator exploded")

    monkeypatch.setattr(rc, "generate_proposals", _boom)
    result = run_research_cycle(ResearchCycleConfig(apply=False), private_root=tmp_path)  # must not raise
    gen = next(s for s in result.steps if s.name == "generate_proposals")
    assert gen.status == "failed" and "proposal_error" in gen.detail
    # the rest of the cycle still ran
    assert any(s.name == "queue_proposals" for s in result.steps)


def test_cycle_summary_and_dashboard_include_last_cycle(tmp_path, monkeypatch):
    from src.research_lab.dashboard_state import load_dashboard_state
    from src.research_lab.research_cycle import write_cycle_report
    monkeypatch.setattr("src.research_lab.dashboard_state.SCOUT_BUDGET_LOG", tmp_path / "missing.jsonl")
    result = run_research_cycle(ResearchCycleConfig(apply=False), private_root=tmp_path)
    write_cycle_report(tmp_path, result)

    summary = cycle_summary(read_cycle_report(tmp_path))
    assert summary["available"] is True and summary["mode"] == "dry_run"

    state = load_dashboard_state(tmp_path)
    assert state["last_cycle"]["available"] is True
    assert str(tmp_path) not in json.dumps(state)
