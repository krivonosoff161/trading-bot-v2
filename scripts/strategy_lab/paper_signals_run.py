# -*- coding: utf-8 -*-
"""Generate + observe + review operational PAPER-WATCH signals from FRESH live OKX data (research-only).

Pipeline (all keyless, deterministic, no order path):
  live movers (discovery snapshot) -> fetch FRESH candles per candidate -> selection gates ->
  deterministic signal geometry (entry/stop/TP/invalidation/max-hold/reason) -> observe on the elapsed
  bars after the decision boundary -> deterministic review -> store + cards + gate-by-gate report.

Boundary note: each signal's geometry is decided using ONLY bars up to a boundary set max_hold+arm bars
back (no look-ahead), then observed on the already-elapsed bars after it. So a signal is checkable in one
session (closed or pending) with a real path — while staying honest (the decision saw no future bars).

Acceptance: produce 3-5 signals, OR a gate-by-gate failure report explaining why none qualify.
NOT an order, NOT live trading; .env/AUTO_TRADE/private endpoints/Telegram-credentials untouched.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab.paper_signals import lane, store  # noqa: E402
from src.research_lab.paper_signals.contract import render_card  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402


def _load_movers(private_root: Path) -> list[dict]:
    path = private_root / "discovery" / "live_universe.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    rows = []
    for grp_rows in (data.get("detail") or {}).values():
        rows.extend(grp_rows or [])
    rows.sort(key=lambda r: -(r.get("score") or 0))
    return rows


def run(private_root: Path, *, timeframes=("15m", "1h"), max_signals=5, provider=None,
        apply=False) -> dict:
    if provider is None:
        from src.research_lab.providers.okx_public import OkxPublicMarketDataProvider
        provider = OkxPublicMarketDataProvider()
    now = time.time()
    now_ms = int(now * 1000)
    known_bad = lane.load_known_bad(private_root)
    movers = _load_movers(private_root)
    gate_counts: dict[str, int] = {}
    rejects: list[dict] = []
    signals = []
    for mv in movers:
        if len(signals) >= max_signals:
            break
        inst = str(mv.get("inst_id") or "")
        symbol = str(mv.get("symbol") or inst.replace("-", "_"))
        for tf in timeframes:
            if len(signals) >= max_signals:
                break
            bars_ms = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}.get(tf, 900_000)
            try:
                candles = provider.fetch_ohlcv(symbol, tf, now_ms - 1500 * bars_ms, now_ms)
            except Exception as exc:  # noqa: BLE001 - network must not crash the lane
                gate_counts["fetch_error"] = gate_counts.get("fetch_error", 0) + 1
                rejects.append({"symbol": symbol, "tf": tf, "gate": f"fetch_error:{type(exc).__name__}"})
                continue
            mv_tf = {**mv, "_tf": tf}
            g = lane.gate_candidate(mv_tf, candles, now_ms=now_ms, known_bad=known_bad)
            gate_counts[g] = gate_counts.get(g, 0) + 1
            if g != "ok":
                rejects.append({"symbol": symbol, "tf": tf, "gate": g})
                continue
            # decide as-of a boundary max_hold+arm bars back, then observe the elapsed bars
            back = lane.HORIZON_BARS.get(tf, 12) + lane.ARM_WINDOW_BARS + 1
            decide = candles[:-back] if len(candles) > back + 40 else candles
            boundary_ts = int(decide[-1]["ts"])
            sig, why = lane.build_signal(symbol, inst, tf, decide, source="farm", mover=mv,
                                         now=now, boundary_ts=boundary_ts)
            gate_counts[f"geom:{why}"] = gate_counts.get(f"geom:{why}", 0) + 1
            if sig is None:
                rejects.append({"symbol": symbol, "tf": tf, "gate": f"geom:{why}"})
                continue
            sig = lane.review(lane.observe(sig, candles))
            signals.append((sig, candles))

    report = {"generated": len(signals), "gate_counts": gate_counts, "rejects": rejects[:40],
              "timeframes": list(timeframes), "movers_considered": len(movers),
              "note": "paper-watch signals from fresh keyless OKX data; research-only, NOT orders"}
    if apply:
        for sig, candles in signals:
            store.append_signal(private_root, sig)
            lane.write_review_artifact(private_root, sig, candles)
        store.write_state_snapshot(private_root)
        report["written"] = True
    report["cards"] = [render_card(s) for s, _ in signals]
    return report


def _notify(cards: list[str]) -> str:
    """Best-effort Telegram NOTIFICATION (surface only). Fires only when a token AND a paper chat id are
    already configured in env — never sends otherwise, never touches orders/credentials in code."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = (os.getenv("PAPER_CHAT_ID") or os.getenv("SCANNER_CHAT_ID") or "").split(",")[0].strip()
    if not token or not chat:
        return "skipped:no_token_or_chat"
    try:
        import asyncio
        from src.utils.telegram import send_message_to
        for c in cards:
            asyncio.run(send_message_to(chat, "📄 PAPER WATCH (research-only, not an order)\n\n" + c))
        return f"sent:{len(cards)}"
    except Exception as exc:  # noqa: BLE001 - notification must never break the lane
        return f"error:{type(exc).__name__}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate paper-watch signals from fresh OKX data (research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--timeframes", default="15m,1h")
    ap.add_argument("--max-signals", type=int, default=5)
    ap.add_argument("--apply", action="store_true", help="persist signals + review artifacts + snapshot")
    ap.add_argument("--notify", action="store_true", help="send cards to Telegram IF token+chat already in env")
    args = ap.parse_args()
    rep = run(Path(args.private_root), timeframes=tuple(args.timeframes.split(",")),
              max_signals=args.max_signals, apply=args.apply)
    print(f"generated={rep['generated']} (apply={args.apply})  gate_counts={rep['gate_counts']}")
    for card in rep["cards"]:
        print("\n" + card)
    if rep["generated"] < 3:
        print("\n--- GATE-BY-GATE (why < 3 signals) ---")
        for r in rep["rejects"][:25]:
            print(f"  {r['symbol']} {r['tf']} -> {r['gate']}")
    if args.notify:
        print("\ntelegram:", _notify(rep["cards"]))


if __name__ == "__main__":
    main()
