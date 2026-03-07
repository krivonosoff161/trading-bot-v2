"""
Trading Bot V2 — entry point.
"""

import asyncio

from loguru import logger

from src.config import Config, SymbolConfig
from src.exchange.okx_client import OKXClient
from src.strategy.signal import get_signal
from src.utils.logger import setup_logger


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

    # Check open position first
    positions = await client.get_positions(sym.symbol)
    open_pos = next((p for p in positions if float(p.get("pos", 0)) != 0), None)

    if open_pos:
        # Position already open — OCO handles TP/SL, nothing to do
        pos_size = float(open_pos.get("pos", 0))
        logger.debug(
            "Position open | symbol={} size={}", sym.symbol, pos_size,
        )
        return

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
        return

    # Calculate TP / SL from ATR (1m)
    price = float(candles_1m[0][4])  # latest 1m candle close (candles_1m[0] = newest)
    atr = signal["atr"]
    sl_dist = max(atr * sym.atr_sl_multiplier, price * sym.min_sl_percent)
    tp_dist = sl_dist * (sym.atr_tp_multiplier / sym.atr_sl_multiplier)  # TP = SL * R:R ratio

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

    await client.place_market_order(
        sym.symbol, signal["side"], sym.order_size, tp_price, sl_price,
    )


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
