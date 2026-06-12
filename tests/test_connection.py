"""Quick connection test — run once to verify API keys work."""

import asyncio
import pytest

pytestmark = pytest.mark.live


async def main():
    from src.config import Config
    from src.exchange.okx_client import OKXClient
    from src.utils.logger import setup_logger

    setup_logger()
    config = Config.load()
    client = OKXClient(config.api_key, config.secret_key, config.passphrase, config.is_demo)

    print("\n--- Testing OKX connection ---")

    price = await client.get_price("BTC-USDT")
    print(f"BTC price: ${price:,.2f}" if price else "ERROR: no price")

    balance = await client.get_balance()
    print(f"USDT balance: ${balance:,.2f}" if balance else "ERROR: no balance")

    await client.close()
    print("--- Test complete ---\n")


if __name__ == "__main__":
    asyncio.run(main())
