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
    target_margin_usdt: float   # margin per trade; notional = margin * leverage
    min_sl_percent: float


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

    # Strategy E parameters (global, not per-symbol)
    ema_fast: int
    ema_slow: int
    adx_period: int
    adx_threshold_1h: float
    pullback_touch_atr: float
    pullback_volume_bars: int
    pullback_volume_factor: float
    breakout_lookback_5m: int
    trigger_volume_ma_period: int
    trigger_volume_factor: float
    sl_buffer_atr: float
    sl_min_atr: float
    tp_r_multiple: float

    def as_strategy_dict(self) -> dict:
        """All strategy parameters passed to get_signal()."""
        return {
            "ema_fast":               self.ema_fast,
            "ema_slow":               self.ema_slow,
            "adx_period":             self.adx_period,
            "adx_threshold_1h":       self.adx_threshold_1h,
            "pullback_touch_atr":     self.pullback_touch_atr,
            "pullback_volume_bars":   self.pullback_volume_bars,
            "pullback_volume_factor": self.pullback_volume_factor,
            "breakout_lookback_5m":   self.breakout_lookback_5m,
            "trigger_volume_ma_period": self.trigger_volume_ma_period,
            "trigger_volume_factor":  self.trigger_volume_factor,
            "sl_buffer_atr":          self.sl_buffer_atr,
            "sl_min_atr":             self.sl_min_atr,
        }

    @classmethod
    def load(cls) -> "Config":
        api_key    = os.getenv("OKX_API_KEY", "")
        secret_key = os.getenv("OKX_SECRET_KEY", "")
        passphrase = os.getenv("OKX_PASSPHRASE", "")
        is_demo    = os.getenv("OKX_IS_DEMO", "1") == "1"

        if not all([api_key, secret_key, passphrase]):
            raise ValueError("Missing OKX API credentials in .env")

        config_path = Path(__file__).parent.parent / "config.yaml"
        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f)

        trading  = cfg.get("trading", {})
        strategy = cfg.get("strategy", {})

        required = [
            "ema_fast", "ema_slow", "adx_period", "adx_threshold_1h",
            "pullback_touch_atr", "pullback_volume_bars", "pullback_volume_factor",
            "breakout_lookback_5m", "trigger_volume_ma_period", "trigger_volume_factor",
            "sl_buffer_atr", "sl_min_atr", "tp_r_multiple",
        ]
        for key in required:
            if key not in strategy:
                raise ValueError(f"Missing strategy.{key} in config.yaml")

        symbols = [
            SymbolConfig(
                symbol=s["id"],
                target_margin_usdt=float(s["target_margin_usdt"]),
                min_sl_percent=float(s["min_sl_percent"]),
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
            ema_fast=int(strategy["ema_fast"]),
            ema_slow=int(strategy["ema_slow"]),
            adx_period=int(strategy["adx_period"]),
            adx_threshold_1h=float(strategy["adx_threshold_1h"]),
            pullback_touch_atr=float(strategy["pullback_touch_atr"]),
            pullback_volume_bars=int(strategy["pullback_volume_bars"]),
            pullback_volume_factor=float(strategy["pullback_volume_factor"]),
            breakout_lookback_5m=int(strategy["breakout_lookback_5m"]),
            trigger_volume_ma_period=int(strategy["trigger_volume_ma_period"]),
            trigger_volume_factor=float(strategy["trigger_volume_factor"]),
            sl_buffer_atr=float(strategy["sl_buffer_atr"]),
            sl_min_atr=float(strategy["sl_min_atr"]),
            tp_r_multiple=float(strategy["tp_r_multiple"]),
        )
