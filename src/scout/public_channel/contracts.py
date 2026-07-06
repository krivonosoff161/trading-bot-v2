from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any

SCHEMA_ITEM = "PublicChannelItem.v1"
SCHEMA_POST = "PublicChannelPost.v1"

LAYER_LABELS = {
    1: "крипта/мажоры",
    2: "альты/листинги/on-chain",
    3: "металлы/макро",
    4: "нефть/энергия",
    5: "акции/AI/pre-IPO",
    6: "связи между рынками",
}


def _clean(value: Any, limit: int = 2000) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].rstrip()


def stable_key(*parts: Any) -> str:
    raw = "|".join(_clean(p, 500) for p in parts if p is not None)
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


@dataclass(frozen=True)
class PublicChannelItem:
    key: str
    title: str
    url: str
    source: str
    source_class: str
    lead_class: str
    layer: int | None
    event_type: str
    published_at: str | None
    text: str
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA_ITEM, **asdict(self)}


@dataclass(frozen=True)
class PublicChannelPost:
    key: str
    headline: str
    category: str
    what_happened: str
    why_matters: str
    watch_points: list[str]
    original_title: str
    source: str
    source_url: str
    layer: int | None
    public_ok: bool = True
    skip_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA_POST, **asdict(self)}


def item_from_source(raw: dict[str, Any]) -> PublicChannelItem | None:
    title = _clean(raw.get("title") or raw.get("headline"), 500)
    url = _clean(raw.get("url") or raw.get("source_url"), 1000)
    text = _clean(raw.get("text") or raw.get("summary") or title, 3000)
    if not title and not text:
        return None
    source = _clean(raw.get("source") or raw.get("source_id") or "unknown", 120)
    try:
        layer = int(raw.get("layer")) if raw.get("layer") is not None else None
    except (TypeError, ValueError):
        layer = None
    key = stable_key(source, raw.get("event_key"), url) if raw.get("event_key") else stable_key(source, url, title)
    return PublicChannelItem(
        key=key,
        title=title or text[:120],
        url=url,
        source=source,
        source_class=_clean(raw.get("source_class"), 80),
        lead_class=_clean(raw.get("lead_class"), 80),
        layer=layer,
        event_type=_clean(raw.get("event_type") or raw.get("channel_kind") or "news", 100),
        published_at=raw.get("published_at") or raw.get("time"),
        text=text,
        raw=dict(raw),
    )


def layer_label(layer: int | None) -> str:
    if layer is None:
        return "рынок"
    return LAYER_LABELS.get(layer, f"L{layer}")
