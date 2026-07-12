"""Pure delivery switches shared by scanner launch surfaces."""

from __future__ import annotations

import os


def scanner_telegram_enabled() -> bool:
    """Preserve standalone delivery unless a supervisor explicitly disables it."""
    value = os.getenv("SCANNER_SEND_TELEGRAM")
    if value is None:
        return True
    return value.strip().lower() in {"1", "true", "yes", "on"}
