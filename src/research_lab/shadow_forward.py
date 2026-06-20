# -*- coding: utf-8 -*-
"""Shadow-forward watch lane — observe a re-validation survivor on NEW data, never trade it.

A re-validation survivor (e.g. CBRS 4h mean_reversion_fade) is NOT proven edge — it cleared an
in-sample, multiple-testing-deflated check and sits at the noise floor. Before anything, it must be
watched FORWARD on bars that did not exist at validation time. This module is that lane:

  * register_revalidation_survivors() puts survivors in a research-only registry with status
    ``shadow_forward_candidate`` and ``paper_forward_ready`` permanently False;
  * record_observation() regenerates the SAME signals on bars AFTER the validation point and records
    what WOULD have happened (entry?/MFE/MAE/capture/outcome/net) — an OBSERVATION, not an order.

There is NO execution path here: no exchange/order/credential import, no PaperTradePlan, no money.
A shadow candidate can never be paper-forward-ready or reach a trading path (AST-tested). It is a
watch list whose only output is forward evidence to decide, later and by a human, whether to promote.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.research_lab.exit_recovery import _exit_grid
from src.research_lab.experiment import generate_signals, load_candles, simulate_trades
from src.research_lab.farm_tasks_db import tasks_db_path
from src.research_lab.paths import market_data_glob

SHADOW_STATUS = "shadow_forward_candidate"
FEES_BPS = 7.0
SLIP_BPS = 3.0


@dataclass(frozen=True)
class ShadowCandidate:
    uc_key: str
    symbol: str
    timeframe: str
    family: str
    recovered_exit: str
    params: dict[str, Any]
    source: str = "revalidation_survivor"

    @property
    def status(self) -> str:
        return SHADOW_STATUS

    @property
    def paper_forward_ready(self) -> bool:
        # Invariant: a shadow candidate is NEVER paper-forward-ready. It is a watch, not a plan.
        return False

    def to_dict(self) -> dict[str, Any]:
        return {"uc_key": self.uc_key, "symbol": self.symbol, "timeframe": self.timeframe,
                "family": self.family, "recovered_exit": self.recovered_exit, "params": self.params,
                "source": self.source, "status": self.status, "paper_forward_ready": False}


def _registry_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "shadow_forward.json"


def _load_registry(private_root: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(_registry_path(private_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    items = data.get("by_uc_key") if isinstance(data, dict) else None
    return items if isinstance(items, dict) else {}


def _params_for_uc(private_root: Path, uc_key: str) -> dict[str, Any]:
    """Pull the candidate's params from the brain (unique_candidates.params_json)."""
    db = tasks_db_path(private_root)
    if not db.exists():
        return {}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT params_json FROM unique_candidates WHERE uc_key=?", (uc_key,)).fetchone()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    try:
        params = json.loads(row[0]) if row and row[0] else {}
    except (TypeError, json.JSONDecodeError):
        return {}
    return params if isinstance(params, dict) else {}


def _read_survivors(private_root: Path) -> list[dict[str, Any]]:
    path = Path(private_root) / "state" / "derived" / "recyclable_revalidation.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = (data.get("summary") or {}).get("survivor_rows") if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else []


def register_revalidation_survivors(private_root: Path) -> list[ShadowCandidate]:
    """Register re-validation survivors as shadow_forward_candidate (research-only, never paper-ready)."""
    private_root = Path(private_root)
    registry = _load_registry(private_root)
    out: list[ShadowCandidate] = []
    for row in _read_survivors(private_root):
        uc = str(row.get("uc_key") or "")
        if not uc:
            continue
        cand = ShadowCandidate(uc_key=uc, symbol=str(row.get("symbol") or ""),
                               timeframe=str(row.get("timeframe") or ""), family=str(row.get("family") or ""),
                               recovered_exit=str(row.get("exit") or "baseline"),
                               params=_params_for_uc(private_root, uc))
        registry[uc] = cand.to_dict()
        out.append(cand)
    _write_registry(private_root, registry)
    return out


def _write_registry(private_root: Path, registry: dict[str, dict[str, Any]]) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = _registry_path(private_root)
    payload = {"schema": "shadow_forward.v1",
               "disclaimer": "Research-only forward watch lane. shadow_forward_candidate is NEVER "
                             "paper-forward-ready and has no execution path. Forward evidence only.",
               "by_uc_key": registry}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _exit_override(params: dict[str, Any], exit_name: str) -> dict[str, Any]:
    for name, override in _exit_grid(params):
        if name == exit_name:
            return dict(override)
    return {}


def record_observation(private_root: Path, uc_key: str, *, after_ts: int) -> dict[str, Any]:
    """Forward-only observation: re-run the survivor's signals on bars AFTER ``after_ts`` and record
    what would have happened under its recovered exit. No order, no money, no promotion."""
    private_root = Path(private_root)
    cand = _load_registry(private_root).get(uc_key)
    if not cand:
        return {"uc_key": uc_key, "skipped": "not_registered"}
    from src.research_lab.experiment import choose_symbol_file
    path = choose_symbol_file(market_data_glob(private_root, cand["timeframe"]), cand["symbol"],
                              timeframe=cand["timeframe"])
    if not path:
        return {"uc_key": uc_key, "skipped": "no_candles"}
    candles = load_candles(path)
    forward_bars = sum(1 for c in candles if int(c.get("ts") or 0) > int(after_ts))
    params = dict(cand.get("params") or {})
    override = _exit_override(params, str(cand.get("recovered_exit") or "baseline"))
    signals = [s for s in generate_signals(candles, cand["family"], params)
               if int(candles[int(s["idx"])].get("ts") or 0) > int(after_ts)]
    trades = simulate_trades(candles, signals, {**params, **override},
                             fees_bps=FEES_BPS, slippage_bps=SLIP_BPS) if signals else []
    obs = _observe(trades)
    record = {"uc_key": uc_key, "symbol": cand["symbol"], "timeframe": cand["timeframe"],
              "after_ts": int(after_ts), "forward_bars": forward_bars, "status": SHADOW_STATUS,
              "paper_forward_ready": False, **obs}
    _append_observation(private_root, record)
    return record


def _observe(trades: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    if not n:
        return {"n_signals": 0, "note": "no forward signals yet (awaiting new bars)"}
    nets = [float(t.get("net_pct") or 0.0) for t in trades]
    caps = [float(t.get("capture_of_mfe") or 0.0) for t in trades]
    outs = [str(t.get("outcome") or "") for t in trades]
    return {"n_signals": n, "forward_net_sum": round(sum(nets), 4),
            "forward_avg_capture": round(sum(caps) / n, 4),
            "n_tp": sum(1 for o in outs if o == "take"), "n_sl": sum(1 for o in outs if o in ("stop", "sl")),
            "n_timeout": sum(1 for o in outs if o in ("time_exit", "timeout"))}


def _append_observation(private_root: Path, record: dict[str, Any]) -> None:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "shadow_observations.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def list_shadow(private_root: Path) -> list[dict[str, Any]]:
    return list(_load_registry(private_root).values())


def summarize_shadow(private_root: Path) -> dict[str, Any]:
    rows = list_shadow(private_root)
    return {"shadow_candidates": len(rows),
            "all_research_only": all(not r.get("paper_forward_ready") for r in rows),
            "by_family": _tally(rows, "family"), "uc_keys": [r.get("uc_key") for r in rows]}


def _tally(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
    return out


def main() -> None:
    import argparse
    import os
    import sys
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.research_lab.paths import DEFAULT_PRIVATE_ROOT
    ap = argparse.ArgumentParser(description="Shadow-forward watch lane (research-only, no execution).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--register", action="store_true", help="register re-validation survivors as shadow")
    args = ap.parse_args()
    if args.register:
        regd = register_revalidation_survivors(Path(args.private_root))
        print(f"registered {len(regd)} shadow_forward_candidate(s)")
    print(json.dumps(summarize_shadow(Path(args.private_root)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
