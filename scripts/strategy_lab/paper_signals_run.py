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
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab.paper_signals import ab_report, cycle, lane, store  # noqa: E402
from src.research_lab.paper_signals.contract import render_card  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402
from src.research_lab.providers.okx_public import OkxPublicMarketDataProvider  # noqa: E402


def _notify(cards: list[str]) -> str:
    """Best-effort Telegram NOTIFICATION (surface only). Fires only when a token AND a paper chat id are
    already configured in env — never sends otherwise, never touches orders/credentials in code."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = (os.getenv("PAPER_CHAT_ID") or "").split(",")[0].strip()
    if not token or not chat:
        return "skipped:no_paper_token_or_chat"
    try:
        import asyncio
        from src.utils.telegram import send_message_to
        for c in cards:
            asyncio.run(send_message_to(chat, "📄 PAPER WATCH (research-only, not an order)\n\n" + c))
        return f"sent:{len(cards)}"
    except Exception as exc:  # noqa: BLE001 - notification must never break the lane
        return f"error:{type(exc).__name__}"


def _print_status(private_root: Path) -> None:
    sigs = store.load_signals(private_root)
    by_status: dict[str, int] = {}
    for s in sigs:
        by_status[s.status] = by_status.get(s.status, 0) + 1
    diag: dict[str, int] = {}
    for m in cycle.load_memory(private_root):
        d = m.get("diagnosis")
        if d:
            diag[d] = diag.get(d, 0) + 1
    print(f"paper signals: total={len(sigs)} by_status={by_status}")
    print(f"diagnosis_counts (memory): {diag}")
    for s in sigs:
        if s.status in cycle.ACTIVE:
            print("\n" + render_card(s))


def main() -> None:
    ap = argparse.ArgumentParser(description="Repeatable paper-watch cycle from fresh OKX data (research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--timeframes", default="15m,1h")
    ap.add_argument("--max-signals", type=int, default=5)
    ap.add_argument("--mode", choices=["live", "replay"], default="live")
    ap.add_argument("--apply", action="store_true", help="persist signals + review artifacts + snapshot")
    ap.add_argument("--loop", type=int, default=1, help="run N bounded cycles (apply)")
    ap.add_argument("--sleep-seconds", type=int, default=0)
    ap.add_argument("--stop-file", default="")
    ap.add_argument("--status", action="store_true", help="print current paper-signal status and exit")
    ap.add_argument("--select", action="store_true", help="print the memory-ranked top-N universe with reasons and exit")
    ap.add_argument("--ab-report", action="store_true",
                    help="write/read paper exit-mode A/B comparison artifact and exit")
    ap.add_argument("--notify", action="store_true", help="send cards to Telegram IF token+PAPER_CHAT_ID exist")
    ap.add_argument("--pfr-db-path", default="",
                    help="path to strategy_lab.sqlite — activates PFR lane (PAPER_FORWARD_READY records)")
    ap.add_argument("--max-pfr-scan", type=int, default=30,
                    help="max PFR records to inspect per cycle; lower this for fast smoke checks")
    ap.add_argument("--max-observe", type=int, default=None,
                    help="optional max active signals to observe per cycle; use small values for smoke tests")
    ap.add_argument("--public-fetch-timeout", type=float, default=10.0,
                    help="per-request timeout for public OKX candle fetches")
    args = ap.parse_args()
    root = Path(args.private_root)
    tfs = tuple(args.timeframes.split(","))
    pfr_db = Path(args.pfr_db_path) if args.pfr_db_path else None
    provider = OkxPublicMarketDataProvider(timeout=args.public_fetch_timeout)
    if args.status:
        _print_status(root)
        return
    if args.select:
        ranked = cycle.rank_movers(cycle._load_movers(root), cycle.load_memory(root),
                                   lane.load_known_bad(root))
        print(f"search layer: {len(ranked)} candidates (memory-ranked). top {min(20, len(ranked))}:")
        for r in ranked[:20]:
            print(f"  {r.get('symbol'):20s} pr={r.get('_priority'):>7} | {r.get('_reason')}")
        return
    if args.ab_report:
        path = ab_report.write_exit_mode_comparison(root)
        report = ab_report.build_exit_mode_comparison(root)
        print(f"ab_report={path}")
        print(f"matched_pairs={report['matched_pairs']} verdict={report['verdict']} "
              f"delta_sum_net_pct={report['delta_sum_net_pct']}")
        return
    if args.loop > 1:
        if not args.apply:
            ap.error("--loop writes paper-signal state; pass --apply explicitly or use one-cycle dry-run")
        reports = cycle.run_loop(root, cycles=args.loop, sleep_seconds=args.sleep_seconds,
                                 stop_file=args.stop_file, mode=args.mode, timeframes=tfs,
                                 max_new=args.max_signals, apply=True, pfr_db_path=pfr_db,
                                 provider=provider, max_pfr_scan=args.max_pfr_scan,
                                 max_observe=args.max_observe)
        for i, r in enumerate(reports):
            print(f"cycle {i}: observed={r.get('observed')} closed={r.get('closed')} "
                  f"generated={r.get('generated')} state={r.get('state')} gates={r.get('gate_counts')}")
    else:
        r = cycle.run_cycle(root, mode=args.mode, timeframes=tfs, max_new=args.max_signals,
                            apply=args.apply, pfr_db_path=pfr_db, provider=provider,
                            max_pfr_scan=args.max_pfr_scan,
                            max_observe=args.max_observe)
        print(f"mode={args.mode} apply={args.apply} observed={r['observed']} closed={r['closed']} "
              f"generated={r['generated']}\ngate_counts={r['gate_counts']}"
              + (f"\npfr_counts={r['pfr_counts']}" if r.get("pfr_counts") else ""))
        if r["generated"] < 3 and args.apply:
            print("\n--- GATE-BY-GATE (why < 3 new signals) ---")
            for g, n in sorted(r["gate_counts"].items(), key=lambda kv: -kv[1]):
                print(f"  {g}: {n}")
    if args.apply or args.loop > 1:
        _print_status(root)
        if args.notify:
            cards = [render_card(s) for s in store.load_signals(root) if s.status in cycle.ACTIVE]
            print("\ntelegram:", _notify(cards))


if __name__ == "__main__":
    main()
