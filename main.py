"""
Trading Bot V2 — entry point.
"""

from loguru import logger

from src.config import Config
from src.utils.logger import setup_logger


def main() -> None:
    setup_logger()
    logger.info("Trading Bot V2 starting...")

    try:
        config = Config.load()
        logger.info(
            "Config loaded | symbol={} leverage={}x demo={}",
            config.symbol,
            config.leverage,
            config.is_demo,
        )
    except ValueError as e:
        logger.error("Config error: {}", e)
        return

    logger.info("Bot ready. OKX client next.")


if __name__ == "__main__":
    main()
