"""
Config loader — reads .env and config.yaml.
Single source of truth for all settings.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # OKX API
    api_key: str
    secret_key: str
    passphrase: str
    is_demo: bool

    # Trading
    symbol: str
    leverage: int

    @classmethod
    def load(cls) -> "Config":
        api_key = os.getenv("OKX_API_KEY", "")
        secret_key = os.getenv("OKX_SECRET_KEY", "")
        passphrase = os.getenv("OKX_PASSPHRASE", "")
        is_demo = os.getenv("OKX_IS_DEMO", "1") == "1"

        if not all([api_key, secret_key, passphrase]):
            raise ValueError("Missing OKX API credentials in .env")

        return cls(
            api_key=api_key,
            secret_key=secret_key,
            passphrase=passphrase,
            is_demo=is_demo,
            symbol="BTC-USDT",
            leverage=5,
        )
