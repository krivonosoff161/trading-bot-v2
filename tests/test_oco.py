"""
Test OCO order on DEMO account.
Opens BTC LONG 1 contract with TP +0.5% and SL -0.3%, then closes it.
"""

import asyncio
from src.utils.logger import setup_logger
from src.config import Config
from src.exchange.okx_client import OKXClient


async def main():
    setup_logger()
    config = Config.load()
    client = OKXClient(config.api_key, config.secret_key, config.passphrase, config.is_demo)

    symbol = "BTC-USDT"

    # Get current price
    price = await client.get_price(symbol)
    if not price:
        print("ERROR: cannot get price")
        return

    print(f"\nBTC price: ${price:,.1f}")

    # Set leverage
    await client.set_leverage(symbol, config.leverage)

    # Calculate TP and SL (rounded to tickSz=0.1)
    tp_price = round(price * 1.005, 1)   # +0.5%
    sl_price = round(price * 0.997, 1)   # -0.3%
    print(f"TP: ${tp_price:,.1f} (+0.5%)")
    print(f"SL: ${sl_price:,.1f} (-0.3%)")

    # Place OCO order — 1 contract (0.01 BTC)
    result = await client.place_market_order(
        symbol=symbol,
        side="buy",
        size="1",
        tp_price=str(tp_price),
        sl_price=str(sl_price),
    )

    if result:
        print(f"\nOrder placed! ordId={result.get('ordId')}")
        print("Waiting 5 seconds...")
        await asyncio.sleep(5)

        # Check position
        positions = await client.get_positions(symbol)
        if positions:
            pos = positions[0]
            print(f"Position: side={pos.get('posSide')} size={pos.get('pos')} upl={pos.get('upl')}")

            # Close position
            print("\nClosing position...")
            closed = await client.close_position(symbol, "long")
            print(f"Closed: {closed}")
        else:
            print("No position found (may have been closed by TP/SL already)")
    else:
        print("ERROR: order failed")

    await client.close()
    print("\n--- OCO test complete ---\n")


if __name__ == "__main__":
    asyncio.run(main())
