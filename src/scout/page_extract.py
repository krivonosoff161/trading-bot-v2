# -*- coding: utf-8 -*-
"""
page_extract.py — страница → чистый текст + дата для аналитика сканера.

PICK (рой 02.06): Trafilatura (Apache-2.0, keyless, CPU-only, лучший экстрактор).
Вход:  url.  Выход: {"url","title","date","text"} либо None (если не извлеклось).
Без GPU, без ключей. Часть ingestion-стека (см. SCANNER_SPEC.md).
"""
import json
import math
from configparser import ConfigParser


def _bounded_config(trafilatura, timeout_seconds: float | None) -> ConfigParser:
    defaults = dict(trafilatura.settings.DEFAULT_CONFIG["DEFAULT"])
    if timeout_seconds is not None:
        bounded = max(1, int(math.ceil(float(timeout_seconds))))
        defaults["DOWNLOAD_TIMEOUT"] = str(bounded)
        defaults["EXTRACTION_TIMEOUT"] = str(bounded)
    config = ConfigParser()
    config["DEFAULT"] = defaults
    return config


def extract(url: str, *, timeout_seconds: float | None = None) -> dict | None:
    """Скачать страницу и извлечь чистый текст статьи + метаданные (title/date)."""
    try:
        import trafilatura
    except ImportError:
        return {"url": url, "error": "trafilatura не установлен → pip install trafilatura"}
    try:
        downloaded = trafilatura.fetch_url(
            url,
            config=_bounded_config(trafilatura, timeout_seconds),
        )
        if not downloaded:
            return None
        data = trafilatura.extract(
            downloaded,
            output_format="json",
            with_metadata=True,
            favor_precision=True,
        )
        if not data:
            return None
        d = json.loads(data)
        return {
            "url": url,
            "title": d.get("title"),
            "date": d.get("date"),
            "text": (d.get("text") or "").strip(),
        }
    except Exception as e:
        return {"url": url, "error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    import sys
    u = sys.argv[1] if len(sys.argv) > 1 else \
        "https://www.coindesk.com/markets/2026/05/29/ice-ceo-calls-hyperliquid-bigger-than-nasdaq-says-he-s-met-its-founders"
    r = extract(u)
    if r and r.get("text"):
        print(f"TITLE: {r['title']}")
        print(f"DATE:  {r['date']}")
        print(f"TEXT:  {len(r['text'])} символов")
        print("---")
        print(r["text"][:700])
    else:
        print("RESULT:", r)
