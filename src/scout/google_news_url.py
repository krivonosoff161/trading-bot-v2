# -*- coding: utf-8 -*-
"""
google_news_url.py — раскрытие redirect-обёрток Google News до реального URL статьи.

Зачем: google_news_* ленты отдают `news.google.com/rss/articles/<id>` — page_extract
не может прочитать такую страницу (consent/JS) → 72% буфера остаются «заголовок без
тела», chief судит по полутора строкам. Этот модуль возвращает настоящий URL.

Два пути:
  • decode_google_news_url — ЛОКАЛЬНО, без сети: старый формат id (до ~2024) содержит
    URL прямо в protobuf-пейлоаде base64. Новый формат (`AU_yqL…`) URL не содержит → None.
  • resolve_google_news_url — СЕТЬ (2 запроса к Google, без ключей/браузера): страница
    статьи (SOCS-кука против consent-стены) → data-n-a-sg/ts → POST batchexecute →
    реальный URL. Проверено живьём 10.06.2026 (cnbc/morningstar/bullionvault 3/3).

Никогда не бросает исключений: не получилось → None / исходный URL без изменений.
LLM не вызывается. Сеть — только в resolve_* (вызывать из extract-стадии, не из ingest).

Троттлинг (аудит 11.06: бэкфил ~600×2 запросов за день → Google 429 «unusual traffic»,
509 доков с ошибкой, 466 обёрток не раскрыто): пауза между сетевыми резолвами +
cooldown после 429 (мягкий отказ, оригинальный URL остаётся фолбэком, петля не падает).
Конфиг через env: SCANNER_GN_DELAY_S / SCANNER_GN_COOLDOWN_S. Метрики — metrics().
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from urllib.parse import quote, urlsplit

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
# Consent-bypass (EU-стена): без неё страница статьи = consent.google.com без sg/ts.
_COOKIES = {"SOCS": "CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg"}
_TIMEOUT = 20
_URL_RE = re.compile(rb"https?://[!-~]+")          # printable-ASCII run внутри protobuf
_SG_RE = re.compile(r'data-n-a-sg="([^"]+)"')
_TS_RE = re.compile(r'data-n-a-ts="([^"]+)"')


# ── троттлинг/метрики сетевого резолва ───────────────────────────────────────
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


RESOLVE_DELAY_S = _env_float("SCANNER_GN_DELAY_S", 4.0)        # пауза между резолвами
COOLDOWN_429_S = _env_float("SCANNER_GN_COOLDOWN_S", 1800.0)   # стоп после 429 (~1 проход)

_metrics = {"google_wrappers_seen": 0, "google_resolved": 0, "google_failed": 0,
            "google_429": 0, "google_skipped_cooldown": 0, "google_backoff_seconds": 0.0}
_state = {"last_resolve_ts": 0.0, "cooldown_until": 0.0}


def _now() -> float:           # обёртки — monkeypatch в тестах вместо реального времени
    return time.monotonic()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def metrics() -> dict:
    """Снимок счётчиков резолва (для llm_budget/отчётов)."""
    return dict(_metrics)


def reset_metrics() -> None:
    for k in _metrics:
        _metrics[k] = 0.0 if k == "google_backoff_seconds" else 0
    _state["last_resolve_ts"] = 0.0
    _state["cooldown_until"] = 0.0


def in_cooldown() -> bool:
    return _now() < _state["cooldown_until"]


def _looks_like_429(resp) -> bool:
    try:
        if int(getattr(resp, "status_code", 0) or 0) == 429:
            return True
        return "unusual traffic" in str(getattr(resp, "text", "") or "").lower()
    except Exception:
        return False


def _throttle_before_request() -> None:
    wait = _state["last_resolve_ts"] + RESOLVE_DELAY_S - _now()
    if wait > 0:
        _metrics["google_backoff_seconds"] += wait
        _sleep(wait)
    _state["last_resolve_ts"] = _now()


def _enter_cooldown() -> None:
    _metrics["google_429"] += 1
    _state["cooldown_until"] = _now() + COOLDOWN_429_S


def is_google_news_url(url: str) -> bool:
    try:
        p = urlsplit(str(url or "").strip())
    except Exception:
        return False
    return p.netloc.lower().endswith("news.google.com") and "/articles/" in p.path


def article_id(url: str) -> str | None:
    """`…/articles/<id>` → id (без query). None, если это не статья Google News."""
    if not is_google_news_url(url):
        return None
    try:
        tail = urlsplit(url).path.split("/articles/", 1)[1]
        return tail.split("/")[0].strip() or None
    except Exception:
        return None


def decode_google_news_url(url: str) -> str | None:
    """ЛОКАЛЬНЫЙ декод (старый формат id: URL зашит в base64-protobuf). Без сети.

    Новый формат (payload `AU_yqL…`) URL не содержит → None (нужен resolve_*)."""
    art = article_id(url)
    if not art:
        return None
    try:
        raw = base64.urlsafe_b64decode(art + "=" * (-len(art) % 4))
    except Exception:
        return None
    m = _URL_RE.search(raw)
    if not m:
        return None
    candidate = m.group(0).decode("ascii", errors="ignore")
    try:
        p = urlsplit(candidate)
        if p.scheme in ("http", "https") and p.netloc and "." in p.netloc:
            return candidate
    except Exception:
        return None
    return None


def unwrap_google_news_url(url: str) -> tuple[str, dict]:
    """Локальная развёртка для ingest: (лучший URL, meta). Сеть НЕ трогает.

    meta: google_news=bool, decoded=bool, google_news_url=исходная обёртка (если декод)."""
    if not is_google_news_url(url):
        return url, {"google_news": False, "decoded": False}
    real = decode_google_news_url(url)
    if real:
        return real, {"google_news": True, "decoded": True, "google_news_url": url}
    return url, {"google_news": True, "decoded": False}


def resolve_google_news_url(url: str, timeout: int = _TIMEOUT, http=None) -> str | None:
    """СЕТЕВОЙ резолв нового формата: страница статьи → sg/ts → batchexecute → URL.

    http — инжект для тестов (объект с .get/.post как у requests). Любой сбой → None.
    Троттлинг: пауза RESOLVE_DELAY_S между запросами; после 429 — cooldown (мягкий отказ,
    оригинальная обёртка остаётся URL-ом, сканер-петля не падает)."""
    art = article_id(url)
    if not art:
        return None
    _metrics["google_wrappers_seen"] += 1
    if in_cooldown():
        _metrics["google_skipped_cooldown"] += 1
        return None
    try:
        if http is None:
            import requests as http  # noqa: PLC0415 — ленивый импорт, модуль остаётся чистым
        _throttle_before_request()
        page = http.get(f"https://news.google.com/articles/{art}?hl=en-US&gl=US",
                        headers=UA, cookies=_COOKIES, timeout=timeout)
        if _looks_like_429(page):
            _enter_cooldown()
            return None
        m_sg = _SG_RE.search(page.text or "")
        m_ts = _TS_RE.search(page.text or "")
        if not (m_sg and m_ts):
            _metrics["google_failed"] += 1
            return None
        inner = (
            '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,'
            'null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
            f'"{art}",{m_ts.group(1)},"{m_sg.group(1)}"]'
        )
        # между get и post ОДНОЙ статьи паузу не держим (естественный page→xhr паттерн);
        # троттлится частота резолвов статей (_throttle_before_request выше)
        payload = json.dumps([[["Fbv4je", inner, None, "generic"]]])
        resp = http.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            headers={**UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            cookies=_COOKIES, data=f"f.req={quote(payload)}", timeout=timeout,
        )
        if _looks_like_429(resp):
            _enter_cooldown()
            return None
        text = resp.text or ""
        chunk = text.split("\n\n", 1)[1] if "\n\n" in text else text
        start = chunk.find("[")
        if start < 0:
            _metrics["google_failed"] += 1
            return None
        for line in chunk[start:].splitlines():
            if "Fbv4je" not in line:
                continue
            arr = json.loads(line)
            real = json.loads(arr[0][2])[1]
            if isinstance(real, str) and real.startswith("http"):
                _metrics["google_resolved"] += 1
                return real
            _metrics["google_failed"] += 1
            return None
    except Exception:
        _metrics["google_failed"] += 1
        return None
    _metrics["google_failed"] += 1
    return None
