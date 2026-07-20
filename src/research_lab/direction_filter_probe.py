# -*- coding: utf-8 -*-
"""Direction-filter probe — can a pre-entry feature tilt the mover coin-flip? (research-only)

Mover momentum is a ~coin-flip on direction (bimodal: trend-to-take vs reverse-to-stop). An exit cannot
fix that. The only thing that could is a PRE-ENTRY FILTER that separates continuation from reversal. This
probe does not build a strategy and hope — it MEASURES: pool every momentum entry across the live-mover
universe, attach features known at entry (no look-ahead), split in-sample/out-of-sample by time, and for
each feature check whether its terciles separate the forward outcome OUT OF SAMPLE on a broad symbol base.

Features (all computed from bars strictly before entry):
  * overext  — how far price is stretched above its long MA (overextension -> reversal pressure?)
  * run5     — magnitude of the last-5-bar run (already exhausted?)
  * volratio — volume surge vs the recent average (conviction?)

A feature "tilts the coin" only if its top vs bottom OOS tercile mean-net separate with a consistent
sign across a broad pool. Nothing here is edge or paper-ready; a separation is a forward-watch lead.
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any

from src.research_lab.exit_phase2 import _exit_modes, simulate_exit_mode
from src.research_lab.experiment import generate_signals
from src.research_lab.mover_validation import _mover_symbols, ensure_candles
from src.research_lab.strategies._helpers import sma, vols

FEES_BPS = 7.0
SLIP_BPS = 3.0
OOS_FRAC = 0.35
MA_LONG = 50
RUN_BARS = 5
MIN_OOS_ENTRIES = 60          # need a broad pool before trusting a tercile separation
PARAMS = {"lookback": 20, "hold_bars": 6, "stop_pct": 6, "take_pct": 12}
FEATURES = ("overext", "run5", "volratio")


def _entry_features(candles: list[dict[str, Any]], idx: int, closes: list[float],
                    vseries: list[float]) -> dict[str, float] | None:
    """Features known at the decision bar idx-1 (entry is at idx; no look-ahead)."""
    j = idx - 1
    if j < MA_LONG or j < RUN_BARS:
        return None
    ma = sma(closes, j, MA_LONG)
    if ma is None or ma <= 0 or closes[j - RUN_BARS] <= 0:
        return None
    avg_vol = sma(vseries, j, MA_LONG)
    volratio = (vseries[j] / avg_vol) if avg_vol and avg_vol > 0 else 1.0
    return {"overext": (closes[j] / ma - 1) * 100,
            "run5": (closes[j] / closes[j - RUN_BARS] - 1) * 100,
            "volratio": volratio}


def collect_entries(private_root: Path, *, limit_symbols: int = 30, timeframe: str = "4h",
                    provider: Any = None) -> list[dict[str, Any]]:
    """Pool every momentum entry across movers with its features + forward net (early_tp exit)."""
    private_root = Path(private_root)
    if provider is None:
        from src.research_lab.providers.okx_public import OkxPublicMarketDataProvider
        provider = OkxPublicMarketDataProvider()
    early_tp = dict(_exit_modes(PARAMS))["early_tp"]
    rows: list[dict[str, Any]] = []
    for sym in _mover_symbols(private_root, limit=limit_symbols):
        if not ensure_candles(private_root, sym, timeframe, provider=provider):
            continue
        from src.research_lab.candle_library import load_canonical_candles
        selected = load_canonical_candles(
            private_root, sym, timeframe,
            purpose="direction_filter_probe", coverage_policy="gap_free",
        )
        candles = selected.rows
        if len(candles) < MA_LONG + 30:
            continue
        closes = [float(c["close"]) for c in candles]
        vseries = vols(candles)
        cut = int(len(candles) * (1.0 - OOS_FRAC))
        sigs = generate_signals(candles, "momentum_breakout", PARAMS)
        for s in sigs:
            idx = int(s["idx"])
            feats = _entry_features(candles, idx, closes, vseries)
            if feats is None:
                continue
            trades = simulate_exit_mode(candles, [s], PARAMS, early_tp, fees_bps=FEES_BPS, slip_bps=SLIP_BPS)
            if not trades:
                continue
            rows.append({"symbol": sym, "oos": idx >= cut, "net": float(trades[0].get("net_pct") or 0.0),
                         "data_snapshot_id": selected.manifest.snapshot_id,
                         "data_evidence_hash": selected.manifest.evidence_hash,
                         "data_provenance_status": selected.manifest.provenance_status,
                         **feats})
    return rows


def _terciles(values: list[float]) -> tuple[float, float]:
    s = sorted(values)
    n = len(s)
    return s[n // 3], s[2 * n // 3]


def analyze(rows: list[dict[str, Any]]) -> dict[str, Any]:
    oos = [r for r in rows if r["oos"]]
    out: dict[str, Any] = {}
    for feat in FEATURES:
        vals = [r[feat] for r in oos]
        if len(vals) < MIN_OOS_ENTRIES:
            out[feat] = {"oos_entries": len(vals), "verdict": "underpowered"}
            continue
        lo, hi = _terciles(vals)
        low = [r["net"] for r in oos if r[feat] <= lo]
        mid = [r["net"] for r in oos if lo < r[feat] <= hi]
        top = [r["net"] for r in oos if r[feat] > hi]
        m_low, m_top = (mean(low) if low else 0.0), (mean(top) if top else 0.0)
        sep = m_top - m_low
        out[feat] = {
            "oos_entries": len(vals), "tercile_lo": round(lo, 2), "tercile_hi": round(hi, 2),
            "mean_net_low": round(m_low, 3), "mean_net_mid": round(mean(mid) if mid else 0.0, 3),
            "mean_net_top": round(m_top, 3), "separation_top_minus_low": round(sep, 3),
            "win_low": round(sum(1 for x in low if x > 0) / len(low), 3) if low else 0.0,
            "win_top": round(sum(1 for x in top if x > 0) / len(top), 3) if top else 0.0,
            "verdict": _feat_verdict(m_low, m_top),
        }
    return out


def _feat_verdict(m_low: float, m_top: float) -> str:
    """A feature tilts the coin only if one tercile is clearly positive and the other clearly negative."""
    if m_top > 0.2 and m_low < -0.2:
        return "tilts_long_high"        # high feature -> continuation, low -> reversal
    if m_low > 0.2 and m_top < -0.2:
        return "tilts_long_low"         # low feature -> continuation, high -> reversal
    return "no_separation"


def run(private_root: Path, *, limit_symbols: int = 30) -> dict[str, Any]:
    rows = collect_entries(Path(private_root), limit_symbols=limit_symbols)
    oos = sum(1 for r in rows if r["oos"])
    feats = analyze(rows)
    tilts = [f for f, v in feats.items() if v.get("verdict", "").startswith("tilts")]
    return {"entries": len(rows), "oos_entries": oos, "by_feature": feats, "tilting_features": tilts,
            "overall": "direction_filter_found" if tilts else "no_filter_separates_coinflip"}


def write_snapshot(private_root: Path, report: dict[str, Any]) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "direction_filter_probe.json"
    payload = {"schema": "direction_filter_probe.v1",
               "disclaimer": "Pre-entry feature tercile separation on pooled mover momentum entries, OOS. "
                             "Research-only; a separation is a forward-watch lead, never edge/paper-ready.",
               **report}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> None:
    import argparse
    import os
    import sys
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.research_lab.paths import DEFAULT_PRIVATE_ROOT
    ap = argparse.ArgumentParser(description="Direction-filter probe on mover momentum entries (research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--limit-symbols", type=int, default=30)
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    report = run(Path(args.private_root), limit_symbols=args.limit_symbols)
    print(f"entries={report['entries']} oos={report['oos_entries']}  overall={report['overall']}")
    for feat, v in report["by_feature"].items():
        if "mean_net_low" in v:
            print(f"  {feat:9s} oos={v['oos_entries']:4d}  low={v['mean_net_low']:+.3f}(win{v['win_low']}) "
                  f"top={v['mean_net_top']:+.3f}(win{v['win_top']})  sep={v['separation_top_minus_low']:+.3f}  {v['verdict']}")
        else:
            print(f"  {feat:9s} {v['verdict']} (oos={v['oos_entries']})")
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root), report))


if __name__ == "__main__":
    main()
