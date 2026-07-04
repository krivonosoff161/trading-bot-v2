# -*- coding: utf-8 -*-
"""Live universe selector — pick the farm's symbols from where the market actually moves.

The farm was grinding a stale alphabetical universe (intake reason ``universe_grind``) polluted with
low-volatility tokenized equities, blind to the live 20-60% movers. This selector replaces that as the
PRIMARY intake source: it reads the OKX PUBLIC tickers (keyless), scores every live USDT perp by
movement / volume / volatility / spread, and buckets them into live groups:

  fresh_movers · btc_eth_tactical · core · high_beta · meme · equity_proxy(separate lane)

It drops illiquid / wide-spread / untradeable instruments, routes tokenized equities to their OWN lane
(never mixed into crypto), and emits farm intake events with reason ``live_mover`` ranked by movement —
so the farm computes the pairs that are actually moving. Curated static groups remain a fallback.

Public/keyless only. No order/account/private endpoint, no secrets, no .env. ``http_get`` is injectable
so scoring/grouping/filtering/intake are fully testable without network. Research-only.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any, Callable

from src.research_lab.instrument_discovery import classify_symbol

OKX_TICKERS = "https://www.okx.com/api/v5/market/tickers?instType=SWAP"
_USER_AGENT = "strategy-lab-research/1.0 (+public-market-data)"
HttpGet = Callable[[str], Any]

MIN_VOL_USD = 5_000_000.0     # 24h quote volume floor (drop illiquid)
MAX_SPREAD_BPS = 25.0         # drop wide-spread (untradeable) instruments
FRESH_RANGE_PCT = 15.0        # 24h range at/above this = a fresh mover (where the action is)
BTC_ETH = {"BTC", "ETH"}
# live group -> the farm intake asset_class vocabulary (data_readiness/timeframe selection)
_ASSET_CLASS = {"fresh_movers": "crypto_alt", "btc_eth_tactical": "crypto_major", "core": "crypto_major",
                "high_beta": "crypto_alt", "meme": "meme", "equity_proxy": "tokenized_equity"}
_PRIORITY = {"fresh_movers": 1, "btc_eth_tactical": 2, "core": 3, "meme": 3, "high_beta": 4, "equity_proxy": 5}


def _default_http_get(url: str, *, timeout: float = 15.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - public https only
        return json.loads(resp.read().decode("utf-8"))


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def score_ticker(t: dict[str, Any]) -> dict[str, Any] | None:
    """One ticker -> normalized metrics (move%, vol_usd, range%, spread_bps) or None if untradeable."""
    inst = str(t.get("instId") or "")
    if not inst.endswith("-USDT-SWAP"):
        return None
    last, o = _f(t.get("last")), _f(t.get("open24h"))
    hi, lo = _f(t.get("high24h")), _f(t.get("low24h"))
    ask, bid = _f(t.get("askPx")), _f(t.get("bidPx"))
    if last <= 0 or o <= 0 or ask <= 0 or bid <= 0:
        return None
    # volCcy24h is in the BASE currency for SWAP -> multiply by price for a cross-symbol USD measure
    vol_usd = _f(t.get("volCcy24h")) * last
    mid = (ask + bid) / 2
    spread_bps = (ask - bid) / mid * 10000 if mid > 0 else 1e9
    return {"symbol": inst.replace("-", "_"), "inst_id": inst,
            "move_pct": round((last / o - 1) * 100, 3), "range_pct": round((hi - lo) / o * 100, 3),
            "vol_usd": round(vol_usd, 1), "spread_bps": round(spread_bps, 3), "last": last}


def _group(symbol: str, m: dict[str, Any]) -> str:
    base_class = classify_symbol(symbol)
    if base_class == "tokenized_equity":
        return "equity_proxy"
    base = symbol.split("_")[0]
    if m["range_pct"] >= FRESH_RANGE_PCT:
        return "fresh_movers"                 # where the action is, regardless of category
    if base in BTC_ETH:
        return "btc_eth_tactical"
    if base_class == "crypto_major":
        return "core"
    if base_class == "meme_or_high_beta":
        return "meme"
    return "high_beta"


def _score(m: dict[str, Any]) -> float:
    """Composite rank: liquidity (log vol) rewarded, volatility rewarded, spread penalized."""
    import math
    return round(math.log10(max(1.0, m["vol_usd"])) * (1 + m["range_pct"] / 20.0) - m["spread_bps"] / 50.0, 4)


def select_universe(tickers: list[dict[str, Any]], *, top_n_per_group: int = 15,
                    min_vol_usd: float = MIN_VOL_USD, max_spread_bps: float = MAX_SPREAD_BPS,
                    include_equity: bool = False) -> dict[str, Any]:
    """Score, filter, group, and top-N. Equities go to their own lane (excluded from crypto by default)."""
    scored: list[dict[str, Any]] = []
    dropped = {"illiquid": 0, "wide_spread": 0, "untradeable": 0}
    for t in tickers:
        m = score_ticker(t)
        if m is None:
            dropped["untradeable"] += 1
            continue
        if m["vol_usd"] < min_vol_usd:
            dropped["illiquid"] += 1
            continue
        if m["spread_bps"] > max_spread_bps:
            dropped["wide_spread"] += 1
            continue
        m["group"] = _group(m["symbol"], m)
        m["score"] = _score(m)
        scored.append(m)
    groups: dict[str, list[dict[str, Any]]] = {}
    for m in sorted(scored, key=lambda x: -x["score"]):
        groups.setdefault(m["group"], []).append(m)
    selected = {g: rows[:top_n_per_group] for g, rows in groups.items()
                if include_equity or g != "equity_proxy"}
    return {"selected": selected, "dropped": dropped, "scored_total": len(scored),
            "equity_lane": groups.get("equity_proxy", [])[:top_n_per_group]}


def to_intake_events(selected: dict[str, list[dict[str, Any]]], *, now: float,
                     suggested_timeframes: list[str] | None = None) -> list[dict[str, Any]]:
    """Farm intake events (reason=live_mover), ranked, deduped by event_id. Never order/live."""
    from src.research_lab.data_readiness_selector import timeframes_for_asset_class
    from src.research_lab.intake_adapter import event_id
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group, rows in selected.items():
        asset_class = _ASSET_CLASS.get(group, "crypto_alt")
        tfs = suggested_timeframes or timeframes_for_asset_class(asset_class, farm_only=True)
        for m in rows:
            eid = event_id(m["symbol"], "live_universe", f"live_mover {group}", now)
            if eid in seen:
                continue
            seen.add(eid)
            out.append({
                "event_id": eid, "symbol": m["symbol"], "source": "live_universe",
                "reason": "live_mover", "observed_at": now, "priority": _PRIORITY.get(group, 4),
                "asset_class": asset_class, "suggested_timeframes": tfs,
                "evidence": {"group": group, "move_pct": m["move_pct"], "range_pct": m["range_pct"],
                             "vol_usd": m["vol_usd"], "spread_bps": m["spread_bps"], "score": m["score"]},
                "raw_ref": {"inst_id": m["inst_id"]},
            })
    return out


def dry_run_table(result: dict[str, Any]) -> str:
    lines = ["group           symbol                move%   range%    vol$M  spread_bps  score"]
    for group, rows in result["selected"].items():
        for m in rows[:8]:
            lines.append(f"{group:15s} {m['symbol']:20s} {m['move_pct']:+7.2f} {m['range_pct']:7.2f} "
                         f"{m['vol_usd']/1e6:8.1f} {m['spread_bps']:9.2f} {m['score']:7.2f}")
    drp = result["dropped"]
    lines.append(f"\ndropped: illiquid={drp['illiquid']} wide_spread={drp['wide_spread']} "
                 f"untradeable={drp['untradeable']} | equity_lane(separate)={len(result['equity_lane'])}")
    return "\n".join(lines)


def write_snapshot(private_root: Path, result: dict[str, Any], *, generated_at: float) -> Path:
    out_dir = Path(private_root) / "discovery"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "live_universe.json"
    payload = {"schema": "live_universe.v1", "generated_at": generated_at,
               "disclaimer": "Live movement-ranked universe from OKX public tickers (keyless). Equities "
                             "are a separate lane. Research-only; selection only, no order/live path.",
               "groups": {g: [r["symbol"] for r in rows] for g, rows in result["selected"].items()},
               "detail": result["selected"], "equity_lane": [r["symbol"] for r in result["equity_lane"]],
               "dropped": result["dropped"]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def run(private_root: Path, *, http_get: HttpGet | None = None, top_n_per_group: int = 15,
        now: float = 0.0) -> dict[str, Any]:
    getter = http_get or _default_http_get
    data = getter(OKX_TICKERS)
    tickers = data.get("data") or [] if isinstance(data, dict) else []
    result = select_universe(tickers, top_n_per_group=top_n_per_group)
    result["intake_events"] = to_intake_events(result["selected"], now=now)
    result["tickers_seen"] = len(tickers)
    return result


def apply_intake(private_root: Path, events: list[dict[str, Any]], *, now: float) -> dict[str, int]:
    """Register live_mover intake events into the farm brain. They carry priority 1-3, so the farm
    consumes them BEFORE the alphabetical universe_grind (priority 4) — no coordinator change needed.
    Pure scheduling: writes the intake table only, never an order/live/.env path."""
    from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path
    db = FarmTasksDB(tasks_db_path(Path(private_root)))
    registered = duplicate = 0
    try:
        for ev in events:
            _eid, is_new = db.upsert_intake_event(ev, now=now)
            registered += int(is_new)
            duplicate += int(not is_new)
    finally:
        db.close()
    return {"registered": registered, "duplicate": duplicate, "total": len(events)}


def main() -> None:
    import argparse
    import os
    import sys
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.research_lab.paths import DEFAULT_PRIVATE_ROOT
    ap = argparse.ArgumentParser(description="Live movement-ranked universe selector (keyless, research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--top-n", type=int, default=15)
    ap.add_argument("--snapshot", action="store_true", help="write discovery/live_universe.json")
    ap.add_argument("--apply", action="store_true",
                    help="register live_mover intake events into the farm brain (priority over grind)")
    args = ap.parse_args()
    import time
    now = time.time()
    result = run(Path(args.private_root), top_n_per_group=args.top_n, now=now)
    print(dry_run_table(result))
    print(f"\ntickers_seen={result['tickers_seen']} intake_events={len(result['intake_events'])}")
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root), result, generated_at=now))
    if args.apply:
        print("intake applied:", apply_intake(Path(args.private_root), result["intake_events"], now=now))


if __name__ == "__main__":
    main()
