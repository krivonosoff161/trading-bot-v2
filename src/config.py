"""
Config loader — reads .env and config.yaml.
Single source of truth for all settings.
"""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
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
    order_size: str
    poll_interval: int

    # Strategy
    atr_sl_multiplier: float
    min_sl_percent: float
    tp_rr: float

    @classmethod
    def load(cls) -> "Config":
        api_key = os.getenv("OKX_API_KEY", "")
        secret_key = os.getenv("OKX_SECRET_KEY", "")
        passphrase = os.getenv("OKX_PASSPHRASE", "")
        is_demo = os.getenv("OKX_IS_DEMO", "1") == "1"

        if not all([api_key, secret_key, passphrase]):
            raise ValueError("Missing OKX API credentials in .env")

        config_path = Path(__file__).parent.parent / "config.yaml"
        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f)

        trading = cfg.get("trading", {})
        strategy = cfg.get("strategy", {})

        return cls(
            api_key=api_key,
            secret_key=secret_key,
            passphrase=passphrase,
            is_demo=is_demo,
            symbol=trading.get("symbol", "BTC-USDT"),
            leverage=int(trading.get("leverage", 5)),
            order_size=str(trading.get("order_size", "1")),
            poll_interval=int(trading.get("poll_interval", 10)),
            atr_sl_multiplier=float(strategy.get("atr_sl_multiplier", 1.5)),
            min_sl_percent=float(strategy.get("min_sl_percent", 0.006)),
            tp_rr=float(strategy.get("tp_rr", 1.5)),
        )
