"""
Trading Bot V2 — entry point.
"""

import asyncio
from datetime import datetime, timezone

from loguru import logger

from src.config import Config, SymbolConfig
from src.exchange.okx_client import OKXClient
from src.strategy.signal import get_signal
from src.utils.logger import setup_logger, trade_logger, write_signal
from src.utils.telegram import send_message

# Per-symbol position state: symbol -> {side, entry_price, entry_time, sl, tp, ...}
_open_positions: dict = {}


async def run_bot(config: Config, client: OKXClient) -> None:
    symbol_ids = [s.symbol for s in config.symbols]
    logger.info(
        "Bot started | symbols={} leverage={}x demo={} poll={}s",
        symbol_ids, config.leverage, config.is_demo, config.poll_interval,
    )

    for sym in config.symbols:
        await client.set_leverage(sym.symbol, config.leverage)

    while True:
        try:
            await _tick(config, client)
        except Exception as e:
            logger.error("Tick error | {}", e)
        await asyncio.sleep(config.poll_interval)


async def _tick(config: Config, client: OKXClient) -> None:
    for sym in config.symbols:
        try:
            await _tick_symbol(config, client, sym)
        except Exception as e:
            logger.error("Symbol tick error | symbol={} {}", sym.symbol, e)


async def _tick_symbol(config: Config, client: OKXClient, sym: SymbolConfig) -> None:
    candles_1h, candles_15m, candles_5m = await asyncio.gather(
        client.get_candles(sym.symbol, bar="1H", limit=100),
        client.get_candles(sym.symbol, bar="15m", limit=100),
        client.get_candles(sym.symbol, bar="5m",  limit=100),
    )

    if not candles_1h or not candles_15m or not candles_5m:
        logger.warning("No candles received | symbol={}", sym.symbol)
        return

    positions = await client.get_positions(sym.symbol)
    open_pos = next((p for p in positions if float(p.get("pos", 0)) != 0), None)

    had_position = sym.symbol in _open_positions

    if open_pos:
        pos_size = float(open_pos.get("pos", 0))
        logger.debug("Position open | symbol={} size={}", sym.symbol, pos_size)
        return

    # Position just closed — fetch PnL and log
    if had_position:
        entry = _open_positions[sym.symbol]
        closed = await _log_trade_close(client, sym.symbol, entry)
        if closed:
            _open_positions.pop(sym.symbol)
        return  # wait for next tick before new entry

    # No open position — check entry signal
    signal = get_signal(candles_1h, candles_15m, candles_5m, config.as_strategy_dict())

    if not signal["side"]:
        logger.info(
            "No trade | symbol={} reason={} adx_1h={:.1f} +DI={:.1f} -DI={:.1f} vol_ratio={:.2f}",
            sym.symbol, signal["reason"],
            signal["adx"], signal["plus_di"], signal["minus_di"],
            signal["vol_ratio"],
        )
        write_signal({
            "event":     "no_trade",
            "symbol":    sym.symbol,
            "reason":    signal["reason"],
            "adx":       round(signal["adx"], 2),
            "plus_di":   round(signal["plus_di"], 2),
            "minus_di":  round(signal["minus_di"], 2),
            "vol_ratio": round(signal["vol_ratio"], 2),
            "ema_fast":  round(signal["ema_fast"], 4),
            "ema_slow":  round(signal["ema_slow"], 4),
            "atr":       round(signal["atr"], 4),
        })
        return

    # Current price from last completed 5m candle
    price = float(candles_5m[1][4])
    atr   = signal["atr"]

    # SL: structure-based anchor, enforced minimum
    sl_dist_structure = abs(price - signal["setup_sl"])
    sl_dist_min       = max(price * sym.min_sl_percent, config.sl_min_atr * atr)
    sl_dist           = max(sl_dist_structure, sl_dist_min)
    tp_dist           = sl_dist * config.tp_r_multiple

    if signal["side"] == "buy":
        sl_price = str(round(price - sl_dist, 4))
        tp_price = str(round(price + tp_dist, 4))
    else:
        sl_price = str(round(price + sl_dist, 4))
        tp_price = str(round(price - tp_dist, 4))

    logger.info(
        "Signal | symbol={} side={} reason={} adx_1h={:.1f} +DI={:.1f} -DI={:.1f} "
        "vol_ratio={:.2f} atr={:.4f} price={} sl={} tp={}",
        sym.symbol, signal["side"], signal["reason"],
        signal["adx"], signal["plus_di"], signal["minus_di"],
        signal["vol_ratio"], atr, price, sl_price, tp_price,
    )

    result = await client.place_market_order(
        sym.symbol, signal["side"], sym.order_size, tp_price, sl_price,
    )

    if result:
        now        = datetime.now(timezone.utc)
        entry_time = now.strftime("%Y-%m-%d %H:%M:%S")
        trade_id   = f"{sym.symbol}_{now.strftime('%Y%m%dT%H%M%S')}"
        _open_positions[sym.symbol] = {
            "side":        signal["side"],
            "entry_price": price,
            "entry_time":  entry_time,
            "entry_ts":    now.timestamp(),
            "sl":          sl_price,
            "tp":          tp_price,
            "atr":         round(atr, 4),
            "trade_id":    trade_id,
        }
        trade_logger.info(
            "OPEN  | symbol={} side={} entry={} sl={} tp={} atr={:.4f} "
            "adx_1h={:.1f} vol_ratio={:.2f}",
            sym.symbol, signal["side"], price, sl_price, tp_price, atr,
            signal["adx"], signal["vol_ratio"],
        )
        await send_message(
            f"<b>OPEN</b> {sym.symbol}\n"
            f"Side: {signal['side'].upper()}  Entry: {price}\n"
            f"SL: {sl_price}  TP: {tp_price}\n"
            f"ADX: {signal['adx']:.1f}  Vol×: {signal['vol_ratio']:.2f}"
        )
        write_signal({
            "event":     "open",
            "trade_id":  trade_id,
            "symbol":    sym.symbol,
            "side":      signal["side"],
            "price":     price,
            "sl":        float(sl_price),
            "tp":        float(tp_price),
            "atr":       round(atr, 4),
            "adx":       round(signal["adx"], 2),
            "plus_di":   round(signal["plus_di"], 2),
            "minus_di":  round(signal["minus_di"], 2),
            "vol_ratio": round(signal["vol_ratio"], 2),
            "ema_fast":  round(signal["ema_fast"], 4),
            "ema_slow":  round(signal["ema_slow"], 4),
        })


async def _log_trade_close(client: OKXClient, symbol: str, entry: dict) -> bool:
    """Returns True if close was logged, False if no matching record yet (retry next tick)."""
    records = await client.get_last_position_close(symbol)
    if not records:
        trade_logger.warning(
            "CLOSE waiting | symbol={} — no positions-history data",
            symbol,
        )
        return False

    entry_ts_ms    = int(float(entry.get("entry_ts", 0)) * 1000)
    entry_price    = float(entry.get("entry_price") or 0)
    entry_atr      = float(entry.get("atr") or 0)
    expected_dir   = "long" if entry.get("side") == "buy" else "short"
    time_skew_ms   = 30_000
    price_tol      = max(entry_price * 0.001, entry_atr * 0.5, 0.01)

    candidates = []
    for rec in records:
        if rec.get("direction") != expected_dir:
            continue
        open_avg_px  = float(rec.get("openAvgPx") or 0)
        close_avg_px = float(rec.get("closeAvgPx") or 0)
        rec_utime    = int(rec.get("uTime") or 0)
        if open_avg_px <= 0 or close_avg_px <= 0:
            continue
        if entry_ts_ms and rec_utime < entry_ts_ms - time_skew_ms:
            continue
        if entry_price and abs(open_avg_px - entry_price) > price_tol:
            continue
        candidates.append(rec)

    if not candidates:
        trade_logger.warning(
            "CLOSE waiting | symbol={} — no matching record yet, will retry next tick",
            symbol,
        )
        return False

    # Pick best match: closest in time, then by price proximity
    close = min(candidates, key=lambda r: (
        abs(int(r.get("uTime") or 0) - entry_ts_ms),
        abs(float(r.get("openAvgPx") or 0) - entry_price),
    ))

    pnl        = float(close.get("realizedPnl", 0))
    close_px   = float(close.get("closeAvgPx", 0))
    entry_px   = float(close.get("openAvgPx") or entry_price)
    direction  = close.get("direction", entry.get("side", "?"))
    close_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Determine close reason: TP / SL / manual
    tp = float(entry.get("tp", 0))
    sl = float(entry.get("sl", 0))
    if tp and abs(close_px - tp) / tp < 0.002:
        reason = "TP"
    elif sl and abs(close_px - sl) / sl < 0.002:
        reason = "SL"
    else:
        reason = "manual"

    pnl_sign = "+" if pnl >= 0 else ""
    trade_logger.info(
        "CLOSE | symbol={} side={} entry={} close={} pnl={}{:.4f} reason={} "
        "open_at={} close_at={}",
        symbol, direction, entry_px, close_px,
        pnl_sign, pnl, reason,
        entry.get("entry_time"), close_time,
    )
    await send_message(
        f"<b>CLOSE</b> {symbol} — {reason}\n"
        f"Side: {direction.upper()}  Entry: {entry_px}  Close: {close_px}\n"
        f"PnL: <b>{pnl_sign}{pnl:.4f} USDT</b>"
    )
    write_signal({
        "event":       "close",
        "trade_id":    entry.get("trade_id"),
        "symbol":      symbol,
        "pnl":         round(pnl, 4),
        "reason":      reason,
        "entry_price": entry_px,
        "close_price": close_px,
    })
    return True


def main() -> None:
    setup_logger()
    logger.info("Trading Bot V2 starting...")

    try:
        config = Config.load()
        logger.info(
            "Config loaded | symbols={} leverage={}x demo={}",
            [s.symbol for s in config.symbols], config.leverage, config.is_demo,
        )
    except (ValueError, FileNotFoundError, KeyError) as e:
        logger.error("Config error: {}", e)
        return

    client = OKXClient(config.api_key, config.secret_key, config.passphrase, config.is_demo)

    try:
        asyncio.run(run_bot(config, client))
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        asyncio.run(client.close())


if __name__ == "__main__":
    main()
