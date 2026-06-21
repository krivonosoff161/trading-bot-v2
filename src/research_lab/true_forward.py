# -*- coding: utf-8 -*-
"""True-forward collector — watch tactical/shadow candidates on GENUINELY NEW bars (research-only).

shadow_oos used a held-out TAIL of the same window (pseudo-OOS). This collector is the real thing: at
registration it pins a forward boundary = the LAST bar timestamp then present, and afterwards counts
ONLY bars whose timestamp is strictly greater (i.e. data that did not exist at registration). Until the
data-prepare pipeline fetches newer candles, a candidate honestly stays ``pending_new_bars``.

It reads the watch list from the EXISTING registries (shadow_forward survivors + the outcome-memory
one_shot_candidates — e.g. CBRS 4h MRF and the mean_reversion_fade thin pool). No second brain. On each
bounded pass it re-runs the candidate's own signals on the new-bar region, accumulates the forward
outcome (trades / net / MFE / MAE / TP-before-SL), and matures a candidate once it has enough forward
trades. Bounded: caps + stop-file + dedup. NOTHING is promoted; no order/money/live path.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.research_lab.exit_phase2 import _exit_modes, simulate_exit_mode
from src.research_lab.experiment import (
    choose_symbol_file,
    generate_signals,
    load_candles,
    simulate_trades,
)
from src.research_lab.param_schemas import executable_exit_params
from src.research_lab.paths import market_data_glob
from src.research_lab.stop_intent import is_stop_requested

FEES_BPS = 7.0
SLIP_BPS = 3.0
MATURE_TRADES = 10          # forward trades needed before a candidate is "matured" (still not edge)
DEFAULT_MAX_CANDIDATES = 20
STATUS_PENDING = "pending_new_bars"
STATUS_COLLECTING = "collecting"
STATUS_MATURED = "matured"


@dataclass(frozen=True)
class ForwardWatch:
    uc_key: str
    symbol: str
    timeframe: str
    family: str
    exit: str
    params: dict[str, Any]
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {"uc_key": self.uc_key, "symbol": self.symbol, "timeframe": self.timeframe,
                "family": self.family, "exit": self.exit, "params": self.params, "source": self.source,
                "paper_forward_ready": False}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def build_watchlist(private_root: Path, *, max_candidates: int = DEFAULT_MAX_CANDIDATES) -> list[ForwardWatch]:
    """Watch list from existing registries: shadow_forward survivors + memory one_shot_candidates."""
    private_root = Path(private_root)
    derived = private_root / "state" / "derived"
    out: list[ForwardWatch] = []
    seen: set[str] = set()
    for uc, row in (_read_json(derived / "shadow_forward.json").get("by_uc_key") or {}).items():
        if not isinstance(row, dict) or uc in seen:
            continue
        seen.add(uc)
        out.append(ForwardWatch(str(uc), str(row.get("symbol") or ""), str(row.get("timeframe") or ""),
                                str(row.get("family") or ""), str(row.get("recovered_exit") or "baseline"),
                                dict(row.get("params") or {}), "shadow_forward"))
    mem = _read_json(derived / "setup_outcome_memory.json").get("records") or []
    for r in mem:
        uc = str(r.get("uc_key") or "")
        if not uc or uc in seen or r.get("tactical_class") != "one_shot_candidate":
            continue
        seen.add(uc)
        out.append(ForwardWatch(uc, str(r.get("symbol") or ""), str(r.get("timeframe") or ""),
                                str(r.get("family") or ""), "baseline", {}, "one_shot_candidate"))
        if len(out) >= max_candidates:
            break
    return out[:max_candidates]


def _last_bar_ts(private_root: Path, symbol: str, timeframe: str) -> int:
    path = choose_symbol_file(market_data_glob(private_root, timeframe), symbol, timeframe=timeframe)
    if not path:
        return 0
    candles = load_candles(path)
    return int(candles[-1].get("ts") or 0) if candles else 0


def register(private_root: Path, *, max_candidates: int = DEFAULT_MAX_CANDIDATES) -> dict[str, Any]:
    """Pin the forward boundary (last bar now present) for each watch candidate. Idempotent: an already
    registered candidate keeps its original boundary so new bars are measured from first registration."""
    private_root = Path(private_root)
    reg = _load_registry(private_root)
    for w in build_watchlist(private_root, max_candidates=max_candidates):
        if w.uc_key in reg:
            continue  # keep the original boundary
        boundary = _last_bar_ts(private_root, w.symbol, w.timeframe)
        reg[w.uc_key] = {**w.to_dict(), "boundary_ts": boundary, "last_collected_ts": boundary,
                         "status": STATUS_PENDING, "forward_trades": 0, "forward_net_sum": 0.0,
                         "forward_tp_before_sl": 0, "forward_mfe_sum": 0.0, "forward_mae_sum": 0.0}
    _write_registry(private_root, reg)
    return {"registered": len(reg), "boundaries_pinned": True}


def _registry_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "true_forward.json"


def _load_registry(private_root: Path) -> dict[str, dict[str, Any]]:
    data = _read_json(_registry_path(private_root))
    items = data.get("by_uc_key") if isinstance(data, dict) else None
    return items if isinstance(items, dict) else {}


def _write_registry(private_root: Path, reg: dict[str, dict[str, Any]]) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = _registry_path(private_root)
    payload = {"schema": "true_forward.v1",
               "disclaimer": "True-forward watch on GENUINELY NEW bars (boundary pinned at registration). "
                             "Research-only, no execution path; matured != edge; nothing paper-ready.",
               "summary": summarize_registry(reg), "by_uc_key": reg}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _simulate(candles: list[dict[str, Any]], signals: list[dict[str, Any]], params: dict[str, Any],
              exit_name: str) -> list[dict[str, Any]]:
    if not signals:
        return []
    if exit_name and exit_name != "baseline":
        mode = dict(_exit_modes(params)).get(exit_name)
        if mode is not None:
            return simulate_exit_mode(candles, signals, params, mode, fees_bps=FEES_BPS, slip_bps=SLIP_BPS)
    return simulate_trades(candles, signals, params, fees_bps=FEES_BPS, slippage_bps=SLIP_BPS)


def collect_one(private_root: Path, uc_key: str) -> dict[str, Any]:
    """Accumulate forward outcome on bars strictly after the last collected ts. No look-ahead."""
    private_root = Path(private_root)
    reg = _load_registry(private_root)
    row = reg.get(uc_key)
    if not row:
        return {"uc_key": uc_key, "skipped": "not_registered"}
    path = choose_symbol_file(market_data_glob(private_root, row["timeframe"]), row["symbol"],
                              timeframe=row["timeframe"])
    if not path:
        return {"uc_key": uc_key, "skipped": "no_candles"}
    candles = load_candles(path)
    boundary = int(row.get("last_collected_ts") or row.get("boundary_ts") or 0)
    new_bars = [c for c in candles if int(c.get("ts") or 0) > boundary]
    if not new_bars:
        return {"uc_key": uc_key, "status": STATUS_PENDING, "new_bars": 0}
    params = dict(row.get("params") or {}) or executable_exit_params(row["family"])
    signals = [s for s in generate_signals(candles, row["family"], params)
               if int(candles[int(s["idx"])].get("ts") or 0) > boundary]
    trades = _simulate(candles, signals, params, str(row.get("exit") or "baseline"))
    _accumulate(row, trades, len(new_bars), int(candles[-1].get("ts") or 0))
    reg[uc_key] = row
    _write_registry(private_root, reg)
    return {"uc_key": uc_key, "status": row["status"], "new_bars": len(new_bars),
            "forward_trades": row["forward_trades"]}


def _accumulate(row: dict[str, Any], trades: list[dict[str, Any]], new_bars: int, last_ts: int) -> None:
    for t in trades:
        row["forward_trades"] += 1
        row["forward_net_sum"] = round(row["forward_net_sum"] + float(t.get("net_pct") or 0.0), 4)
        row["forward_mfe_sum"] = round(row["forward_mfe_sum"] + float(t.get("mfe_pct") or 0.0), 4)
        row["forward_mae_sum"] = round(row["forward_mae_sum"] + float(t.get("mae_pct") or 0.0), 4)
        if t.get("tp_before_sl") is True:
            row["forward_tp_before_sl"] += 1
    row["last_collected_ts"] = last_ts
    row["status"] = STATUS_MATURED if row["forward_trades"] >= MATURE_TRADES else STATUS_COLLECTING
    row["paper_forward_ready"] = False


def collect_once(private_root: Path, *, max_candidates: int = DEFAULT_MAX_CANDIDATES) -> dict[str, Any]:
    """One bounded pass over the registry (stop-file aware). Returns per-status counts."""
    private_root = Path(private_root)
    if is_stop_requested(private_root):
        return {"stopped": True}
    reg = _load_registry(private_root)
    results = []
    for uc in list(reg)[: max(1, int(max_candidates))]:
        if is_stop_requested(private_root):
            break
        results.append(collect_one(private_root, uc))
    return {"passed": len(results), "summary": summarize_registry(_load_registry(private_root))}


def summarize_registry(reg: dict[str, dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for row in reg.values():
        by_status[str(row.get("status"))] = by_status.get(str(row.get("status")), 0) + 1
    matured = [r for r in reg.values() if r.get("status") == STATUS_MATURED]
    return {"watched": len(reg), "by_status": dict(sorted(by_status.items(), key=lambda kv: -kv[1])),
            "matured": len(matured), "all_research_only": all(not r.get("paper_forward_ready") for r in reg.values()),
            "note": "true-forward on new bars; pending until data-prepare fetches newer candles; matured != edge"}


def summarize(private_root: Path) -> dict[str, Any]:
    return summarize_registry(_load_registry(Path(private_root)))


def main() -> None:
    import argparse
    import os
    import sys
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.research_lab.paths import DEFAULT_PRIVATE_ROOT
    ap = argparse.ArgumentParser(description="True-forward collector on new bars (research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--register", action="store_true", help="pin forward boundaries for the watch list")
    ap.add_argument("--collect", action="store_true", help="one bounded collection pass over new bars")
    ap.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES)
    args = ap.parse_args()
    if args.register:
        print(json.dumps(register(Path(args.private_root), max_candidates=args.max_candidates), ensure_ascii=False))
    if args.collect:
        print(json.dumps(collect_once(Path(args.private_root), max_candidates=args.max_candidates),
                         ensure_ascii=False, indent=2))
    if not args.register and not args.collect:
        print(json.dumps(summarize(Path(args.private_root)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
