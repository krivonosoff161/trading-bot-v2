# -*- coding: utf-8 -*-
"""
get_chat_id.py — показать chat_id тех, кто недавно писал боту.

Для настройки доставки сканера: напиши боту @lektorTP_bot любое сообщение
со СВОЕГО личного аккаунта, запусти этот скрипт → увидишь свой chat_id →
впиши SCANNER_CHAT_ID=<id> в .env.

Read-only. Токен бота НЕ печатает (берёт из .env). Ничего не отправляет.

Запуск:  python scripts/get_chat_id.py
"""
import os
import sys
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.utils.runtime_root import load_runtime_dotenv  # noqa: E402


def main():
    load_runtime_dotenv(_ROOT)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip("'\"")
    if not token:
        print("TELEGRAM_BOT_TOKEN не найден в .env")
        raise SystemExit(1)
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
    data = r.json()
    if not data.get("ok"):
        print(f"Telegram error: {data}")
        return
    updates = data.get("result", [])
    if not updates:
        print("Обновлений нет. Напиши боту @lektorTP_bot сообщение со своего акка и запусти снова.")
        print("(если продукт-бот сейчас запущен — он мог 'съесть' апдейты; останови его и повтори)")
        return

    seen = {}
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None or cid in seen:
            continue
        name = chat.get("title") or " ".join(
            x for x in (chat.get("first_name"), chat.get("last_name")) if x
        ) or chat.get("username") or "?"
        seen[cid] = (chat.get("type"), name)

    print("=== кто недавно писал боту ===")
    for cid, (ctype, name) in seen.items():
        print(f"  chat_id={cid}   type={ctype}   '{name}'")
    print("\nТвой ЛИЧНЫЙ (type=private, твоё имя) → впиши в .env:")
    print("  SCANNER_CHAT_ID=<твой chat_id>")


if __name__ == "__main__":
    main()
