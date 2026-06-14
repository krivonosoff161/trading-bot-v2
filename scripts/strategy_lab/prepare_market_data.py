# -*- coding: utf-8 -*-
"""Operator-facing market-data prepare command for 15m/1h/4h/1d.

Dry-run is the default: no network, no files. Apply fetches only the requested
bounded window via an explicitly selected market-data-only provider and writes
canonical OHLCV JSON under the private research root.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.data_prepare import (  # noqa: E402
    MarketDataPrepareItem,
    prepare_market_data,
    write_market_data_prepare_report,
)
from src.research_lab.market_data_provider import get_provider  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402
from src.research_lab.universe import load_universe  # noqa: E402

_SUPPORTED = ("15m", "1h", "4h", "1d")
_DEFAULT_DAYS = {"15m": 14, "1h": 60, "4h": 180, "1d": 1000}
_TF_SECONDS = {"15m": 15 * 60, "1h": 60 * 60, "4h": 4 * 60 * 60, "1d": 24 * 60 * 60}
_MAX_BARS_PER_WINDOW = 2_000


def _parse_utc(value: str) -> dt.datetime:
    text = str(value).strip()
    if not text:
        raise ValueError("empty datetime")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _ms(value: dt.datetime) -> int:
    return int(value.timestamp() * 1000)


def _norm_symbol(symbol: str) -> str:
    return str(symbol).replace("-", "_").replace("/", "_").upper()


def _split_symbols(raw: str) -> list[str]:
    out: list[str] = []
    for chunk in str(raw or "").replace(";", ",").split(","):
        sym = _norm_symbol(chunk.strip())
        if sym and sym not in out:
            out.append(sym)
    return out


def _window(timeframe: str, start: str, end: str, days: int | None) -> tuple[int, int]:
    now = dt.datetime.now(dt.timezone.utc)
    if bool(start) != bool(end):
        raise ValueError("--start and --end must be provided together")
    if start and end:
        start_dt = _parse_utc(start)
        end_dt = _parse_utc(end)
    else:
        span_days = int(days if days is not None else _DEFAULT_DAYS[timeframe])
        end_dt = now
        start_dt = end_dt - dt.timedelta(days=span_days)
    if end_dt <= start_dt:
        raise ValueError("--end must be after --start")
    return _ms(start_dt), _ms(end_dt)


def _cap_window(timeframe: str, start_ts: int, end_ts: int) -> tuple[int, int, int]:
    interval_ms = _TF_SECONDS[timeframe] * 1000
    bars = (end_ts - start_ts) // interval_ms + 1
    if bars <= _MAX_BARS_PER_WINDOW:
        return start_ts, end_ts, int(bars)
    capped_start = end_ts - (_MAX_BARS_PER_WINDOW - 1) * interval_ms
    return int(capped_start), int(end_ts), _MAX_BARS_PER_WINDOW


def build_items(args: argparse.Namespace) -> tuple[list[MarketDataPrepareItem], list[str]]:
    timeframe = str(args.timeframe).strip().lower()
    profiles = load_timeframe_profiles()
    profile = profiles.get(timeframe)
    if profile.trigger_only:
        raise ValueError("1m is trigger-only; use scripts.strategy_lab.prepare_1m_data")
    if timeframe not in _SUPPORTED:
        raise ValueError(f"prepare_market_data supports {', '.join(_SUPPORTED)}, got {timeframe!r}")

    symbols = _split_symbols(args.symbols)
    if args.symbol:
        symbols.extend(s for s in _split_symbols(args.symbol) if s not in symbols)
    if not symbols:
        universe = load_universe()
        group = str(args.universe).strip()
        symbols = list(universe.symbols_in(group))
        if not symbols:
            known = ", ".join(universe.groups)
            raise ValueError(f"unknown or empty universe group {group!r} (known: {known})")

    max_symbols = min(max(1, int(args.max_symbols or profile.max_symbols_per_cycle)), profile.max_symbols_per_cycle)
    original_count = len(symbols)
    symbols = symbols[:max_symbols]
    skipped: list[str] = []
    if original_count > len(symbols):
        skipped.append(f"symbol_cap:{original_count}->{len(symbols)}")

    start_ts, end_ts = _window(timeframe, args.start, args.end, args.days)
    capped_start, capped_end, bars = _cap_window(timeframe, start_ts, end_ts)
    if capped_start != start_ts:
        skipped.append(f"window_cap:{bars}_bars")

    return [
        MarketDataPrepareItem(symbol=s, timeframe=timeframe, start_ts=capped_start, end_ts=capped_end)
        for s in symbols
    ], skipped


def _print_report(report, *, report_path: Path, skipped: list[str]) -> None:
    mode = "APPLY" if report.apply else "DRY-RUN"
    print(f"Strategy Lab market-data prepare ({mode})")
    print("-" * 56)
    print(f"timeframe          : {report.timeframe}")
    print(f"provider           : {report.provider} (configured={report.provider_configured})")
    print(f"items              : {len(report.items)}")
    print(f"would download     : {report.would_download}")
    print(f"downloaded         : {report.downloaded}")
    print(f"files written      : {len(report.files_written)}")
    if skipped:
        print(f"operator caps      : {', '.join(skipped)}")
    if report.skipped:
        reasons: dict[str, int] = {}
        for item in report.skipped:
            reasons[item.get("reason", "unknown")] = reasons.get(item.get("reason", "unknown"), 0) + 1
        print("skipped            : " + ", ".join(f"{k}: {v}" for k, v in sorted(reasons.items())))
    print(f"report             : {report_path.relative_to(report_path.parents[2]) if len(report_path.parents) > 2 else report_path.name}")
    print("safety             : market data only; no orders; no live trading; dry-run by default")
    if not report.apply:
        print("next               : add --apply and explicit --provider okx-public to fetch public candles")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", required=True, choices=_SUPPORTED)
    ap.add_argument("--universe", default="core_market")
    ap.add_argument("--symbol", default="")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--start", default="", help="UTC ISO timestamp, e.g. 2026-06-10T00:00:00Z")
    ap.add_argument("--end", default="", help="UTC ISO timestamp, e.g. 2026-06-10T03:00:00Z")
    ap.add_argument("--days", type=int, default=None, help="Lookback days when start/end are omitted")
    ap.add_argument("--provider", default="null", choices=["null", "okx-public", "synthetic"])
    ap.add_argument("--max-symbols", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="Explicit no-fetch mode (default)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--night-mode", action="store_true", help="Operator label only; policy caps still come from timeframe profile")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    args = ap.parse_args()

    private_root = resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output)
    items, skipped = build_items(args)
    allow_synthetic = os.getenv("STRATEGY_LAB_ALLOW_SYNTHETIC", "").strip().lower() in {"1", "true", "yes", "on"}
    provider = get_provider(args.provider, allow_synthetic=allow_synthetic)
    report = prepare_market_data(
        items,
        provider=provider,
        private_root=private_root,
        timeframe=args.timeframe,
        apply=bool(args.apply),
        allow_public_output=args.allow_public_output,
    )
    report_path = write_market_data_prepare_report(
        private_root, report, allow_public_output=args.allow_public_output,
    )
    _print_report(report, report_path=report_path, skipped=skipped)


if __name__ == "__main__":
    main()
