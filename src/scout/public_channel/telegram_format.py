from __future__ import annotations

import html
from typing import Any

from src.scout.public_channel.contracts import PublicChannelPost


def format_telegram_html(post: PublicChannelPost) -> str:
    watch = "\n".join(f"• {_esc(point)}" for point in post.watch_points[:4])
    return "\n".join(
        [
            "<b>Токеномика</b>",
            f"<b>{_esc(post.headline)}</b>",
            f"<i>{_esc(post.category)}</i>",
            "",
            _esc(post.what_happened),
            "",
            _esc(post.why_matters),
            "",
            "<b>Следим за:</b>",
            watch,
            "",
            f"<b>Оригинал:</b> {_esc(post.original_title)}",
            f"🔗 <a href=\"{_esc(post.source_url)}\">источник</a>",
            "",
            "<i>Инфоповод для наблюдения. Не торговая рекомендация.</i>",
        ]
    )


def _esc(text: Any) -> str:
    return html.escape(str(text or ""), quote=True)
