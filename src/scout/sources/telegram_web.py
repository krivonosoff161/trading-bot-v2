# -*- coding: utf-8 -*-
"""Read public Telegram channel pages as scanner sources.

This is the no-API fallback for channels that expose public `https://t.me/s/...`
pages. It does not log in, does not send messages, and does not use Telegram
client credentials. The output is normalized into the same source-item shape as
RSS/API feeds so the existing buffer/router/agent pipeline can replay it.
"""
from __future__ import annotations

import datetime as dt
import html
import re
from dataclasses import dataclass
from typing import Any

import requests

from src.scout.router import baseline_for_layer, enabled_sources, source_meta

UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 telegram-web-source; keyless)"}
TIMEOUT = 20
MAX_PER_SOURCE = 20

_BLOCK_RE = re.compile(r'<div class="tgme_widget_message_wrap\b(.*?)(?=<div class="tgme_widget_message_wrap\b|</section>)', re.S)
_POST_RE = re.compile(r'data-post="([^"]+)"')
_TIME_RE = re.compile(r'<time datetime="([^"]+)"')
_TEXT_RE = re.compile(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', re.S)
_HREF_RE = re.compile(r'<a\b[^>]*\bhref="([^"]+)"', re.I)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_CASHTAG_RE = re.compile(r"(?<![A-Z0-9])\$([A-Z][A-Z0-9]{1,14})\b")
_HASH_SYMBOL_RE = re.compile(r"(?<![A-Z0-9])#(?:[A-Za-z0-9_]+:)?([A-Z][A-Z0-9]{1,14})\b")


@dataclass(frozen=True)
class TelegramPost:
    channel: str
    post_id: str
    url: str
    text: str
    published_at: str | None
    links: tuple[str, ...]


def fetch_telegram_web_sources(limit_per_source: int = MAX_PER_SOURCE) -> list[dict[str, Any]]:
    """Fetch all enabled `telegram_web` sources from source_registry.yaml."""
    out: list[dict[str, Any]] = []
    for source_id, meta in enabled_sources().items():
        if meta.get("source_class") != "telegram_web":
            continue
        out.extend(fetch_source(source_id, limit=limit_per_source))
    return out


def fetch_source(source_id: str, limit: int = MAX_PER_SOURCE, *, fetch=None) -> list[dict[str, Any]]:
    """Fetch one registry source and return scanner items."""
    meta = source_meta(source_id)
    if not meta or meta.get("source_class") != "telegram_web" or meta.get("enabled") is not True:
        return []
    channel = str(meta.get("channel") or "").strip().lstrip("@")
    url = str(meta.get("url") or f"https://t.me/s/{channel}").strip()
    if not channel or not url:
        return []
    fetch = fetch or _fetch_url
    try:
        body = fetch(url)
    except Exception as exc:
        print(f"  TG {source_id}: {exc}")
        return []
    posts = parse_channel_html(body, channel=channel)
    items: list[dict[str, Any]] = []
    for post in posts[-max(1, int(limit)):]:
        items.extend(_post_to_items(post, source_id, meta))
    return items


def parse_channel_html(body: str, *, channel: str) -> list[TelegramPost]:
    """Parse Telegram public channel HTML into posts."""
    posts: list[TelegramPost] = []
    for block_match in _BLOCK_RE.finditer(body or ""):
        block = block_match.group(1)
        post_m = _POST_RE.search(block)
        text_m = _TEXT_RE.search(block)
        if not post_m or not text_m:
            continue
        text = _clean_html(text_m.group(1))
        if not text:
            continue
        post_ref = html.unescape(post_m.group(1))
        if "/" in post_ref:
            post_channel, post_id = post_ref.split("/", 1)
            if post_channel.lower() != channel.lower():
                continue
        else:
            post_id = post_ref
        time_m = _TIME_RE.search(block)
        published = _normalize_time(time_m.group(1)) if time_m else None
        links = tuple(_clean_link(u) for u in _HREF_RE.findall(text_m.group(1)))
        links = tuple(u for u in links if u)
        posts.append(
            TelegramPost(
                channel=channel,
                post_id=post_id,
                url=f"https://t.me/{channel}/{post_id}",
                text=text,
                published_at=published,
                links=links,
            )
        )
    return posts


def _post_to_items(post: TelegramPost, source_id: str, meta: dict[str, Any]) -> list[dict[str, Any]]:
    channel_kind = str(meta.get("telegram_kind") or "").strip().lower()
    tickers = _extract_tickers(post.text)
    base = {
        "title": _title_for_post(post.text),
        "url": post.url,
        "time": post.published_at,
        "published_at": post.published_at,
        "source": source_id,
        "source_id": source_id,
        "source_class": "telegram_web",
        "lead_class": meta.get("lead_class", "COINCIDENT"),
        "trigger_type": "telegram_post",
        "text": post.text,
        "summary": post.text,
        "channel": post.channel,
        "post_id": post.post_id,
        "links": list(post.links),
    }
    if channel_kind == "listing" and tickers:
        items = []
        for ticker in tickers:
            items.append(
                {
                    **base,
                    "title": f"${ticker} listing signal: {_title_for_post(post.text)}",
                    "asset": ticker,
                    "okx_inst": f"{ticker}-USDT-SWAP",
                    "layer": 2,
                    "baseline": baseline_for_layer(2),
                    "event_type": "exchange_listing",
                    "phase": "REALIZED",
                    "event_key": f"{source_id}:{post.post_id}:{ticker}",
                }
            )
        return items
    if channel_kind == "liquidations" and tickers:
        ticker = tickers[0]
        layer = 4 if ticker in {"CL", "NG"} else 1 if ticker in {"BTC", "ETH", "SOL", "BNB", "XRP"} else 2
        return [
            {
                **base,
                "asset": ticker,
                "okx_inst": f"{ticker}-USDT-SWAP",
                "layer": layer,
                "baseline": baseline_for_layer(layer),
                "event_type": "liquidation_flow",
                "phase": "REALIZED",
                "event_key": f"{source_id}:{post.post_id}:{ticker}",
            }
        ]
    return [base]


def _fetch_url(url: str) -> str:
    response = requests.get(url, headers=UA, timeout=TIMEOUT)
    response.raise_for_status()
    return response.text


def _clean_html(fragment: str) -> str:
    value = _BR_RE.sub("\n", fragment or "")
    value = _TAG_RE.sub("", value)
    value = html.unescape(value)
    lines = [" ".join(line.split()) for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _clean_link(url: str) -> str:
    value = html.unescape(url or "").strip()
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("/"):
        return "https://t.me" + value
    return value


def _normalize_time(value: str) -> str | None:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return value or None
    return parsed.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _extract_tickers(text: str) -> list[str]:
    found: list[str] = []
    for regex in (_CASHTAG_RE, _HASH_SYMBOL_RE):
        for match in regex.findall(text or ""):
            ticker = str(match).upper()
            if ticker not in found and 2 <= len(ticker) <= 15:
                found.append(ticker)
    return found


def _title_for_post(text: str) -> str:
    one_line = " ".join((text or "").split())
    return one_line[:220].strip() or "(empty telegram post)"
