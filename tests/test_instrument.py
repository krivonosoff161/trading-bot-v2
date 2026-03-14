"""Check BTC-SWAP contract specs — needed to calculate correct order size."""

import asyncio
from src.utils.logger import setup_logger
from src.config import Config
from src.exchange.okx_client import OKXClient


async def main():
    setup_logger()
    config = Config.load()
    client = OKXClient(config.api_key, config.secret_key, config.passphrase, config.is_demo)

    data = await client._get("/api/v5/public/instruments", {"instType": "SWAP", "instId": "BTC-USDT-SWAP"})
    if data.get("data"):
        inst = data["data"][0]
        print(f"\nBTC-USDT-SWAP specs:")
        print(f"  ctVal  (contract value in BTC): {inst.get('ctVal')}")
        print(f"  ctMult (multiplier):            {inst.get('ctMult')}")
        print(f"  minSz  (min order size):        {inst.get('minSz')}")
        print(f"  lotSz  (lot size step):         {inst.get('lotSz')}")
        print(f"  tickSz (price tick):            {inst.get('tickSz')}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
