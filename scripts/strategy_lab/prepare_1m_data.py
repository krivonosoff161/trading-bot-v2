# -*- coding: utf-8 -*-
"""Prepare 1m data on demand for active Strategy Lab needs. Dry-run by default.

Derives the capped 1m windows needed by current event sweeps / queued specs (and an
optional manual --symbol/--start/--end), checks the local cache, and — only with
--apply and a configured provider — downloads and writes just those windows.

The default provider is `null` (no network): with --apply it reports
"provider not configured / no data written" and exits cleanly. This never trades,
never calls order/account endpoints, and never calls a paid LLM.

    python -m scripts.strategy_lab.prepare_1m_data --dry-run
    python -m scripts.strategy_lab.prepare_1m_data --symbol BTC_USDT_SWAP --start 2025-01-30T00:00 --end 2025-01-30T06:00 --dry-run
    python -m scripts.strategy_lab.prepare_1m_data --apply        # null provider -> nothing written
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.data_cache import resolve_requirements  # noqa: E402
from src.research_lab.data_prepare import prepare, write_prepare_report  # noqa: E402
from src.research_lab.data_requirements import collect_requirements, manual_requirement  # noqa: E402
from src.research_lab.event_microscope import derive_limits  # noqa: E402
from src.research_lab.market_data_provider import get_provider  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, one_minute_data_dir, resolve_private_root  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402


def _night_mode(flag: bool) -> bool:
    return flag or os.getenv("STRATEGY_LAB_NIGHT_MODE", "").strip().lower() in {"1", "true", "yes"}


def _allow_synthetic() -> bool:
    return os.getenv("STRATEGY_LAB_ALLOW_SYNTHETIC", "").strip().lower() in {"1", "true", "yes"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", help="Manual request: symbol (e.g. BTC_USDT_SWAP)")
    ap.add_argument("--start", help="Manual request: UTC start, e.g. 2025-01-30T00:00")
    ap.add_argument("--end", help="Manual request: UTC end, e.g. 2025-01-30T06:00")
    ap.add_argument("--max-symbols", type=int, help="Lower the symbol cap (cannot exceed policy)")
    ap.add_argument("--max-windows", type=int, help="Lower the event-window cap (cannot exceed policy)")
    ap.add_argument(
        "--provider", default="null",
        help="null (default, no network) | okx-public (public candles, no key) | synthetic (offline test)",
    )
    ap.add_argument("--night-mode", action="store_true")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Print plan, write nothing (default)")
    mode.add_argument("--apply", action="store_true", help="Download + write the missing windows")
    args = ap.parse_args()

    profiles = load_timeframe_profiles()
    policy = load_resource_policy(night_mode=_night_mode(args.night_mode))
    limits = derive_limits(profiles, policy)
    if args.max_symbols is not None:
        limits = dataclasses.replace(limits, max_symbols=max(0, min(limits.max_symbols, args.max_symbols)))
    if args.max_windows is not None:
        limits = dataclasses.replace(limits, max_event_windows=max(0, min(limits.max_event_windows, args.max_windows)))

    private_root = resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output)

    manual = None
    if args.symbol or args.start or args.end:
        if not (args.symbol and args.start and args.end):
            print("manual request needs --symbol, --start and --end together")
            sys.exit(2)
        try:
            manual = manual_requirement(args.symbol, args.start, args.end, limits=limits)
        except ValueError as exc:
            print(f"invalid manual request: {exc}")
            sys.exit(2)

    requirements = collect_requirements(private_root, limits=limits, manual=manual)
    checks = resolve_requirements(requirements, data_dir=one_minute_data_dir(private_root))

    allow_synth = _allow_synthetic()
    provider = get_provider(args.provider, allow_synthetic=allow_synth)
    report = prepare(
        checks, provider=provider, private_root=private_root,
        apply=args.apply, allow_public_output=args.allow_public_output,
    )
    write_prepare_report(private_root, report, allow_public_output=args.allow_public_output)
    _print_report(report, limits, args, allow_synth)


def _print_report(report, limits, args, allow_synth) -> None:
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Strategy Lab - prepare 1m data ({mode})")
    print("-" * 52)
    print(f"provider : {report.provider} (configured: {str(report.provider_configured).lower()})")
    if report.provider == "okx-public":
        print("           OKX public candles (read-only, no key); fetched only on --apply")
    if args.provider.strip().lower() == "synthetic" and not allow_synth:
        print("           synthetic provider requires STRATEGY_LAB_ALLOW_SYNTHETIC=1 (offline test data)")
    print(f"limits   : symbols<={limits.max_symbols}, windows<={limits.max_event_windows}, "
          f"bars/window<={limits.max_bars_per_window}")
    print(f"required : {len(report.items)} window(s)")
    for item in report.items[:30]:
        extra = f" -> {item['action']}"
        if item.get("written_label"):
            extra += f" ({item['bars_written']} bars -> {item['written_label']})"
        print(f"  [{item['status']}] {item['symbol']} {item['start_utc']} .. {item['end_utc']} "
              f"(bars~{item['bars']}){extra}")
    counts = ", ".join(f"{k}: {v}" for k, v in sorted(report.counts.items())) or "none"
    print("-" * 52)
    print(f"counts   : {counts}")
    if args.apply:
        if report.downloaded:
            print(f"written  : {report.downloaded} file(s) under private root market_data/1m/")
        elif not report.provider_configured and report.to_dict()["missing"] > 0:
            print("provider not configured / no data written")
        else:
            print("nothing to write")
    else:
        print(f"would download: {report.would_download} window(s) (dry-run wrote nothing)")
        print("to actually fetch (only with a configured provider):")
        print("  python -m scripts.strategy_lab.prepare_1m_data --apply")
    print("safety   : public market-data only; no live trading, no order endpoints, no account access, no paid LLM")


if __name__ == "__main__":
    main()
