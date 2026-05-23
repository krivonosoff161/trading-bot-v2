from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "logs" / "main_impulse"
ENGINE_LOG = LOG_DIR / "ws_main_impulse.log"

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
    "exit_slippage_pct": 0.03,
    "fee_pct": 0.20,
    "stop_buffer_pct": 0.10,
    "structure_k": 2,
    "exit_mode": "ride",
    "scaled_tp_body_mult": 1.0,
    "scaled_tp_min_pct": 0.8,
    "be_at_R": 1.0,
    "max_hold_min": 60,
    "max_concurrent": 2,
    "cooldown_sec": 300,
    "warmup_bars": 40,
    "heartbeat_interval": 30,
    "notify_min_interval_sec": 2.0,
    "extra_notify_chats": [],
}


def load_main_impulse_config() -> dict[str, Any]:
    cfg = DEFAULT_CONFIG.copy()
    try:
        with (ROOT / "config.yaml").open(encoding="utf-8") as fh:
            project = yaml.safe_load(fh) or {}
        cfg.update(project.get("main_impulse", {}) or {})
    except Exception:
        pass
    return cfg


def setup_logger() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ws_main_impulse")
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


def notify_chat_ids(config: dict[str, Any]) -> list[str]:
    ids = [str(cid).strip() for cid in config.get("extra_notify_chats", [])]
    # main engine -> bot group (TELEGRAM_CHAT_ID), NOT the pump chat.
    # MAIN_IMPULSE_CHAT_ID is an optional override if a separate channel is ever wanted.
    raw = os.getenv("MAIN_IMPULSE_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")
    ids += [cid.strip() for cid in raw.split(",")]
    return list(dict.fromkeys([cid for cid in ids if cid]))


def slipped(price: float, side: str, slippage_pct: float) -> float:
    return price * (1 + slippage_pct / 100) if side == "long" else price * (1 - slippage_pct / 100)


def exit_slipped(price: float, side: str, slippage_pct: float) -> float:
    close_side = "short" if side == "long" else "long"
    return slipped(price, close_side, slippage_pct)
