import os, requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip("'\"")
r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", params={"limit": 100})
data = r.json()
chats = {}
for u in data.get("result", []):
    for key in ["message", "my_chat_member", "channel_post", "chat_member"]:
        if key in u:
            c = u[key].get("chat", {})
            cid = c.get("id")
            ctype = c.get("type", "")
            title = c.get("title", c.get("username", ""))
            if cid and ctype in ("group", "supergroup", "channel"):
                chats[cid] = (ctype, title)
if chats:
    for cid, (ctype, title) in chats.items():
        print(f"{ctype}: {cid} — {title}")
else:
    print("No group chats found in recent updates")
    print(f"Total updates: {len(data.get('result', []))}")
