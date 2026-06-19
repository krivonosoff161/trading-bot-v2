# -*- coding: utf-8 -*-
"""Materialize a run_sweep task into the proven compute queue (strategy_lab.sqlite).

Builds one bounded :class:`SweepSpec` for a (symbol, timeframe, family), compiles it
through the existing ``compile_sweep`` path, and idempotently queues it via
``ensure_experiment_queued`` — so the EXISTING worker computes it unchanged.

The crucial fix for the ``already_queued`` saturation lives here: the spec filename
embeds the DATA FINGERPRINT, so a sweep on fresh candles gets a NEW spec_path and is
recomputed, while identical data hits the existing completed-job dedup. Paper only:
no order path, writes only the private spec file + the queue row.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.scanner_farm_pipeline import event_spec_dir
from src.research_lab.state_db import ensure_experiment_queued
from src.research_lab.strategy_registry import get_strategy
from src.research_lab.sweep_compile import compile_sweep
from src.research_lab.sweep_spec import SweepSpec

# Params we vary by ±half when present (one "size" knob per family + the hold).
_SIZE_KEYS = ("lookback", "period", "trend_ma", "oi_lookback", "range_lookback",
              "swing_lookback", "fvg_lookback", "squeeze_lookback", "vwap_period", "bb_period")
MAX_VARIANTS = 8


def _safe(symbol: str) -> str:
    return str(symbol).replace("-", "_").replace("/", "_").upper()


def _default_grids(defaults: dict[str, Any]) -> tuple[dict, dict]:
    """A tiny bounded grid from the family defaults (one size knob + hold)."""
    exit_keys = {"hold_bars", "stop_pct", "take_pct"}
    setup: dict[str, list[Any]] = {k: [v] for k, v in defaults.items() if k not in exit_keys}
    for key in _SIZE_KEYS:
        if key in defaults:
            base = int(defaults[key])
            setup[key] = sorted({max(2, base // 2), base})
            break
    exit_grid: dict[str, list[Any]] = {
        k: [defaults[k]] for k in ("stop_pct", "take_pct") if k in defaults
    }
    if "hold_bars" in defaults:
        base = int(defaults["hold_bars"])
        exit_grid["hold_bars"] = sorted({base, max(2, base // 2)})
    return setup, exit_grid


def build_sweep_spec(symbol: str, timeframe: str, family: str, *, fingerprint: str | None,
                     backend: str = "auto", max_variants: int = MAX_VARIANTS) -> SweepSpec:
    defaults = dict(get_strategy(family).parameter_defaults)
    setup_grid, exit_grid = _default_grids(defaults)
    fp_tag = (fingerprint or "nofp")[:10]
    return SweepSpec(
        sweep_id=f"farm_{_safe(symbol)}_{timeframe}_{family}_{fp_tag}",
        anchor_symbol=symbol, related_symbols=(), timeframe=timeframe, setup_family=family,
        setup_grid=setup_grid, exit_grid=exit_grid, max_variants=min(max_variants, 12),
        backend=backend, resource_class="normal", private_output_policy="private_only",
    )


def queue_sweep(conn, spec: SweepSpec, *, private_root: Path, profiles, policy, data_glob: str,
                priority: int, fingerprint: str | None, event_context: dict[str, Any] | None = None
                ) -> tuple[str, int, bool]:
    """Compile + write spec file + idempotently enqueue. Returns (experiment_id, job_id, created)."""
    exp = compile_sweep(spec, data_glob=data_glob, timeframe_profiles=profiles,
                        resource_policy=policy, event_context=event_context or {})
    out_dir = event_spec_dir(private_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_path = out_dir / f"{exp.experiment_id}.json"
    payload = {
        "experiment_id": exp.experiment_id, "data_glob": exp.data_glob, "symbols": exp.symbols,
        "timeframe": exp.timeframe, "families": exp.families, "fees_bps": exp.fees_bps,
        "slippage_bps": exp.slippage_bps, "min_trades": exp.min_trades, "split_ratio": exp.split_ratio,
        "max_runs": exp.max_runs, "parameter_grid": exp.parameter_grid, "filters": exp.filters,
        "event_context": exp.event_context, "backend": exp.backend, "data_fingerprint": fingerprint,
    }
    spec_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    job_id, created = ensure_experiment_queued(conn, spec_path.resolve(), priority=int(priority))
    return exp.experiment_id, int(job_id), bool(created)
