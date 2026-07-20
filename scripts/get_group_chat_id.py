import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.runtime_root import load_runtime_dotenv  # noqa: E402


def main() -> None:
    load_runtime_dotenv(ROOT)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip("'\"")
    response = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params={"limit": 100},
    )
    data = response.json()
    chats = {}
    for update in data.get("result", []):
        for key in ["message", "my_chat_member", "channel_post", "chat_member"]:
            if key in update:
                chat = update[key].get("chat", {})
                chat_id = chat.get("id")
                chat_type = chat.get("type", "")
                title = chat.get("title", chat.get("username", ""))
                if chat_id and chat_type in ("group", "supergroup", "channel"):
                    chats[chat_id] = (chat_type, title)
    if chats:
        for chat_id, (chat_type, title) in chats.items():
            print(f"{chat_type}: {chat_id} — {title}")
    else:
        print("No group chats found in recent updates")
        print(f"Total updates: {len(data.get('result', []))}")


if __name__ == "__main__":
    main()
