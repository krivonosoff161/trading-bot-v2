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

# Per-symbol position state: symbol -> {side, entry_price, entry_time, sl, tp}
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
    candles_1m, candles_5m = await asyncio.gather(
        client.get_candles(sym.symbol, bar="1m", limit=100),
        client.get_candles(sym.symbol, bar="5m", limit=100),
    )

    if not candles_1m or not candles_5m:
        logger.warning("No candles received | symbol={}", sym.symbol)
        return

    positions = await client.get_positions(sym.symbol)
    open_pos = next((p for p in positions if float(p.get("pos", 0)) != 0), None)

    had_position = sym.symbol in _open_positions

    if open_pos:
        _open_positions.setdefault(sym.symbol, {})
        pos_size = float(open_pos.get("pos", 0))
        logger.debug("Position open | symbol={} size={}", sym.symbol, pos_size)
        return

    # Position just closed — fetch PnL and log
    if had_position:
        entry = _open_positions.pop(sym.symbol)
        await _log_trade_close(client, sym.symbol, entry)

    # No open position — check entry signal
    signal = get_signal(candles_1m, candles_5m, sym.as_signal_dict())

    if not signal["side"]:
        logger.info(
            "No trade | symbol={} reason={} adx_5m={:.1f} +DI={:.1f} -DI={:.1f} "
            "rsi_1m={:.1f} ema_gap={:.2f}",
            sym.symbol, signal["reason"],
            signal["adx"], signal["plus_di"], signal["minus_di"],
            signal["rsi"], signal["ema_fast"] - signal["ema_slow"],
        )
        write_signal({
            "event": "no_trade",
            "symbol": sym.symbol,
            "reason": signal["reason"],
            "adx": round(signal["adx"], 2),
            "plus_di": round(signal["plus_di"], 2),
            "minus_di": round(signal["minus_di"], 2),
            "rsi": round(signal["rsi"], 2),
            "ema_fast": round(signal["ema_fast"], 4),
            "ema_slow": round(signal["ema_slow"], 4),
            "ema_gap": round(signal["ema_fast"] - signal["ema_slow"], 4),
            "atr": round(signal["atr"], 4),
        })
        return

    # Calculate TP / SL from ATR (1m)
    price = float(candles_1m[0][4])
    atr = signal["atr"]
    sl_dist = max(atr * sym.atr_sl_multiplier, price * sym.min_sl_percent)
    tp_dist = sl_dist * (sym.atr_tp_multiplier / sym.atr_sl_multiplier)

    if signal["side"] == "buy":
        sl_price = str(round(price - sl_dist, 2))
        tp_price = str(round(price + tp_dist, 2))
    else:
        sl_price = str(round(price + sl_dist, 2))
        tp_price = str(round(price - tp_dist, 2))

    logger.info(
        "Signal | symbol={} side={} reason={} rsi_1m={:.1f} adx_5m={:.1f} "
        "+DI={:.1f} -DI={:.1f} atr={:.4f} price={} sl={} tp={}",
        sym.symbol, signal["side"], signal["reason"],
        signal["rsi"], signal["adx"],
        signal["plus_di"], signal["minus_di"],
        atr, price, sl_price, tp_price,
    )

    result = await client.place_market_order(
        sym.symbol, signal["side"], sym.order_size, tp_price, sl_price,
    )

    if result:
        now = datetime.now(timezone.utc)
        entry_time = now.strftime("%Y-%m-%d %H:%M:%S")
        trade_id = f"{sym.symbol}_{now.strftime('%Y%m%dT%H%M%S')}"
        _open_positions[sym.symbol] = {
            "side": signal["side"],
            "entry_price": price,
            "entry_time": entry_time,
            "sl": sl_price,
            "tp": tp_price,
            "atr": round(atr, 4),
            "trade_id": trade_id,
        }
        trade_logger.info(
            "OPEN  | symbol={} side={} entry={} sl={} tp={} atr={:.4f} "
            "adx_5m={:.1f} rsi_1m={:.1f}",
            sym.symbol, signal["side"], price, sl_price, tp_price, atr,
            signal["adx"], signal["rsi"],
        )
        write_signal({
            "event": "open",
            "trade_id": trade_id,
            "symbol": sym.symbol,
            "side": signal["side"],
            "price": price,
            "sl": float(sl_price),
            "tp": float(tp_price),
            "atr": round(atr, 4),
            "ema_fast": round(signal["ema_fast"], 4),
            "ema_slow": round(signal["ema_slow"], 4),
            "ema_gap": round(signal["ema_fast"] - signal["ema_slow"], 4),
            "adx": round(signal["adx"], 2),
            "plus_di": round(signal["plus_di"], 2),
            "minus_di": round(signal["minus_di"], 2),
            "rsi": round(signal["rsi"], 2),
        })


async def _log_trade_close(client: OKXClient, symbol: str, entry: dict) -> None:
    close = await client.get_last_position_close(symbol)
    if not close:
        trade_logger.info(
            "CLOSE | symbol={} side={} entry={} — could not fetch close data",
            symbol, entry.get("side"), entry.get("entry_price"),
        )
        return

    pnl = float(close.get("realizedPnl", 0))
    close_px = float(close.get("closeAvgPx", 0))
    entry_px = float(close.get("openAvgPx", entry.get("entry_price", 0)))
    direction = close.get("direction", entry.get("side", "?"))
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
    write_signal({
        "event": "close",
        "trade_id": entry.get("trade_id"),
        "symbol": symbol,
        "pnl": round(pnl, 4),
        "reason": reason,
        "entry_price": entry_px,
        "close_price": close_px,
    })


def main() -> None:
    setup_logger()
    logger.info("Trading Bot V2 starting...")

    try:
        config = Config.load()
        logger.info(
            "Config loaded | symbols={} leverage={}x demo={}",
            [s.symbol for s in config.symbols], config.leverage, config.is_demo,
        )
    except (ValueError, FileNotFoundError) as e:
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
