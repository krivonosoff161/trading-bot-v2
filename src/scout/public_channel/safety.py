from __future__ import annotations

import re

from src.scout.public_channel.contracts import PublicChannelPost

_FORBIDDEN_PATTERNS = [
    r"\bbuy\b",
    r"\bsell\b",
    r"\bentry\b",
    r"\bstop[- ]?loss\b",
    r"\btake[- ]?profit\b",
    r"\bleverage\b",
    r"\bлонг\b",
    r"\bшорт\b",
    r"\bвход\b",
    r"\bточк[аи]\s+вход",
    r"\bстоп\b",
    r"\bтейк\b",
    r"\bплеч[оа]\b",
    r"\bпокупа[йть]\b",
    r"\bпродава[йть]\b",
    r"\bрекомендую\b",
]


def has_forbidden_advice(text: str) -> bool:
    low = str(text or "").lower()
    return any(re.search(pattern, low, flags=re.I) for pattern in _FORBIDDEN_PATTERNS)


def validate_public_post(post: PublicChannelPost) -> tuple[bool, str]:
    if not post.public_ok:
        return False, post.skip_reason or "public_not_ok"
    combined = "\n".join(
        [
            post.headline,
            post.category,
            post.what_happened,
            post.why_matters,
            "\n".join(post.watch_points),
        ]
    )
    if has_forbidden_advice(combined):
        return False, "forbidden_trading_advice_terms"
    if not post.source_url:
        return False, "missing_source_url"
    return True, ""

