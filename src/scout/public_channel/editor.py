from __future__ import annotations

import json
import re
from typing import Any

from src.research_lab.llm_invocation_ledger import make_runtime_trace_context
from src.scout.public_channel.contracts import PublicChannelItem, PublicChannelPost, layer_label
from src.scout.public_channel.heuristics import cap, category_for, headline_for, what_happened_for, why_matters
from src.scout.public_channel.prompts import SYSTEM_PROMPT

_FIELD_LABEL_RE = re.compile(
    r"^\s*(?:"
    r"что\s+произошло|почему\s+(?:это\s+)?(?:важно|интересно)|"
    r"на\s+что\s+(?:смотреть|обратить\s+внимание)|что\s+это\s+значит|"
    r"what\s+happened|why\s+it\s+matters|watch\s+points?"
    r")\s*[:：\-–—]\s*",
    flags=re.I,
)


def _json_from_text(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    raw = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
    try:
        return json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except Exception:
                return None
    return None


def clean_editor_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    text = _FIELD_LABEL_RE.sub("", text).strip()
    return cap(text, limit)


def _clean_watch_points(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    points: list[str] = []
    for value in values[:5]:
        text = clean_editor_text(value, 180)
        if text and text not in points:
            points.append(text)
    return points[:3]


def deterministic_post(item: PublicChannelItem) -> PublicChannelPost:
    label = layer_label(item.layer)
    category = category_for(item)
    title = headline_for(item)
    return PublicChannelPost(
        key=item.key,
        headline=clean_editor_text(title, 120),
        category=category,
        what_happened=clean_editor_text(what_happened_for(item), 420),
        why_matters=clean_editor_text(why_matters(item), 360),
        watch_points=[
            f"Подтверждение от первичных источников по теме: {label}.",
            "Реакция цены, объема и ликвидности после публикации.",
        ],
        original_title=cap(item.title, 220),
        source=item.source,
        source_url=item.url,
        layer=item.layer,
    )


async def build_post(item: PublicChannelItem, *, use_llm: bool = False) -> tuple[PublicChannelPost, dict[str, Any]]:
    usage: dict[str, Any] = {"provider": "deterministic", "status": "not_requested"}
    if use_llm:
        from src.utils import llm_client

        user = json.dumps(
            {
                "schema": item.to_dict()["schema"],
                "title": item.title,
                "text": item.text[:5000],
                "source": item.source,
                "source_class": item.source_class,
                "lead_class": item.lead_class,
                "layer": item.layer,
                "layer_label": layer_label(item.layer),
                "event_type": item.event_type,
                "url": item.url,
                "extraction_status": item.raw.get("public_extraction_status") or "unknown",
                "text_quality": item.raw.get("public_text_quality") or "unknown",
                "machine_doc": item.raw.get("public_machine_doc") or {},
            },
            ensure_ascii=False,
        )
        trace_context = make_runtime_trace_context(
            surface="public_news.editor",
            source_ref=str(item.key or ""),
            source_payload={"item": item.to_dict()},
        )
        raw, usage = await llm_client.call(
            "mid",
            SYSTEM_PROMPT,
            user,
            json_mode=True,
            max_tokens=700,
            trace_context=trace_context,
        )
        data = _json_from_text(raw)
        if isinstance(data, dict):
            watch_points = _clean_watch_points(data.get("watch_points"))
            post = PublicChannelPost(
                key=item.key,
                headline=clean_editor_text(data.get("headline") or item.title, 120),
                category=clean_editor_text(data.get("category") or category_for(item), 80),
                what_happened=clean_editor_text(data.get("what_happened") or item.text or item.title, 520),
                why_matters=clean_editor_text(data.get("why_matters") or why_matters(item), 420),
                watch_points=watch_points,
                original_title=cap(item.title, 220),
                source=item.source,
                source_url=item.url,
                layer=item.layer,
                public_ok=bool(data.get("public_ok", True)),
                skip_reason=clean_editor_text(data.get("skip_reason") or "", 120),
            )
            if post.public_ok and not post.watch_points:
                fallback = deterministic_post(item)
                post = PublicChannelPost(
                    key=post.key,
                    headline=post.headline,
                    category=post.category,
                    what_happened=post.what_happened,
                    why_matters=post.why_matters,
                    watch_points=fallback.watch_points,
                    original_title=post.original_title,
                    source=post.source,
                    source_url=post.source_url,
                    layer=post.layer,
                    public_ok=post.public_ok,
                    skip_reason=post.skip_reason,
                )
            return post, usage
    return deterministic_post(item), usage
