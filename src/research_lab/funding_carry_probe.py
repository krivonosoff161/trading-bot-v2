# -*- coding: utf-8 -*-
"""Funding-carry probe — a directionless edge candidate (research-only).

Direction prediction on movers is a coin-flip (proven). Funding carry needs NO direction: hold a
delta-neutral position (short the perp + long the spot, net delta 0) and collect the funding the crowded
side pays. The directional PnL cancels; the PnL is the accumulated funding minus the round-trip cost of
the two legs (and, in reality, basis risk — flagged as the next test, not modeled here).

First-order honest question: is there harvestable carry AFTER the entry/exit cost, on a broad symbol
base? This pulls public keyless funding history per symbol, measures the carry/persistence, and
simulates a simple harvest: enter when funding is high and same-signed for a few periods, accrue the
funding each 8h while the sign holds, exit on a flip; subtract a conservative two-leg taker round-trip.

If even gross funding does not beat the cost, carry is dead here. If it does, basis/execution risk is the
next gate. Nothing is edge or paper-ready; keyless public, read-only, no order/money/live path.
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, median
from typing import Any

# Funding accrues every 8h on OKX. A delta-neutral harvest pays two taker legs in and two out.
ENTER_THRESH = 0.0003        # 0.03% per 8h funding to bother entering (~27%/yr annualized)
EXIT_ON_FLIP = True
PERSIST = 2                  # require this many same-sign periods before entering
ROUND_TRIP_COST = 0.0020     # 0.20% = two taker legs in + two out (conservative; maker would be cheaper)
MAX_HOLD = 60                # cap an episode at 60 funding periods (~20 days)
MIN_EPISODES = 30            # need a broad pool before trusting the verdict


def _episodes(points: list[tuple[int, float]]) -> list[dict[str, Any]]:
    """Harvest episodes from a funding series: enter on persistent high funding, accrue, exit on flip."""
    eps: list[dict[str, Any]] = []
    n = len(points)
    i = PERSIST
    while i < n:
        rate = points[i][1]
        sign = 1 if rate > 0 else -1 if rate < 0 else 0
        # entry: |funding| high AND same sign for the last PERSIST periods
        if sign != 0 and abs(rate) >= ENTER_THRESH and all(
                (1 if points[i - k][1] > 0 else -1 if points[i - k][1] < 0 else 0) == sign
                for k in range(1, PERSIST + 1)):
            collected = 0.0
            held = 0
            j = i
            while j < n and held < MAX_HOLD:
                r = points[j][1]
                s = 1 if r > 0 else -1 if r < 0 else 0
                if EXIT_ON_FLIP and s != sign:
                    break
                collected += abs(r)          # delta-neutral harvester collects the funding magnitude
                held += 1
                j += 1
            eps.append({"periods": held, "gross": round(collected, 6),
                        "net": round(collected - ROUND_TRIP_COST, 6)})
            i = j
        else:
            i += 1
    return eps


def _symbol_metrics(points: list[tuple[int, float]]) -> dict[str, Any] | None:
    if len(points) < 10:
        return None
    rates = [r for _, r in points]
    eps = _episodes(points)
    if not eps:
        return {"n_points": len(points), "mean_funding": round(mean(rates), 6), "episodes": 0}
    return {"n_points": len(points), "mean_funding": round(mean(rates), 6),
            "annualized_carry_pct": round(mean(rates) * 3 * 365 * 100, 2),  # 3 periods/day
            "episodes": len(eps), "median_episode_net": round(median(e["net"] for e in eps), 5),
            "episode_net_positive_share": round(sum(1 for e in eps if e["net"] > 0) / len(eps), 3),
            "median_periods_held": median(e["periods"] for e in eps)}


LIQUID_VOL_USD = 50_000_000.0   # the carry must survive where two delta-neutral legs are executable


def _symbol_volumes(private_root: Path) -> dict[str, float]:
    """24h USD volume per symbol from the live-universe snapshot (for the liquidity gate)."""
    try:
        snap = json.loads((Path(private_root) / "discovery" / "live_universe.json").read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, float] = {}
    for rows in (snap.get("detail") or {}).values():
        for m in rows:
            out[m["symbol"]] = float(m.get("vol_usd") or 0.0)
    return out


def run(private_root: Path, *, limit_symbols: int = 40, provider: Any = None) -> dict[str, Any]:
    private_root = Path(private_root)
    if provider is None:
        from src.research_lab.providers.okx_flow import OkxPublicFundingProvider
        provider = OkxPublicFundingProvider()
    from src.research_lab.mover_validation import _mover_symbols
    symbols = _mover_symbols(private_root, limit=limit_symbols) or []
    volumes = _symbol_volumes(private_root)
    import time
    now = int(time.time() * 1000)
    start = now - 120 * 86_400_000   # ~120 days of funding history
    per_symbol: dict[str, dict[str, Any]] = {}
    all_eps: list[dict[str, Any]] = []
    liquid_eps: list[dict[str, Any]] = []
    for sym in symbols:
        try:
            pts = provider.fetch_funding(sym, start, now)
        except Exception:  # noqa: BLE001 - network/parse must not crash the probe
            continue
        if not pts:
            continue
        m = _symbol_metrics(sorted(pts))
        if m is None:
            continue
        m["vol_usd"] = volumes.get(sym, 0.0)
        m["liquid"] = volumes.get(sym, 0.0) >= LIQUID_VOL_USD
        per_symbol[sym] = m
        eps = _episodes(sorted(pts))
        all_eps.extend(eps)
        if m["liquid"]:
            liquid_eps.extend(eps)
    return {"symbols": len(per_symbol), "total_episodes": len(all_eps),
            "summary": _summarize(per_symbol, all_eps, liquid_eps), "per_symbol": per_symbol}


def _pool(nets: list[float]) -> dict[str, Any]:
    return {"episodes": len(nets), "median_net_pct": round(median(nets) * 100, 4) if nets else 0.0,
            "net_positive_share": round(sum(1 for x in nets if x > 0) / len(nets), 3) if nets else 0.0}


def _summarize(per_symbol: dict[str, dict[str, Any]], all_eps: list[dict[str, Any]],
               liquid_eps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if len(all_eps) < MIN_EPISODES:
        return {"verdict": "underpowered", "episodes": len(all_eps)}
    liquid_eps = liquid_eps or []
    nets = [e["net"] for e in all_eps]
    liq_nets = [e["net"] for e in liquid_eps]
    pos_share = sum(1 for x in nets if x > 0) / len(nets)
    med_net = median(nets)
    # the verdict that MATTERS is the LIQUID one (where two delta-neutral legs are executable). The carry
    # is a candidate only if it survives the liquidity gate, not just on untradeable extreme-funding tokens.
    liq_ok = len(liq_nets) >= MIN_EPISODES and median(liq_nets) > 0 and \
        (sum(1 for x in liq_nets if x > 0) / len(liq_nets)) >= 0.55
    verdict = ("carry_beats_cost_liquid_candidate" if liq_ok
               else "carry_only_in_illiquid" if med_net > 0
               else "carry_below_cost")
    ranked = sorted(per_symbol.items(), key=lambda kv: -(kv[1].get("annualized_carry_pct") or 0))
    return {"pooled": _pool(nets), "liquid_pool": _pool(liq_nets),
            "illiquid_pool": _pool([e["net"] for e in all_eps if e not in liquid_eps]) if liquid_eps else {},
            "median_episode_net_pct": round(med_net * 100, 4), "episode_net_positive_share": round(pos_share, 3),
            "top_carry_symbols": [{"symbol": s, "vol_usd": m.get("vol_usd"), "liquid": m.get("liquid"),
                                   "annualized_carry_pct": m.get("annualized_carry_pct"),
                                   "episode_net_positive_share": m.get("episode_net_positive_share")}
                                  for s, m in ranked[:10]],
            "verdict": verdict,
            "note": "delta-neutral funding harvest, net of a 0.20% two-leg round-trip. The LIQUID pool is "
                    "the one that matters (executable). Basis/liquidation/path risk on the short leg NOT "
                    "modeled -> the decisive next gate. carry != edge; nothing paper-ready"}


def write_snapshot(private_root: Path, report: dict[str, Any]) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "funding_carry_probe.json"
    payload = {"schema": "funding_carry_probe.v1",
               "disclaimer": "Delta-neutral funding-carry harvest on public funding history (keyless). "
                             "Net of round-trip cost only; basis/execution risk NOT modeled. Research-only; "
                             "carry != edge; nothing paper-ready.",
               "symbols": report["symbols"], "total_episodes": report["total_episodes"],
               "summary": report["summary"], "per_symbol": report["per_symbol"]}
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
    ap = argparse.ArgumentParser(description="Delta-neutral funding-carry probe (keyless, research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--limit-symbols", type=int, default=40)
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    report = run(Path(args.private_root), limit_symbols=args.limit_symbols)
    s = report["summary"]
    print(f"symbols={report['symbols']} episodes={report['total_episodes']}  verdict={s.get('verdict')}")
    if "median_episode_net_pct" in s:
        print(f"  median episode net%: {s['median_episode_net_pct']:+.4f}  net+ share: {s['episode_net_positive_share']}")
        print("  top carry symbols:")
        for t in s["top_carry_symbols"][:8]:
            print(f"    {t['symbol']:20s} ann_carry={t['annualized_carry_pct']:+7.1f}%  ep_net+share={t['episode_net_positive_share']}")
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root), report))


if __name__ == "__main__":
    main()
