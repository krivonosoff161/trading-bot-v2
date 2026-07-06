from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.scout.public_channel.contracts import PublicChannelItem, item_from_source
from src.scout.router import source_meta

MIN_EXTRACTED_TEXT = 240


def _extend_safe(target: list[dict], label: str, fn, *args, **kwargs) -> None:
    try:
        target.extend(fn(*args, **kwargs) or [])
    except Exception as exc:  # noqa: BLE001 - one broken public source must not stop the pass
        target.append(
            {
                "title": f"{label}: source temporarily unavailable",
                "url": "",
                "source": label,
                "source_class": "source_error",
                "event_type": "source_error",
                "text": str(exc)[:500],
            }
        )


def collect_public_source_items(
    *,
    rss_limit: int = 8,
    telegram_limit: int = 10,
    extraction_limit: int = 8,
    include_rss: bool = True,
    include_telegram: bool = True,
    include_official: bool = True,
    include_native: bool = True,
    enrich_articles: bool = True,
) -> list[PublicChannelItem]:
    """Collect public news-channel candidates from existing Scout adapters.

    This is not the trading scanner. It only gathers public source facts for a
    later editorial/public-channel pass.
    """
    from src.scout import scanner_v0 as scanner
    from src.scout.sources.telegram_web import fetch_telegram_web_sources

    raw: list[dict] = []
    if include_official:
        _extend_safe(raw, "okx_listings", scanner.fetch_new_listings, within_hours=24, limit=5)
        _extend_safe(raw, "sec_edgar", scanner.fetch_recent_filings, within_hours=24, limit=5)
        if source_meta("eia").get("enabled"):
            _extend_safe(raw, "eia", scanner.fetch_eia_schedule, limit=2)
        if source_meta("opec").get("enabled"):
            _extend_safe(raw, "opec", scanner.fetch_opec_schedule, limit=2)
        if source_meta("earnings_calendar").get("enabled"):
            _extend_safe(raw, "earnings_calendar", scanner.fetch_earnings_calendar, limit=5)
    if include_native:
        if source_meta("btc_eth_tactical").get("enabled"):
            _extend_safe(raw, "btc_eth_tactical", scanner.fetch_btc_eth_tactical, limit=4)
        if source_meta("dexscreener").get("enabled"):
            _extend_safe(raw, "dexscreener", scanner.fetch_alt_flow_signals, limit=5)
    if include_telegram:
        _extend_safe(raw, "telegram_web", fetch_telegram_web_sources, limit_per_source=telegram_limit)
    if include_rss:
        _extend_safe(raw, "rss", scanner.fetch_rss, limit=rss_limit)

    if enrich_articles:
        raw = enrich_public_source_rows(raw, limit=extraction_limit)
    return dedupe_items(item for item in (item_from_source(row) for row in raw) if item)


def dedupe_items(items: Iterable[PublicChannelItem]) -> list[PublicChannelItem]:
    seen: set[str] = set()
    out: list[PublicChannelItem] = []
    for item in items:
        if item.key in seen:
            continue
        seen.add(item.key)
        out.append(item)
    return out


def enrich_public_source_rows(rows: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    """Add article body text for public editor inputs when a URL can be extracted.

    The trading scanner still owns its own queue/extraction path. This public
    channel pass only enriches a bounded number of source rows so the LLM editor
    gets a machine-readable document instead of a bare RSS title.
    """
    if limit <= 0:
        return rows
    enriched: list[dict[str, Any]] = []
    extracted = 0
    for row in rows:
        out = dict(row)
        if extracted < limit and _should_extract(out):
            extracted += 1
            out.update(_extract_machine_doc(out))
        else:
            out.setdefault("public_extraction_status", "not_attempted")
            out.setdefault("public_text_quality", _text_quality(str(out.get("text") or out.get("summary") or "")))
        enriched.append(out)
    return enriched


def _should_extract(row: dict[str, Any]) -> bool:
    if not row.get("url"):
        return False
    if row.get("source_class") not in {"rss", "telegram_web"}:
        return False
    text = str(row.get("text") or row.get("summary") or "")
    return len(text.strip()) < MIN_EXTRACTED_TEXT


def _extract_machine_doc(row: dict[str, Any]) -> dict[str, Any]:
    from src.scout.page_extract import extract

    url = str(row.get("url") or "").strip()
    result = extract(url)
    if not isinstance(result, dict):
        return {
            "public_extraction_status": "failed",
            "public_text_quality": _text_quality(str(row.get("text") or row.get("summary") or "")),
        }
    text = str(result.get("text") or "").strip()
    if len(text) < MIN_EXTRACTED_TEXT:
        return {
            "public_extraction_status": "title_only",
            "public_text_quality": _text_quality(text),
            "public_extraction_error": str(result.get("error") or "")[:160],
        }
    title = str(result.get("title") or row.get("title") or "").strip()
    return {
        "title": title or row.get("title"),
        "text": text,
        "summary": text[:1200],
        "published_at": result.get("date") or row.get("published_at") or row.get("time"),
        "public_extraction_status": "extracted",
        "public_text_quality": _text_quality(text),
        "public_machine_doc": {
            "schema": "PublicNewsMachineDoc.v1",
            "source": row.get("source") or row.get("source_id") or "unknown",
            "url": url,
            "title": title or row.get("title") or "",
            "text_len": len(text),
            "extraction_status": "extracted",
        },
    }


def _text_quality(text: str) -> str:
    length = len(str(text or "").strip())
    if length >= 1200:
        return "full"
    if length >= MIN_EXTRACTED_TEXT:
        return "usable"
    if length:
        return "title_or_snippet"
    return "empty"
