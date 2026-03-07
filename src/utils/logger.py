"""
Logging setup for trading bot.
Standard format: timestamp | level | module | message
Every trade event must include: symbol, action, price, side, reason
"""

import sys
from pathlib import Path

from loguru import logger


def setup_logger(log_dir: str = "logs") -> None:
    """Configure logger: console (INFO) + file (DEBUG) with rotation."""
    Path(log_dir).mkdir(exist_ok=True)

    # Remove default handler
    logger.remove()

    # Console — INFO and above, readable format
    logger.add(
        sys.stdout,
        level="INFO",
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> | <level>{message}</level>",
        colorize=True,
    )

    # File — DEBUG and above, full detail, rotation 50MB
    logger.add(
        f"{log_dir}/bot.log",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        rotation="50 MB",
        retention="30 days",
        compression="zip",
        encoding="utf-8",
    )

    logger.info("Logger initialized | log_dir={}", log_dir)
