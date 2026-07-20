# -*- coding: utf-8 -*-
"""Tests for farm sweep materialization contracts."""
from __future__ import annotations

import json
from pathlib import Path

from src.research_lab.farm_sweep_runner import build_sweep_spec, queue_sweep
from src.research_lab.param_schemas import parameter_search_contract
from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.sweep_compile import compile_sweep
from src.research_lab.sweep_spec import SweepSpec
from src.research_lab.state_db import connect, init_db
from src.research_lab.timeframes import load_timeframe_profiles


def test_farm_sweep_variants_include_executable_exit_params():
    spec = build_sweep_spec("BTC_USDT_SWAP", "1h", "momentum_breakout", fingerprint="fp")
    exp = compile_sweep(
        spec,
        data_glob="market_data/1h/{symbol}_*.json",
        timeframe_profiles=load_timeframe_profiles(),
        resource_policy=load_resource_policy(),
    )
    variants = exp.parameter_grid["momentum_breakout"]
    assert variants
    for params in variants:
        assert params["lookback"] > 0
        assert params["hold_bars"] > 0
        assert params["stop_pct"] > 0
        assert params["take_pct"] > 0


def _variants_for_dimension(dimension: str):
    spec = build_sweep_spec(
        "BTC_USDT_SWAP",
        "1h",
        "momentum_breakout",
        fingerprint="fp",
        dimensions=(dimension,),
    )
    exp = compile_sweep(
        spec,
        data_glob="market_data/1h/{symbol}_*.json",
        timeframe_profiles=load_timeframe_profiles(),
        resource_policy=load_resource_policy(),
    )
    return exp.parameter_grid["momentum_breakout"]


def test_trailing_dimension_adds_dynamic_exit_modes():
    variants = _variants_for_dimension("trailing")
    modes = {row.get("exit_mode") for row in variants}
    assert {"baseline", "trailing", "break_even"} <= modes


def test_stop_dimension_widens_stop_axis():
    base = _variants_for_dimension("hold")
    stop = _variants_for_dimension("stop")
    assert len({row["stop_pct"] for row in stop}) > len({row["stop_pct"] for row in base})


def test_take_profit_dimension_widens_take_axis():
    base = _variants_for_dimension("hold")
    take = _variants_for_dimension("take_profit")
    assert len({row["take_pct"] for row in take}) > len({row["take_pct"] for row in base})


def test_entry_timing_dimension_widens_size_axis():
    base = _variants_for_dimension("hold")
    entry = _variants_for_dimension("entry_timing")
    assert len({row["lookback"] for row in entry}) > len({row["lookback"] for row in base})


def test_non_lookback_family_uses_registry_owned_axis():
    spec = build_sweep_spec(
        "BTC_USDT_SWAP", "1h", "rsi_reversal", fingerprint="fp",
        dimensions=("entry_timing",),
    )
    assert "period" in spec.setup_grid
    assert "lookback" not in spec.setup_grid
    assert len(spec.setup_grid["period"]) > 1


def test_farm_varies_every_declared_axis_within_typed_bounds():
    spec = build_sweep_spec(
        "BTC_USDT_SWAP", "1h", "momentum_breakout", fingerprint="fp"
    )
    contract = parameter_search_contract("momentum_breakout")
    for axis in contract.adaptive_axes:
        assert axis.name in spec.setup_grid
        assert all(
            axis.minimum <= float(value) <= axis.maximum
            for value in spec.setup_grid[axis.name]
        )


def test_zero_default_adaptive_axes_receive_absolute_search_levels():
    for family, axis_name in (
        ("momentum_breakout", "threshold_pct"),
        ("sfp_liquidity_sweep", "vol_mult"),
        ("sfp_liquidity_sweep", "reclaim_buf_pct"),
        ("microstructure_confirmed_breakout", "min_trade_delta"),
    ):
        spec = build_sweep_spec("BTC_USDT_SWAP", "1h", family, fingerprint="fp")
        assert len(spec.setup_grid[axis_name]) > 1
        assert any(float(value) > 0 for value in spec.setup_grid[axis_name])


def test_queue_sweep_binds_snapshot_manifest_into_worker_spec(tmp_path):
    conn = connect(tmp_path / "state" / "strategy_lab.sqlite")
    init_db(conn)
    spec = build_sweep_spec(
        "BTC_USDT_SWAP", "1h", "momentum_breakout", fingerprint="evidence-1",
    )

    _exp_id, job_id, created = queue_sweep(
        conn, spec, private_root=tmp_path,
        profiles=load_timeframe_profiles(), policy=load_resource_policy(),
        data_glob=str(tmp_path / "market_data" / "1h" / "{symbol}_*.json"),
        priority=100, fingerprint="evidence-1",
        data_snapshot_id="csm_queued", data_evidence_hash="evidence-1",
        data_snapshot_bindings=[{
            "symbol": "BTC_USDT_SWAP", "timeframe": "1h",
            "snapshot_id": "csm_queued", "evidence_hash": "evidence-1",
            "row_count": 100,
        }],
    )
    spec_path = Path(conn.execute(
        "SELECT spec_path FROM queue WHERE job_id=?", (job_id,),
    ).fetchone()[0])
    payload = json.loads(spec_path.read_text(encoding="utf-8"))

    assert created is True
    assert payload["data_snapshot_id"] == "csm_queued"
    assert payload["data_evidence_hash"] == "evidence-1"
    binding = payload["search_family_definition"]["data_binding"]
    assert binding["status"] == "bound"
    assert binding["members"] == [{
        "symbol": "BTC_USDT_SWAP", "timeframe": "1h",
        "snapshot_id": "csm_queued", "evidence_hash": "evidence-1",
        "row_count": 100,
    }]


def test_changed_raw_family_cannot_overwrite_or_dedup_completed_spec(tmp_path):
    profiles = load_timeframe_profiles()
    policy = load_resource_policy()
    conn = connect(tmp_path / "state.sqlite")
    init_db(conn)
    base = dict(
        sweep_id="reused",
        anchor_symbol="BTC_USDT_SWAP",
        related_symbols=(),
        timeframe="15m",
        setup_family="rsi_reversal",
        max_variants=1,
        backend="cpu",
        resource_class="light",
    )
    first = SweepSpec(**base, setup_grid={"oversold": [30], "overbought": [70]})
    changed = SweepSpec(
        **base,
        setup_grid={"oversold": [30, 80], "overbought": [70]},
    )
    first_exp, first_job, first_created = queue_sweep(
        conn,
        first,
        private_root=tmp_path,
        profiles=profiles,
        policy=policy,
        data_glob="unused/{symbol}.json",
        priority=50,
        fingerprint="fp",
        data_snapshot_id="csm_one",
        data_evidence_hash="fp",
        data_snapshot_bindings=[{
            "symbol": "BTC_USDT_SWAP", "timeframe": "15m",
            "snapshot_id": "csm_one", "evidence_hash": "fp", "row_count": 100,
        }],
    )
    first_path = conn.execute(
        "SELECT spec_path FROM queue WHERE job_id=?", (first_job,)
    ).fetchone()[0]
    first_bytes = Path(first_path).read_bytes()
    second_exp, second_job, second_created = queue_sweep(
        conn,
        changed,
        private_root=tmp_path,
        profiles=profiles,
        policy=policy,
        data_glob="unused/{symbol}.json",
        priority=50,
        fingerprint="fp",
        data_snapshot_id="csm_one",
        data_evidence_hash="fp",
        data_snapshot_bindings=[{
            "symbol": "BTC_USDT_SWAP", "timeframe": "15m",
            "snapshot_id": "csm_one", "evidence_hash": "fp", "row_count": 100,
        }],
    )
    second_path = conn.execute(
        "SELECT spec_path FROM queue WHERE job_id=?", (second_job,)
    ).fetchone()[0]

    assert first_exp == second_exp == "sweep_reused"
    assert first_created is True and second_created is True
    assert first_job != second_job
    assert first_path != second_path
    assert Path(first_path).read_bytes() == first_bytes
