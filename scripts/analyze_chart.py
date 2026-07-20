"""
Chart Analyzer — thin orchestrator around REST data fetch, signal engine and chart renderer.

Usage:
    python scripts/analyze_chart.py --symbol XRP-USDT --captured-at "2026-03-09T11:42:35Z"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.utils.runtime_root import load_runtime_dotenv  # noqa: E402

from src.exchange.okx_client import OKXClient  # noqa: E402
from src.strategy.chart_renderer import generate_chart_png  # noqa: E402
from src.strategy.signal_engine import (  # noqa: E402
    _format_telegram,
    _json_safe,
    build_analysis_snapshot,
    compute_signal,
    confirm_label,
    ts_to_ms,
)


def load_strategy_params() -> dict:
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("strategy", {})


def load_scout_bundle() -> dict | None:
    """Latest scout market context (logs/scout/bundle_latest.json) or None if unavailable."""
    bundle_path = Path(__file__).parent.parent / "logs" / "scout" / "bundle_latest.json"
    try:
        with open(bundle_path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _safe_print(text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.buffer.write((str(text) + "\n").encode(encoding, errors="replace"))
        sys.stdout.buffer.flush()


def _env_enabled(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def should_use_llm_for_delivery(snapshot: dict) -> bool:
    """Return whether manual analysis should spend an LLM call for the card."""
    ctx = snapshot.get("llm_context", {}) if isinstance(snapshot, dict) else {}
    entry_signal = str(ctx.get("entry_signal") or "").upper()
    if entry_signal == "NO_TRADE":
        return _env_enabled("PRODUCT_ANALYZER_LLM_FOR_NO_TRADE", default=False)
    return True


def manual_chart_plan(result: object) -> dict[str, object]:
    """Document the chart/decision timeframes used by the legacy analyzer.

    The current main analyzer computes entry, stop, and take-profit levels from
    the 15m execution frame. 5m is only a trigger confirmation frame; 1H/4H are
    context/veto frames. Rendering a 1H/4H chart for this engine would make the
    levels look cleaner but less truthful.
    """
    trade_style = str(getattr(result, "trade_style", "") or "NO_TRADE")
    return {
        "primary_timeframe": "15m",
        "trigger_timeframe": "5m",
        "context_timeframes": ["1H", "4H"],
        "render_label": "15m execution",
        "trade_style": trade_style,
        "reason": "legacy_main_engine_levels_are_15m; 1H/4H_are_context_veto_frames",
    }


def _manual_delivery_text(result: object) -> str:
    status_map = {
        "ENTRY": "вход сформирован",
        "WAIT": "ждем подтверждение",
        "NO_TRADE": "сделки нет",
    }
    side_map = {"buy": "LONG", "sell": "SHORT", None: "нет направления"}
    regime_map = {
        "TRENDING": "тренд",
        "DRIFT": "медленный дрейф",
        "RANGING": "диапазон",
        "CHOPPY": "пила",
    }
    reason_map = {
        "conditions_not_met": "условия входа не собраны",
        "funding_warn": "есть предупреждение по funding",
        "funding_block": "funding блокирует вход",
        "oi_weak": "движение не подтверждено открытым интересом",
        "vwap_veto": "цена не прошла фильтр VWAP",
        "missing_levels": "нет надежных уровней входа/стопа/цели",
        "four_h_conflict": "старший таймфрейм конфликтует с идеей",
        "strong_4h_veto": "4h-контекст против сделки",
        "perp_div_short_veto": "perp-дивергенция против short",
        "drift_adx1h_veto": "слабый 1h-импульс для drift-сценария",
    }

    def price(value: object) -> str:
        if value is None:
            return "n/a"
        try:
            return f"{float(value):.8g}"
        except (TypeError, ValueError):
            return str(value)

    symbol = str(getattr(result, "symbol", "") or "UNKNOWN")
    signal = str(getattr(result, "entry_signal", "") or "NO_TRADE")
    side = getattr(result, "side", None)
    regime = str(getattr(result, "regime", "") or "UNKNOWN")
    reason = str(getattr(result, "drop_reason", "") or "conditions_not_met")
    max_hold = getattr(result, "max_hold_min", None)

    lines = [
        f"{symbol}",
        f"Статус: {status_map.get(signal, signal)}",
        f"Направление: {side_map.get(side, str(side or 'нет направления'))}",
        f"Рынок: {regime_map.get(regime, regime)}",
        "",
    ]
    if signal in {"ENTRY", "WAIT"}:
        lines.extend(
            [
                f"Вход: {price(getattr(result, 'entry_price', None))}",
                f"Стоп: {price(getattr(result, 'sl_price', None))}",
                f"Цель 1: {price(getattr(result, 'tp1_price', None))}",
                f"Цель 2: {price(getattr(result, 'tp2_price', None))}",
                f"Макс. удержание: {max_hold or 'n/a'} мин",
                "",
                "Что делать: проверять руками; это не команда к входу.",
            ]
        )
    else:
        lines.extend(
            [
                "Что делать: не открывать сделку по этому анализу.",
                f"Почему: {reason_map.get(reason, reason)}.",
            ]
        )
    lines.extend(["", "Это аналитика, не ордер и не инвест-рекомендация."])
    return "\n".join(lines)


async def run(
    symbol: str,
    captured_at_iso: str,
    limit: int,
    image_path: str = None,
    send_telegram: bool = False,
    output_dir: Path = None,
) -> dict | None:
    api_key = os.getenv("OKX_API_KEY", "")
    secret_key = os.getenv("OKX_SECRET_KEY", "")
    passphrase = os.getenv("OKX_PASSPHRASE", "")
    is_demo = os.getenv("OKX_IS_DEMO", "1") == "1"

    client = OKXClient(api_key, secret_key, passphrase, is_demo)
    params = load_strategy_params()
    captured_ms = ts_to_ms(captured_at_iso)
    after_ts = captured_ms + 1
    is_live = abs(datetime.now(timezone.utc).timestamp() * 1000 - captured_ms) <= 15 * 60 * 1000

    print(f"Fetching candles for {symbol} ending at {captured_at_iso} ...")
    try:
        raw_4h, raw_1h, raw_15m, raw_5m, funding, oi, oi_hist, books5, trades100, _raw_mark15, raw_idx15 = await asyncio.gather(
            client.get_history_candles(symbol, "4H", after=after_ts, limit=60),
            client.get_history_candles(symbol, "1H", after=after_ts, limit=limit),
            client.get_history_candles(symbol, "15m", after=after_ts, limit=limit),
            client.get_history_candles(symbol, "5m", after=after_ts, limit=limit),
            client.get_funding_rate(symbol),
            client.get_open_interest(symbol),
            client.get_oi_history(symbol, period="1H", limit=5),
            client.get_books(symbol, size=5) if is_live else asyncio.sleep(0, result=None),
            client.get_trades(symbol, limit=100) if is_live else asyncio.sleep(0, result=[]),
            client.get_history_mark_price_candles(symbol, "15m", after=after_ts, limit=10),
            client.get_history_index_candles(symbol, "15m", after=after_ts, limit=10),
        )
    finally:
        await client.close()

    if not raw_1h or not raw_15m or not raw_5m:
        print("ERROR: No candle data returned. Check symbol and captured-at timestamp.")
        return None
    if len(raw_1h) < 50 or len(raw_15m) < 50 or len(raw_5m) < 20:
        print(f"ERROR: Not enough candles — 1H:{len(raw_1h)} 15m:{len(raw_15m)} 5m:{len(raw_5m)} (need 50/50/20)")
        return None

    print(f"Latest bar status:  1H={confirm_label(raw_1h)}  15m={confirm_label(raw_15m)}  5m={confirm_label(raw_5m)}\n")
    if not is_live:
        funding = None
        oi = None
        oi_hist = None
        books5 = None
        trades100 = []

    result = compute_signal(
        candles_15m=raw_15m,
        candles_1h=raw_1h,
        candles_4h=raw_4h,
        candles_5m=raw_5m,
        symbol=symbol,
        config=params,
        captured_at_iso=captured_at_iso,
        funding=funding,
        open_interest=oi,
        oi_history=oi_hist,
        books5=books5,
        trades100=trades100,
        raw_idx15=raw_idx15,
    )

    _safe_print(result.report_text)
    _safe_print("\n── ENGINE SUMMARY " + "─" * 44)
    _safe_print(result.engine_summary)

    ts_label = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = Path(output_dir) if output_dir is not None else Path(__file__).parent / "analysis_output" / ts_label
    run_dir.mkdir(parents=True, exist_ok=True)

    report_path = run_dir / f"{symbol}_report.md"
    snap_path = run_dir / f"{symbol}_snapshot.json"
    png_path = run_dir / f"{symbol}_chart.png"
    report_path.write_text(result.report_text + "\n", encoding="utf-8")

    chart_plan = manual_chart_plan(result)
    snapshot = build_analysis_snapshot(symbol, captured_at_iso, result, open_interest=oi)
    snapshot["chart_plan"] = chart_plan
    snap_path.write_text(json.dumps(_json_safe(snapshot), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved: {report_path}")
    print(f"Saved: {snap_path}")

    levels = {
        "sl": result.sl_price,
        "tp1": result.tp1_price,
        "tp2": result.tp2_price,
        "entry_price": result.entry_price,
    } if result.sl_price else {}
    generate_chart_png(
        raw_15m,
        result.indicators,
        symbol,
        captured_at_iso,
        str(png_path),
        llm_levels=levels,
        entry_signal=result.entry_signal,
        direction=result.side,
        trade_style=result.trade_style,
        tf_label=str(chart_plan["render_label"]),
    )

    # Analyzer is a descriptive second-opinion product, not a trade gate.
    # NO_TRADE uses the deterministic engine template by default to avoid slow/costly LLM calls.
    llm_text = None
    if should_use_llm_for_delivery(snapshot):
        from src.utils.llm_formatter import generate_client_text

        llm_image = str(png_path) if png_path.exists() else image_path
        market_context = load_scout_bundle() if is_live else None
        llm_text = await generate_client_text(
            symbol, captured_at_iso, snapshot, llm_image,
            client_summary=None, market_context=market_context,
        )
    else:
        print("LLM formatter skipped: NO_TRADE template path")

    delivery_text = llm_text if llm_text else _manual_delivery_text(result)
    from src.research_lab.manual_farm_context import manual_farm_context_text

    farm_context = manual_farm_context_text(symbol)
    if farm_context:
        delivery_text = f"{delivery_text}\n\n{farm_context}"
    summary_path = run_dir / f"{symbol}_client_summary.txt"
    summary_path.write_text(delivery_text, encoding="utf-8")
    print(f"Saved: {summary_path}")

    if send_telegram:
        from src.utils.telegram import send_message

        tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip("'\"")
        tg_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not tg_token or not tg_chat:
            print("Telegram: not sent — TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        else:
            import html as _html

            tg_text = _html.escape(delivery_text) if llm_text else _format_telegram(delivery_text)
            await send_message(tg_text)
            if image_path and os.path.exists(image_path):
                from src.utils.telegram import send_photo_to

                await send_photo_to(tg_chat, image_path)
            print("Telegram: sent.")

    _safe_print(f"\nРезультаты: {run_dir}")
    return result.to_legacy_result(delivery_text)


def main() -> None:
    # Standalone entrypoint boundary. When imported by telegram_bot, that
    # canonical entrypoint has already loaded runtime configuration.
    load_runtime_dotenv(ROOT)
    parser = argparse.ArgumentParser(description="Chart Analyzer — FAST/SWING engine + OKX data")
    parser.add_argument("--symbol", required=True, help="e.g. XRP-USDT")
    parser.add_argument("--captured-at", required=True, dest="captured_at", help="ISO UTC timestamp e.g. 2026-03-09T11:42:35Z")
    parser.add_argument("--image", default=None, help="Path to screenshot (optional)")
    parser.add_argument("--limit", type=int, default=100, help="Candles to fetch per timeframe (default 100)")
    parser.add_argument("--send-telegram", action="store_true", dest="send_telegram", help="Send client summary to Telegram after analysis")
    args = parser.parse_args()
    asyncio.run(run(args.symbol, args.captured_at, args.limit, args.image, args.send_telegram))


if __name__ == "__main__":
    main()
