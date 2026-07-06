from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from src.scout.public_channel.collector import collect_public_source_items
from src.scout.public_channel.contracts import PublicChannelItem
from src.scout.public_channel.editor import build_post
from src.scout.public_channel.safety import validate_public_post
from src.scout.public_channel.stats import format_public_stats_html, load_public_paper_stats
from src.scout.public_channel.storage import (
    append_audit,
    enqueue_items,
    mark_sent,
    read_queue,
    remove_queue_keys,
    was_sent,
)
from src.scout.public_channel.telegram_format import format_telegram_html
from src.utils.telegram_delivery_router import deliver_notification


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _item_from_queue_record(record: dict[str, Any]) -> PublicChannelItem | None:
    data = record.get("item")
    if not isinstance(data, dict):
        return None
    try:
        return PublicChannelItem(
            key=str(data["key"]),
            title=str(data.get("title") or ""),
            url=str(data.get("url") or ""),
            source=str(data.get("source") or "unknown"),
            source_class=str(data.get("source_class") or ""),
            lead_class=str(data.get("lead_class") or ""),
            layer=data.get("layer") if isinstance(data.get("layer"), int) or data.get("layer") is None else int(data.get("layer")),
            event_type=str(data.get("event_type") or "news"),
            published_at=data.get("published_at"),
            text=str(data.get("text") or ""),
            raw=dict(data.get("raw") or {}),
        )
    except Exception:
        return None


def collect_news_to_queue(*, max_queue: int = 200) -> dict[str, Any]:
    items = collect_public_source_items()
    queue = enqueue_items([item.to_dict() for item in items], max_items=max_queue)
    row = {
        "ts": _now(),
        "schema": "public_channel_collect_run.v1",
        "items": len(items),
        "queue": queue,
    }
    append_audit(row)
    return row


async def publish_news_once(
    *,
    limit: int = 3,
    send: bool = False,
    use_llm: bool = False,
    chat_env: str = "SCANNER_CHAT_ID",
    collect: bool = True,
    max_queue: int = 200,
) -> dict[str, Any]:
    collect_summary = collect_news_to_queue(max_queue=max_queue) if collect else None
    records = read_queue()
    summary = {
        "schema": "public_channel_news_run.v2",
        "collect": collect_summary,
        "queue_available": len(records),
        "posted": 0,
        "skipped": 0,
        "removed_from_queue": 0,
        "rows": [],
    }
    remove_keys: set[str] = set()
    for record in records:
        if summary["posted"] >= limit:
            break
        item = _item_from_queue_record(record)
        if item is None:
            summary["skipped"] += 1
            if record.get("key"):
                remove_keys.add(str(record["key"]))
            continue
        if was_sent(item.key):
            summary["skipped"] += 1
            remove_keys.add(item.key)
            continue
        post, usage = await build_post(item, use_llm=use_llm)
        ok, reason = validate_public_post(post)
        if not ok:
            summary["skipped"] += 1
            remove_keys.add(item.key)
            append_audit({"ts": _now(), "status": "skipped", "reason": reason, "item": item.to_dict(), "usage": usage})
            continue
        text = format_telegram_html(post)
        delivery = await deliver_notification(
            event_type="NEWS",
            text=text,
            chat_env=chat_env,
            symbol=item.raw.get("asset") or "",
            dry_run=not send,
        )
        if send and int(delivery.get("delivered") or 0) > 0:
            mark_sent(item.key)
            remove_keys.add(item.key)
        row = {"ts": _now(), "status": "sent" if send else "dry_run", "post": post.to_dict(), "delivery": delivery, "usage": usage}
        append_audit(row)
        summary["rows"].append(row)
        summary["posted"] += 1
    summary["removed_from_queue"] = remove_queue_keys(remove_keys)
    summary["queue_remaining"] = len(read_queue())
    return summary


async def publish_stats_once(
    *,
    send: bool = False,
    chat_env: str = "SCANNER_CHAT_ID",
    private_root: Path | None = None,
) -> dict[str, Any]:
    stats = load_public_paper_stats(private_root)
    text = format_public_stats_html(stats)
    delivery = await deliver_notification(
        event_type="MARKET_SUMMARY",
        text=text,
        chat_env=chat_env,
        symbol="SYSTEM",
        dry_run=not send,
    )
    row = {"ts": _now(), "schema": "public_channel_stats_run.v1", "status": "sent" if send else "dry_run", "delivery": delivery}
    append_audit(row)
    return row
