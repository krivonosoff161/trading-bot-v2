from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from src.scout.public_channel.collector import collect_public_source_items
from src.scout.public_channel.editor import build_post
from src.scout.public_channel.safety import validate_public_post
from src.scout.public_channel.stats import format_public_stats_html, load_public_paper_stats
from src.scout.public_channel.storage import append_audit, mark_sent, was_sent
from src.scout.public_channel.telegram_format import format_telegram_html
from src.utils.telegram_delivery_router import deliver_notification


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def publish_news_once(
    *,
    limit: int = 3,
    send: bool = False,
    use_llm: bool = False,
    chat_env: str = "SCANNER_CHAT_ID",
) -> dict[str, Any]:
    items = collect_public_source_items()
    summary = {"schema": "public_channel_news_run.v1", "items": len(items), "posted": 0, "skipped": 0, "rows": []}
    for item in items:
        if summary["posted"] >= limit:
            break
        if was_sent(item.key):
            summary["skipped"] += 1
            continue
        post, usage = await build_post(item, use_llm=use_llm)
        ok, reason = validate_public_post(post)
        if not ok:
            summary["skipped"] += 1
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
        row = {"ts": _now(), "status": "sent" if send else "dry_run", "post": post.to_dict(), "delivery": delivery, "usage": usage}
        append_audit(row)
        summary["rows"].append(row)
        summary["posted"] += 1
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
