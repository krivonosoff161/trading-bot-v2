"""
Trading Bot V2 — entry point.
"""

import asyncio

from loguru import logger

from src.config import Config
from src.exchange.okx_client import OKXClient
from src.strategy.signal import get_reversal_signal, get_signal
from src.utils.logger import setup_logger


async def run_bot(config: Config, client: OKXClient) -> None:
    logger.info(
        "Bot started | symbol={} leverage={}x demo={} poll={}s",
        config.symbol, config.leverage, config.is_demo, config.poll_interval,
    )

    await client.set_leverage(config.symbol, config.leverage)

    while True:
        try:
            await _tick(config, client)
        except Exception as e:
            logger.error("Tick error | {}", e)
        await asyncio.sleep(config.poll_interval)


async def _tick(config: Config, client: OKXClient) -> None:
    candles = await client.get_candles(config.symbol, bar="5m", limit=100)
    if not candles:
        logger.warning("No candles received | symbol={}", config.symbol)
        return

    # Check open position first
    positions = await client.get_positions(config.symbol)
    open_pos = next((p for p in positions if float(p.get("pos", 0)) != 0), None)

    if open_pos:
        pos_size = float(open_pos.get("pos", 0))
        position_side = "buy" if pos_size > 0 else "sell"

        reversal = get_reversal_signal(candles, position_side)
        if reversal:
            logger.info(
                "Reversal detected | symbol={} position_side={} reason={}",
                config.symbol, position_side, reversal,
            )
            # Re-check position is still open before closing (ghost position protection)
            positions_recheck = await client.get_positions(config.symbol)
            still_open = next((p for p in positions_recheck if float(p.get("pos", 0)) != 0), None)
            if still_open:
                await client.close_position(config.symbol, position_side)
            else:
                logger.info("Position already closed | symbol={}", config.symbol)
        return

    # No open position — check entry signal
    signal = get_signal(candles)

    if not signal["side"]:
        logger.info(
            "No trade | symbol={} reason={} adx={:.1f} rsi={:.1f}",
            config.symbol, signal["reason"], signal["adx"], signal["rsi"],
        )
        return

    # Calculate TP / SL from ATR
    price = float(candles[0][4])  # latest candle close (candles[0] = newest)
    atr = signal["atr"]
    sl_dist = max(atr * config.atr_sl_multiplier, price * config.min_sl_percent)
    tp_dist = sl_dist * config.tp_rr

    if signal["side"] == "buy":
        sl_price = str(round(price - sl_dist, 2))
        tp_price = str(round(price + tp_dist, 2))
    else:
        sl_price = str(round(price + sl_dist, 2))
        tp_price = str(round(price - tp_dist, 2))

    logger.info(
        "Signal | symbol={} side={} reason={} rsi={:.1f} adx={:.1f} "
        "range=[{:.0f}-{:.0f}] mpr={:.0f} atr={:.2f} price={} sl={} tp={}",
        config.symbol, signal["side"], signal["reason"],
        signal["rsi"], signal["adx"],
        signal["range_low"], signal["range_high"], signal["mpr"],
        atr, price, sl_price, tp_price,
    )

    await client.place_market_order(
        config.symbol, signal["side"], config.order_size, tp_price, sl_price,
    )


def main() -> None:
    setup_logger()
    logger.info("Trading Bot V2 starting...")

    try:
        config = Config.load()
        logger.info(
            "Config loaded | symbol={} leverage={}x demo={}",
            config.symbol, config.leverage, config.is_demo,
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
