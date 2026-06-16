# -*- coding: utf-8 -*-
"""Tests for the GPU backend contract (cpu/gpu/auto), kernel parity, and metadata.

All tests run on a machine WITHOUT a GPU: capability detection is monkeypatched,
so cpu/gpu/auto behavior is deterministic everywhere. One real-GPU parity test is
skipped unless a GPU backend is actually installed.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from src.research_lab import gpu_runtime
from src.research_lab.experiment import ExperimentSpec, evaluate_spec, load_candles
from src.research_lab.gpu_kernels import generate_signals_vectorized
from src.research_lab.gpu_runtime import GpuCapability, resolve_backend
from src.research_lab.outputs import write_run_outputs
from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.strategies.breakout import signals_momentum_breakout
from src.research_lab.sweep_compile import compile_sweep
from src.research_lab.sweep_spec import SweepSpec, validate_sweep_spec
from src.research_lab.timeframes import load_timeframe_profiles


def _patch_gpu(monkeypatch, *, available: bool, backend_name: str = "cupy") -> None:
    cap = GpuCapability(
        gpu_available=available,
        backend_name=backend_name if available else "none",
        device_name="TEST GPU" if available else "",
        reason_if_unavailable="" if available else "no GPU compute backend installed (test)",
        nvidia_smi_present=True,
    )
    monkeypatch.setattr(gpu_runtime, "detect_gpu", lambda: cap)
    if available:
        # Drive the GPU code-path deterministically with numpy as the array
        # module, so the integration test does not depend on a healthy real cupy
        # install (real-GPU parity is covered separately, skipif-gated).
        import src.research_lab.experiment as _exp
        monkeypatch.setattr(_exp, "array_module", lambda _name: np)


def _write_candles(path, n=120, seed=7):
    rng = np.random.default_rng(seed)
    price = 100.0
    rows = []
    day = 86_400_000
    for i in range(n):
        price *= 1.0 + float(rng.normal(0, 0.03))
        price = max(1.0, price)
        high = price * (1 + abs(float(rng.normal(0, 0.02))))
        low = price * (1 - abs(float(rng.normal(0, 0.02))))
        rows.append({"ts": i * day, "date": f"d{i}", "open": price, "high": high,
                     "low": low, "close": price, "vol": 1000.0})
    path.write_text(json.dumps(rows), encoding="utf-8")


def _spec(data_glob, backend, symbol="SYN_USDT_SWAP"):
    return ExperimentSpec(
        experiment_id=f"gpu_test_{backend}",
        data_glob=data_glob,
        symbols=[symbol],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [
            {"lookback": 20, "threshold_pct": 0.0},
            {"lookback": 10, "threshold_pct": 1.0},
        ]},
        timeframe="1d",
        backend=backend,
    )


@pytest.fixture
def syn_glob(tmp_path):
    _write_candles(tmp_path / "SYN_USDT_SWAP_120d_1Dutc.json")
    return str(tmp_path / "{symbol}_*.json")


# --- backend resolution contract -------------------------------------------

def test_gpu_unavailable_backend_gpu_rejected(monkeypatch):
    _patch_gpu(monkeypatch, available=False)
    r = resolve_backend("gpu")
    assert r.effective_backend == "cpu"
    assert not r.ok()
    assert "unavailable" in r.error


def test_gpu_unavailable_backend_auto_falls_back_to_cpu_with_reason(monkeypatch):
    _patch_gpu(monkeypatch, available=False)
    r = resolve_backend("auto")
    assert r.effective_backend == "cpu"
    assert r.ok()
    assert "cpu fallback" in r.fallback_reason


def test_backend_cpu_always_cpu(monkeypatch):
    _patch_gpu(monkeypatch, available=True)  # even with GPU present, cpu stays cpu
    r = resolve_backend("cpu")
    assert r.effective_backend == "cpu"
    assert r.ok()


def test_auto_uses_gpu_when_available(monkeypatch):
    _patch_gpu(monkeypatch, available=True)
    r = resolve_backend("auto")
    assert r.effective_backend == "gpu"
    assert r.ok()


# --- evaluate_spec behavior -------------------------------------------------

def test_evaluate_gpu_unavailable_raises_not_silent_cpu(monkeypatch, syn_glob):
    _patch_gpu(monkeypatch, available=False)
    rt: dict = {}
    with pytest.raises(RuntimeError):
        evaluate_spec(_spec(syn_glob, "gpu"), rt)
    assert rt["effective_backend"] == "cpu"  # recorded, but the run was refused
    assert rt["error"]


def test_evaluate_auto_runs_cpu_with_reason(monkeypatch, syn_glob):
    _patch_gpu(monkeypatch, available=False)
    rt: dict = {}
    results = evaluate_spec(_spec(syn_glob, "auto"), rt)
    assert len(results) == 2
    assert rt["effective_backend"] == "cpu"
    assert rt["accelerated_runs"] == 0
    assert "elapsed_ms" in rt


def test_evaluate_gpu_available_accelerates_and_matches_cpu(monkeypatch, syn_glob):
    # GPU "available": the vectorized kernel runs (xp falls back to numpy when
    # cupy is absent), so we get acceleration counted AND identical results to cpu.
    _patch_gpu(monkeypatch, available=True, backend_name="cupy")
    rt_gpu: dict = {}
    gpu_results = evaluate_spec(_spec(syn_glob, "gpu"), rt_gpu)
    assert rt_gpu["effective_backend"] == "gpu"
    assert rt_gpu["accelerated_runs"] == 2

    _patch_gpu(monkeypatch, available=False)
    cpu_results = evaluate_spec(_spec(syn_glob, "cpu"), {})
    # same run_ids, same metrics -> the GPU path did not change the numbers
    assert [r.run_id for r in gpu_results] == [r.run_id for r in cpu_results]
    for g, c in zip(gpu_results, cpu_results):
        assert g.metrics["n_trades"] == c.metrics["n_trades"]
        assert g.metrics["total_net_pct"] == c.metrics["total_net_pct"]


# --- kernel parity ----------------------------------------------------------

def test_numpy_kernel_parity_with_scalar(syn_glob):
    import glob
    path = glob.glob(syn_glob.format(symbol="SYN_USDT_SWAP"))[0]
    from pathlib import Path
    candles = load_candles(Path(path))
    for params in ({"lookback": 20, "threshold_pct": 0.0},
                   {"lookback": 7, "threshold_pct": 2.0},
                   {"lookback": 30, "threshold_pct": 0.0}):
        scalar = signals_momentum_breakout(candles, params)
        vec = generate_signals_vectorized(candles, "momentum_breakout", params, xp=np)
        assert scalar == vec


@pytest.mark.skipif(not gpu_runtime.gpu_available(), reason="no real GPU backend installed")
def test_real_gpu_kernel_parity(syn_glob):
    import glob
    from pathlib import Path
    cap = gpu_runtime.detect_gpu()
    xp = gpu_runtime.array_module(cap.backend_name)
    path = glob.glob(syn_glob.format(symbol="SYN_USDT_SWAP"))[0]
    candles = load_candles(Path(path))
    for params in ({"lookback": 20, "threshold_pct": 0.0}, {"lookback": 10, "threshold_pct": 1.0}):
        scalar = signals_momentum_breakout(candles, params)
        gpu = generate_signals_vectorized(candles, "momentum_breakout", params, xp=xp)
        assert scalar == gpu


# --- compile + outputs metadata --------------------------------------------

def test_compile_sweep_preserves_backend():
    spec = SweepSpec(
        sweep_id="b", anchor_symbol="DOGE-USDT-SWAP", related_symbols=(), timeframe="1d",
        setup_family="momentum_breakout", setup_grid={"lookback": [10, 20]},
        max_variants=4, backend="auto",
    )
    exp = compile_sweep(
        spec, data_glob="x/{symbol}.json",
        timeframe_profiles=load_timeframe_profiles(), resource_policy=load_resource_policy(),
    )
    assert exp.backend == "auto"


def test_experiment_spec_backend_roundtrip(tmp_path):
    p = tmp_path / "spec.json"
    p.write_text(json.dumps({
        "experiment_id": "x", "data_glob": "g/{symbol}.json", "symbols": ["A_USDT_SWAP"],
        "families": ["momentum_breakout"], "parameter_grid": {"momentum_breakout": [{"lookback": 10}]},
        "backend": "gpu",
    }), encoding="utf-8")
    assert ExperimentSpec.from_json(p).backend == "gpu"


def test_output_writes_effective_backend(monkeypatch, syn_glob, tmp_path):
    _patch_gpu(monkeypatch, available=False)
    rt: dict = {}
    spec = _spec(syn_glob, "auto")
    results = evaluate_spec(spec, rt)
    out_root = tmp_path / "priv"
    run_dir = write_run_outputs(spec, results, out_root, allow_public_output=True, runtime_meta=rt)
    payload = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    assert payload["requested_backend"] == "auto"
    assert payload["runtime"]["effective_backend"] == "cpu"
    assert payload["runtime"]["gpu_available"] is False
    assert "elapsed_ms" in payload["runtime"]


def test_validate_sweep_spec_backend_capability(monkeypatch):
    profiles = load_timeframe_profiles()
    policy = load_resource_policy()

    def _spec_b(backend):
        return SweepSpec(
            sweep_id="x", anchor_symbol="DOGE-USDT-SWAP", related_symbols=(), timeframe="1d",
            setup_family="momentum_breakout", setup_grid={"lookback": [10, 20]},
            max_variants=4, backend=backend,
        )

    _patch_gpu(monkeypatch, available=False)
    assert validate_sweep_spec(_spec_b("cpu"), timeframe_profiles=profiles, resource_policy=policy).ok
    assert validate_sweep_spec(_spec_b("auto"), timeframe_profiles=profiles, resource_policy=policy).ok
    assert not validate_sweep_spec(_spec_b("gpu"), timeframe_profiles=profiles, resource_policy=policy).ok

    _patch_gpu(monkeypatch, available=True)
    assert validate_sweep_spec(_spec_b("gpu"), timeframe_profiles=profiles, resource_policy=policy).ok
