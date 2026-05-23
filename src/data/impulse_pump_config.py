from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from src.data.impulse_pump_model import PaperPosition, slipped


ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "logs" / "impulse_pump"
ENGINE_LOG = LOG_DIR / "ws_impulse_pump.log"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "auto_trade": False,
    "paper": True,
    "pairs": [
        "BSB-USDT-SWAP",
        "EDEN-USDT-SWAP",
        "RLS-USDT-SWAP",
        "CHZ-USDT-SWAP",
        "SPACE-USDT-SWAP",
        "NOT-USDT-SWAP",
        "TURBO-USDT-SWAP",
        "BOME-USDT-SWAP",
    ],
    "body_ratio_min": 2.0,
    "vol_ratio_min": 2.0,
    "trigger_pct": 1.0,
    "trigger_window_sec": 10,
    "entry_slippage_pct": 0.03,
    "fee_pct": 0.20,
    "stop_buffer_pct": 0.10,
    "structure_k": 2,
    "max_hold_min": 60,
    "max_concurrent": 2,
    "cooldown_sec": 300,
    "position_usd": 100.0,
    "warmup_bars": 40,
    "heartbeat_interval": 30,
    "notify_min_interval_sec": 2.0,
    "vol_tier": "volatile_alt",
}


def load_impulse_config() -> dict[str, Any]:
    cfg = DEFAULT_CONFIG.copy()
    try:
        with (ROOT / "config.yaml").open(encoding="utf-8") as fh:
            project = yaml.safe_load(fh) or {}
        cfg.update(project.get("impulse_pump", {}) or {})
    except Exception:
        pass
    return cfg


def setup_logger() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ws_impulse_pump")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    fmt = logging.Formatter("%(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        ENGINE_LOG, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def pump_chat_ids(config: dict[str, Any]) -> list[str]:
    ids = [str(cid).strip() for cid in config.get("extra_notify_chats", [])]
    ids += [cid.strip() for cid in os.getenv("PUMP_CHAT_ID", "").split(",")]
    return list(dict.fromkeys([cid for cid in ids if cid]))


def exit_slipped(price: float, side: str, slippage_pct: float) -> float:
    close_side = "short" if side == "long" else "long"
    return slipped(price, close_side, slippage_pct)


def fmt_open(pos: PaperPosition) -> str:
    return (
        f"PUMP PAPER OPEN | {pos.symbol} | {pos.side.upper()}\n"
        f"entry {pos.entry_price:.8g} | stop {pos.stop_price:.8g}\n"
        f"body {pos.signal['impulse_body_pct']:.2f}% | "
        f"vol {pos.signal['vol_ratio']:.2f}x | lag {pos.signal['entry_lag_pct']:.2f}%"
    )


def fmt_close(row: dict[str, Any]) -> str:
    return (
        f"PUMP PAPER CLOSE | {row['symbol']} | {row['side'].upper()} | {row['outcome']}\n"
        f"net {row['net_pct']:+.2f}% | mfe {row['mfe_pct']:.2f}% | "
        f"mae {row['mae_pct']:.2f}% | hold {row['hold_min']:.1f}m"
    )
