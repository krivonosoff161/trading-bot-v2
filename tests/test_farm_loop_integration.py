# -*- coding: utf-8 -*-
"""End-to-end: intake -> plan -> run_sweep -> worker compute -> sync -> classify (real compute),
plus validation auto stamp-back, storage bounds, and the no-live-trading boundary."""
import json
import math
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab.farm_coordinator import run_coordinator_cycle  # noqa: E402
from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402

PROFILES = load_timeframe_profiles()
POLICY = load_resource_policy()
_HOUR_MS = 3_600_000


def _write_1h_candles(private_root: Path, symbol: str, n: int = 200) -> None:
    d = private_root / "market_data" / "1h"
    d.mkdir(parents=True, exist_ok=True)
    start = 1_700_000_000_000
    rows = []
    for i in range(n):
        ts = start + i * _HOUR_MS
        base = 100.0 + 10.0 * math.sin(i / 7.0) + (i * 0.05)  # drift + waves -> some breakouts
        rows.append({"ts": ts, "date": str(ts), "open": base, "high": base * 1.02,
                     "low": base * 0.98, "close": base * (1.01 if i % 5 else 0.99), "vol": 1000.0 + i})
    end = start + (n - 1) * _HOUR_MS
    (d / f"{symbol}_{start}_{end}_1h.json").write_text(json.dumps(rows), encoding="utf-8")


def _event(symbol):
    return {"event_id": f"{symbol}:test", "symbol": symbol, "source": "okx_announcement",
            "reason": "listing", "observed_at": 1000.0, "priority": 2, "asset_class": "crypto_major",
            "suggested_timeframes": ["1h"], "evidence": {}, "raw_ref": {}}


def test_end_to_end_real_compute_closes_the_loop(tmp_path):
    symbol = "AAA-USDT-SWAP"
    _write_1h_candles(tmp_path, "AAA_USDT_SWAP")
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    out = run_coordinator_cycle(
        tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY, intake_events=[_event(symbol)],
        families=("momentum_breakout",), provider=None, apply=True, now=2000.0,
        run_worker=True, max_worker_jobs=4, backend="cpu",
    )
    c = out["counters"]
    assert c["sweeps_materialized"] >= 1          # the 1h run_sweep materialized into the compute queue
    assert c["runs_completed"] >= 1               # the worker actually computed it (real compute)
    assert c["classified"] >= 1 and c["unique_upserted"] >= 1
    latest = tasks.latest_unique_candidates()
    assert latest and latest[0]["symbol"] == "AAA_USDT_SWAP" and latest[0]["timeframe"] == "1h"
    assert latest[0]["data_fingerprint"] and latest[0]["data_fingerprint"] != "nofp"
    # the completed run_sweep is recorded; re-running the SAME data does NOT recompute (no saturation)
    out2 = run_coordinator_cycle(
        tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY, intake_events=[_event(symbol)],
        families=("momentum_breakout",), provider=None, apply=True, now=2100.0,
        run_worker=True, max_worker_jobs=4, backend="cpu",
    )
    assert out2["counters"]["sweeps_materialized"] == 0   # identical fingerprint -> not re-armed
    assert out2["pivot"] in ("blocked:no_eligible_tasks", "work_available", "advanced_lifecycle")
    tasks.close()


def test_prepare_chains_to_run_sweep_in_one_cycle(tmp_path):
    # data MISSING initially -> prepare fetches it -> run_sweep is planned + computed same cycle
    from src.research_lab.market_data_provider import get_provider
    provider = get_provider("synthetic", allow_synthetic=True)
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    out = run_coordinator_cycle(
        tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY,
        intake_events=[_event("ZZZ-USDT-SWAP")], families=("momentum_breakout",),
        provider=provider, apply=True, now=1_700_000_000.0, run_worker=True, max_worker_jobs=6,
        max_prepares=6, max_sweeps=4, backend="cpu",  # realistic now -> sane fetch window
    )
    c = out["counters"]
    assert c["prepared_ok"] >= 1                # synthetic candles fetched on demand
    assert c["sweeps_materialized"] >= 1        # prepare CHAINED into run_sweep in the same cycle
    assert c["runs_completed"] >= 1 and c["unique_upserted"] >= 1
    tasks.close()


def test_validation_orchestrator_auto_stampback(monkeypatch, tmp_path):
    from src.research_lab.hard_validation_export import validation_id_for_unique_candidate
    from src.research_lab.validation_orchestrator import run_due_validations
    uc_key = "X::1h::trend::ph::fp"
    validation_id = validation_id_for_unique_candidate({"uc_key": uc_key})
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    tasks.upsert_unique_candidate({
        "uc_key": uc_key, "symbol": "X", "timeframe": "1h", "family": "trend",
        "params_hash": "ph", "data_fingerprint": "fp", "decision": "OBSERVE",
        "validation_status": "FORWARD_PAPER", "hard_status": "", "candidate_id": "c1",
    }, now=1.0)
    tasks.enqueue_task(task_type="export_validation", task_key=f"export::{uc_key}", symbol="X",
                       timeframe="1h", family="trend",
                       payload={"candidate_id": "c1", "uc_key": uc_key}, now=1.0)
    # a verdict already on disk -> the stamp-back must mirror it into unique_candidates
    vdir = tmp_path / "hard_validation" / "verdicts"
    vdir.mkdir(parents=True)
    (vdir / f"{validation_id}.json").write_text(
        json.dumps({"candidate_id": validation_id, "hard_status": "PAPER_FORWARD_READY"}),
        encoding="utf-8")
    rdir = tmp_path / "hard_validation" / "requests"
    rdir.mkdir(parents=True)
    (rdir / f"{validation_id}.json").write_text(
        json.dumps({
            "candidate_id": validation_id,
            "symbol": "X",
            "timeframe": "1h",
            "strategy_id": "trend",
            "lite_status": "FORWARD_PAPER",
            "params": {"direction": "long", "stop_pct": 2, "take_pct": 4, "hold_bars": 3},
            "data_window": {"fingerprint": "fp", "n_bars": 10, "start_ts": 1, "end_ts": 10},
            "metrics": {"uc_key": uc_key},
        }), encoding="utf-8")
    reports = tmp_path / "hard_validation" / "reports"
    reports.mkdir(parents=True)
    (reports / f"{validation_id}.json").write_text(json.dumps({
        "candidate_id": validation_id,
        "source_run_id": "run",
        "symbol": "X",
        "timeframe": "1h",
        "strategy_id": "trend",
        "verdict": {
            "candidate_id": validation_id,
            "hard_status": "PAPER_FORWARD_READY",
            "checks": [],
            "failed_checks": [],
            "reason_codes": [],
        },
        "checks_summary": {"total": 0, "passed": 0, "failed": 0},
    }), encoding="utf-8")

    def fake_validation_batch(*args, **kwargs):
        return {"total": 1, "validated": 1, "errors": 0,
                "results": [{"candidate_id": validation_id, "hard_status": "PAPER_FORWARD_READY"}]}

    monkeypatch.setattr("src.research_lab.validation_orchestrator.run_validation_batch", fake_validation_batch)
    out = run_due_validations(tasks, tmp_path, apply=True, limit=10, now=2.0)
    assert out["export_tasks"] == 1 and out["stamped_unique"] == 1
    assert out["setup_cards"] == 1
    assert tasks.latest_unique_candidates()[0]["hard_status"] == "PAPER_FORWARD_READY"
    card = tmp_path / "setup_library" / "cards" / f"setup-{validation_id}.json"
    assert json.loads(card.read_text(encoding="utf-8"))["paper_forward_ready"] is True
    assert not tasks.tasks_in_state("queued", task_type="export_validation")  # task completed
    tasks.close()


def test_validation_dry_run_is_noop(tmp_path):
    from src.research_lab.validation_orchestrator import run_due_validations
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    tasks.enqueue_task(task_type="export_validation", task_key="export::c1",
                       payload={"candidate_id": "c1"}, now=1.0)
    out = run_due_validations(tasks, tmp_path, apply=False, now=2.0)
    assert out["export_tasks"] == 1 and out["exported"] == 0
    assert not (tmp_path / "hard_validation" / "requests").exists()  # wrote nothing
    tasks.close()


def test_storage_bounds_event_specs_and_terminal_tasks(tmp_path):
    from src.research_lab.storage_policy import bound_farm_artifacts, prune_event_specs
    spec_dir = tmp_path / "plans" / "event_specs"
    spec_dir.mkdir(parents=True)
    for i in range(10):
        (spec_dir / f"s{i}.json").write_text("{}", encoding="utf-8")
    dry = prune_event_specs(tmp_path, keep=4, apply=False)
    assert dry["present"] == 10 and dry["removed"] == 6
    assert len(list(spec_dir.glob("*.json"))) == 10  # dry-run removed nothing
    applied = prune_event_specs(tmp_path, keep=4, apply=True)
    assert applied["removed"] == 6 and len(list(spec_dir.glob("*.json"))) == 4
    # terminal-task retention
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    for i in range(6):
        tid, _ = tasks.enqueue_task(task_type="run_sweep", task_key=f"k{i}", now=float(i))
        tasks.complete_task(tid, now=float(i))
    tasks.close()
    res = bound_farm_artifacts(tmp_path, keep_specs=4, keep_terminal=3, apply=True)
    assert res["terminal_tasks_pruned"] == 3
    tasks2 = FarmTasksDB(tasks_db_path(tmp_path))
    assert len(tasks2.tasks_in_state("completed")) == 3
    tasks2.close()


# ── no-live-trading boundary (AST import scan + code-token scan over new modules) ─
NEW_MODULES = [
    "src/research_lab/data_fingerprint.py", "src/research_lab/farm_tasks_db.py",
    "src/research_lab/intake_adapter.py", "src/research_lab/data_planner.py",
    "src/research_lab/farm_data_state.py", "src/research_lab/farm_sweep_runner.py",
    "src/research_lab/farm_classifier.py", "src/research_lab/farm_coordinator.py",
    "src/research_lab/validation_orchestrator.py", "scripts/strategy_lab/farm_loop.py",
    "src/research_lab/farm_journal.py", "src/research_lab/providers/okx_flow.py",
    "src/research_lab/paper_contract.py",
    "src/research_lab/paper_journal.py", "src/research_lab/paper_runtime.py",
    "scripts/strategy_lab/paper_loop.py",
]
# Module paths the research farm must NEVER import (the live/money/secrets/Telegram path).
FORBIDDEN_IMPORTS = (
    "src.exchange", "src.exchange.okx_client", "scripts.auto_execute",
    "src.data.impulse_pump_engine", "src.data.main_impulse_engine",
    "src.utils.telegram", "src.scout.scanner_v0", "src.config",
)
# Call tokens that would mean an order/live path (never legitimately in research code).
FORBIDDEN_TOKENS = ("place_market_order", "place_order", "execute_signal", "set_leverage",
                    'getenv("AUTO_TRADE"', "getenv('AUTO_TRADE'")


def _imported_modules(tree) -> set[str]:
    import ast
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_new_modules_have_no_live_trading_coupling():
    import ast
    for rel in NEW_MODULES:
        text = (_ROOT / rel).read_text(encoding="utf-8")
        mods = _imported_modules(ast.parse(text))
        for mod in mods:
            for bad in FORBIDDEN_IMPORTS:
                assert not (mod == bad or mod.startswith(bad + ".")), f"{rel} imports {mod}"
        # strip the module docstring so safety prose ('no AUTO_TRADE') is not a false hit
        body = ast.parse(text).body
        doc = body[0].value.value if (body and isinstance(body[0], ast.Expr)
                                      and isinstance(getattr(body[0], "value", None), ast.Constant)) else ""
        code = text.replace(str(doc), "", 1) if doc else text
        for token in FORBIDDEN_TOKENS:
            assert token not in code, f"{rel} must not use {token}"
