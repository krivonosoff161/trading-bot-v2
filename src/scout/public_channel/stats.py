from __future__ import annotations

from pathlib import Path
from typing import Any


def load_public_paper_stats(private_root: Path | None = None) -> dict[str, Any]:
    from scripts.project_snapshot import paper_product_status

    return paper_product_status(private_root)


def format_public_stats_html(stats: dict[str, Any]) -> str:
    total = int(stats.get("product_trades") or stats.get("paper_total") or 0)
    active = int(stats.get("product_active_trades") or 0)
    outcomes = stats.get("training_by_result") or {}
    families = stats.get("product_active_by_family") or {}
    delivery_sent = int(stats.get("delivery_sent") or 0)
    delivery_cards = int(stats.get("delivery_sent_cards") or 0)
    delivery_errors = int(stats.get("delivery_errors") or 0)

    return "\n".join(
        [
            "<b>Токеномика</b>",
            "<b>Публичный срез paper-бота</b>",
            "",
            f"Сейчас под наблюдением: <b>{active}</b> paper-сигналов.",
            f"Всего записано: <b>{total}</b> paper-наблюдений.",
            f"Разобрано по исходам: <b>{sum(int(v or 0) for v in outcomes.values())}</b>.",
            "",
            "<b>Исходы:</b>",
            f"✅ take: {int(outcomes.get('take') or 0)}",
            f"❌ stop: {int(outcomes.get('stop') or 0)}",
            f"⚪ безубыток/simple BE: {int(outcomes.get('simple_be') or 0)}",
            f"⌛ истекло без входа: {int(outcomes.get('expired_no_entry') or 0)}",
            "",
            "<b>Активные семейства:</b>",
            *[f"• {name}: {count}" for name, count in list(families.items())[:5]],
            "",
            "<b>Доставка карточек:</b>",
            f"сообщений {delivery_sent}, карточек {delivery_cards}, ошибок {delivery_errors}.",
            "",
            "<i>Paper-режим. Это статистика наблюдений, не торговая рекомендация.</i>",
        ]
    )

