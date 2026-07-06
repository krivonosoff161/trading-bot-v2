from __future__ import annotations

from typing import Any

from src.scout.public_channel.contracts import PublicChannelItem, layer_label


def cap(text: Any, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def category_for(item: PublicChannelItem) -> str:
    event = item.event_type.replace("_", " ").strip()
    if "listing" in event:
        return "листинг/биржа"
    if "liquidation" in event:
        return "ликвидации/потоки"
    if "dex" in event or "launch" in event:
        return "DEX/on-chain"
    if "earnings" in event or "filing" in event:
        return "акции/отчетность"
    if item.layer == 4:
        return "энергия/сырье"
    if item.layer == 3:
        return "металлы/макро"
    return "рыночный инфоповод"


def headline_for(item: PublicChannelItem) -> str:
    asset = str(item.raw.get("asset") or "").upper()
    event = item.event_type.lower()
    if item.source == "dexscreener" and asset:
        if event == "launch":
            return f"Новая DEX-пара: {asset}"
        return f"DEX-активность: {asset}"
    if "listing" in event and asset:
        return f"Биржевой листинг: {asset}"
    if "liquidation" in event and asset:
        return f"Поток ликвидаций: {asset}"
    return item.title


def what_happened_for(item: PublicChannelItem) -> str:
    raw = item.raw
    if item.source == "dexscreener":
        return _dex_what(raw)
    if item.source_class == "telegram_web":
        return cap(f"Публичный Telegram-источник опубликовал событие: {item.text or item.title}", 360)
    if "listing" in item.event_type.lower():
        return cap(f"Источник сообщил о листинге или изменении доступности инструмента: {item.title}", 360)
    return cap(item.text or item.title, 360)


def why_matters(item: PublicChannelItem) -> str:
    label = layer_label(item.layer)
    lead = (item.lead_class or "").upper()
    if lead == "LEADING":
        return f"Это ранний или первичный источник для слоя {label}; полезно смотреть, подтвердит ли рынок событие."
    if lead == "COINCIDENT":
        return f"Это потоковое рыночное наблюдение для слоя {label}; оно полезно как контекст, а не как команда к сделке."
    return f"Это новостной фон для слоя {label}; его стоит сверять с первоисточником и реакцией рынка."


def _dex_what(raw: dict) -> str:
    asset = str(raw.get("asset") or "актив").upper()
    dex = raw.get("dex") or "DEX"
    chain = raw.get("chain") or "сеть"
    parts = [f"DexScreener зафиксировал активность по {asset} на {chain}/{dex}."]
    nums = []
    for label, key, suffix in (
        ("Ликвидность около", "liquidity_usd", ""),
        ("объем за 24ч около", "volume_24h_usd", ""),
        ("изменение за 24ч", "price_change_24h_pct", "%"),
    ):
        try:
            value = float(raw.get(key))
        except (TypeError, ValueError):
            continue
        nums.append(f"{label} {value:+.1f}{suffix}" if suffix else f"{label} ${value:,.0f}")
    if nums:
        parts.append(", ".join(nums) + ".")
    return cap(" ".join(parts), 360)
