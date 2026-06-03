# -*- coding: utf-8 -*-
"""
dedup.py — event-level дедуп (одно событие из N лент → 1 карточка, не N).

Находка #1 обоих ресёрчей: дедуп по URL ≠ по событию. canonical_url (scanner_v0)
ловит utm-варианты одной ссылки; тут — near-duplicate ЗАГОЛОВКОВ одного актива из
разных лент (RapidFuzz token_set, CPU, keyless). Чистые функции, тестируются.

На одном источнике эффект малый, но закладываем ДО провода новых лент (Этап 4):
иначе флуд = N карточек + N LLM-вызовов + кривой forward-WR на одно событие.
"""
from __future__ import annotations

import hashlib
import re

from rapidfuzz import fuzz

_STOP = {"the", "a", "an", "to", "of", "on", "in", "for", "and", "s", "as",
         "is", "at", "by", "with", "after", "amid", "over", "its", "new"}


def normalize_title(title: str) -> str:
    """Заголовок → канон: lower, только слова/числа, без source-префиксов и стоп-слов."""
    t = re.sub(r"^\s*[\w .]{1,20}:\s*", "", title or "")     # «CoinDesk: …» префикс
    words = re.findall(r"[a-z0-9]+", t.lower())
    return " ".join(w for w in words if w not in _STOP)


def event_key(asset: str | None, title: str) -> str:
    """Стабильный ключ события (актив + хэш нормализованного заголовка) — для кластера."""
    return f"{asset or 'NA'}::{hashlib.sha1(normalize_title(title).encode('utf-8')).hexdigest()[:8]}"


def is_duplicate(title: str, asset: str | None, recent: list, threshold: int = 88) -> str | None:
    """near-dup того же актива среди recent=[(asset,title),…]? Возвращает совпавший заголовок или None."""
    nt = normalize_title(title)
    if not nt:
        return None
    for a, t in recent:
        if a == asset and fuzz.token_set_ratio(nt, normalize_title(t)) >= threshold:
            return t
    return None
