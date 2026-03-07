"""
Logging setup for trading bot.

Log files:
  bot.log      — DEBUG+ everything (full debug trail)
  trades.log   — trade events only: OPEN / CLOSE with PnL
  errors.log   — WARNING+ errors and exceptions
  signals.jsonl — structured JSONL: every tick decision with full indicator state
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

_signal_log_path: str = "logs/signals.jsonl"


def setup_logger(log_dir: str = "logs") -> None:
    global _signal_log_path
    _signal_log_path = f"{log_dir}/signals.jsonl"
    Path(log_dir).mkdir(exist_ok=True)

    logger.remove()

    # Console — INFO+, human-readable
    logger.add(
        sys.stdout,
        level="INFO",
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # bot.log — DEBUG+ everything, rotation 5MB
    logger.add(
        f"{log_dir}/bot.log",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        rotation="5 MB",
        retention="14 days",
        compression="zip",
        encoding="utf-8",
        enqueue=True,
    )

    # trades.log — only messages tagged with extra["trade"]=True
    logger.add(
        f"{log_dir}/trades.log",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {message}",
        filter=lambda r: r["extra"].get("trade", False),
        rotation="5 MB",
        retention="90 days",
        encoding="utf-8",
        enqueue=True,
    )

    # errors.log — WARNING+ with full traceback
    logger.add(
        f"{log_dir}/errors.log",
        level="WARNING",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}\n{exception}",
        rotation="5 MB",
        retention="30 days",
        encoding="utf-8",
        backtrace=True,
        diagnose=True,
        enqueue=True,
    )

    logger.info("Logger initialized | log_dir={}", log_dir)


# Shortcut for trade events — writes to both bot.log and trades.log
trade_logger = logger.bind(trade=True)


def write_signal(data: dict) -> None:
    """Append one JSON line to signals.jsonl. Adds timestamp if not present."""
    data.setdefault("ts", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    try:
        with open(_signal_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("write_signal failed | {}", e)
