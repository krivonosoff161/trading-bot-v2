"""
Config loader — reads .env and config.yaml.
Single source of truth for all settings.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

import yaml
from dotenv import load_dotenv

load_dotenv()


@dataclass
class SymbolConfig:
    symbol: str
    order_size: str
    atr_sl_multiplier: float
    atr_tp_multiplier: float
    min_sl_percent: float
    rsi_oversold: float
    rsi_overbought: float

    def as_signal_dict(self) -> dict:
        return {
            "rsi_oversold": self.rsi_oversold,
            "rsi_overbought": self.rsi_overbought,
        }


@dataclass
class Config:
    # OKX API
    api_key: str
    secret_key: str
    passphrase: str
    is_demo: bool

    # Trading
    leverage: int
    poll_interval: int
    symbols: List[SymbolConfig]

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

        symbols = [
            SymbolConfig(
                symbol=s["id"],
                order_size=str(s.get("order_size", "1")),
                atr_sl_multiplier=float(s.get("atr_sl_multiplier", 1.5)),
                atr_tp_multiplier=float(s.get("atr_tp_multiplier", 2.25)),
                min_sl_percent=float(s.get("min_sl_percent", 0.003)),
                rsi_oversold=float(s.get("rsi_oversold", 35)),
                rsi_overbought=float(s.get("rsi_overbought", 65)),
            )
            for s in trading.get("symbols", [])
        ]

        if not symbols:
            raise ValueError("No symbols configured in config.yaml")

        return cls(
            api_key=api_key,
            secret_key=secret_key,
            passphrase=passphrase,
            is_demo=is_demo,
            leverage=int(trading.get("leverage", 5)),
            poll_interval=int(trading.get("poll_interval", 10)),
            symbols=symbols,
        )
