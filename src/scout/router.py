# -*- coding: utf-8 -*-
"""
router.py — детерминированное ядро «вывода» (0 токенов LLM, до мозга).

Чистые функции, читают конфиги (entities/event_taxonomy/source_registry):
- route_asset(headline)      → актив/слой/инструмент/baseline/уверенность (или None)
- route_temporal(headline)   → фаза будет/произошло/контекст/неясно
- score_materiality(h,layer) → материальность (триггер vs шум) + family + drop_reason

Это «дёшево решает ЧТО ЭТО». LLM получает уже нормализованное событие (см. SYNTHESIS-док).
Тестируется tests/test_scanner_router.py (оба ресёрча: ядро = тестируемая rules-машина).
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

_CFG = Path(__file__).resolve().parent / "config"


@lru_cache(maxsize=1)
def _entities() -> dict:
    return yaml.safe_load((_CFG / "entities.yaml").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _taxonomy() -> dict:
    return yaml.safe_load((_CFG / "event_taxonomy.yaml").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _sources() -> dict:
    return yaml.safe_load((_CFG / "source_registry.yaml").read_text(encoding="utf-8"))


def materiality_threshold() -> float:
    return _taxonomy().get("materiality", {}).get("threshold_llm", 0.5)


def dedup_config() -> dict:
    return _taxonomy().get("dedup", {}) or {}


def classify_layer(symbol: str) -> int:
    """Базовый символ инструмента → слой (открытая вселенная листингов). Дефолт = 2 (крипта-альт)."""
    cfg = _entities()
    sym = (symbol or "").upper()
    for a in cfg.get("assets", []):
        if a["sym"].upper() == sym:
            return a["layer"]
    for layer, syms in (cfg.get("layer_map", {}) or {}).items():
        if sym in [str(s).upper() for s in syms]:
            return int(layer)
    return 2


def baseline_for_layer(layer: int) -> str | None:
    """Якорь baseline по слою (excess vs index). Нет в карте → None (manual, off-OKX)."""
    return (_entities().get("baseline_by_layer", {}) or {}).get(layer)


def source_meta(source: str) -> dict:
    return (_sources().get("sources", {}) or {}).get(source, {})


# ── РОУТЕР актив/слой ────────────────────────────────────────────────────────
def route_asset(headline: str) -> dict | None:
    """Заголовок → {asset, okx_inst, layer, baseline, confidence} либо None.
    Мульти-матч + субъект (позиция) + подтверждение короткого тикера."""
    cfg = _entities()
    rt = cfg.get("router", {})
    threshold = rt.get("threshold", 0.5)
    zone = rt.get("subject_zone_words", 6)
    low = headline.lower()
    first_zone = set(re.findall(r"[a-z]+", low)[:zone])

    best_key, best_asset = None, None
    for a in cfg.get("assets", []):
        score, pos = 0.0, 9999
        for name in a.get("strong", []):
            m = re.search(r"\b" + re.escape(name) + r"\b", low)
            if m:
                score = max(score, 0.7)
                if name.split()[0] in first_zone:
                    score += 0.15
                pos = min(pos, m.start())
        for tk in a.get("weak", []):
            if re.search(r"\$" + re.escape(tk) + r"\b", low) or re.search(r"\b" + re.escape(tk) + r"[-/]usd", low):
                score = max(score, 0.6)
            elif re.search(r"\b" + re.escape(tk) + r"\b", low) and score == 0.0:
                score = max(score, 0.35)
        if score > 0:
            key = (score, -pos)
            if best_key is None or key > best_key:
                best_key, best_asset = key, a
    if best_asset and best_key[0] >= threshold:
        return {"asset": best_asset["sym"], "okx_inst": best_asset["okx_inst"],
                "layer": best_asset["layer"], "baseline": best_asset.get("baseline"),
                "confidence": round(min(best_key[0], 1.0), 2)}
    return None


# ── ТЕМПОРАЛ будет/произошло ─────────────────────────────────────────────────
_FUTURE_RE = re.compile(
    r"\b(will|expected|scheduled|upcoming|plans?\s+to|set\s+to|"
    r"to\s+(launch|list|release|unveil|vote|decide|raise)|ahead\s+of|"
    r"next\s+(week|month)|due)\b")
# headlinese: результативный present-simple/past = уже случилось (фикс «Bitcoin crashes»)
_REALIZED_RE = re.compile(
    r"\b(announced?|approved?|rejected?|reported?|released?|launch(ed|es)?|"
    r"list(ed|s)?|filed?|files?|halt(ed|s)?|hack(ed|s)?|surge[sd]?|crash(ed|es)?|"
    r"plunge[sd]?|jump(ed|s)?|fell|rose|rise[sd]?|beat[s]?|miss(ed|es)?|"
    r"drop[s]?|dropped|unveil(ed|s)?|raises?|cuts?|soar[sd]?)\b")
_CONTEXT_RE = re.compile(
    r"\b(analysis|prediction|forecast|recap|opinion|explain(ed|er)?|guide|outlook|"
    r"how\s+to|reasons?\s+to|what\s+to\s+know)\b")


def route_temporal(headline: str) -> dict:
    """Фаза события по времени: FUTURE / REALIZED / CONTEXT / AMBIGUOUS (→ LLM)."""
    low = headline.lower()
    fut = bool(_FUTURE_RE.search(low))
    real = bool(_REALIZED_RE.search(low))
    ctx = bool(_CONTEXT_RE.search(low))
    # future-маркер доминирует: тредабельное СОБЫТИЕ в будущем («announces it WILL unlock»
    # = отчёт realized, но событие future → в pending). headlinese-результатив = realized.
    if fut:
        phase = "FUTURE"
    elif real:
        phase = "REALIZED"
    elif ctx:
        phase = "CONTEXT"
    else:
        phase = "AMBIGUOUS"      # нет ясных маркеров → консервативно к LLM
    return {"phase": phase, "future": fut, "realized": real, "context": ctx}


# ── МАТЕРИАЛЬНОСТЬ ───────────────────────────────────────────────────────────
def score_materiality(headline: str, layer: int) -> dict:
    """Триггер vs шум: negative-genre → дроп; positive per-layer термин → family+score."""
    tax = _taxonomy()
    low = headline.lower()
    for g in tax.get("noise_genres", []):
        if str(g).lower() in low:
            return {"score": 0.0, "family": None, "drop_reason": "noise_genre", "matched": g}
    fams = (tax.get("layers", {}) or {}).get(layer, {}) or {}
    base = tax.get("materiality", {}).get("base_score", 0.6)
    for fam, terms in fams.items():
        for term in terms:
            if re.search(r"\b" + re.escape(str(term).lower()) + r"\b", low):
                return {"score": base, "family": fam, "drop_reason": None, "matched": term}
    return {"score": 0.0, "family": None, "drop_reason": "no_material_term", "matched": None}
