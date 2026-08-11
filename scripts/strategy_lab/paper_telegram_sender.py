"""Send validated paper Telegram previews to active subscriber bot chats."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils.runtime_root import load_runtime_dotenv  # noqa: E402

if __name__ == "__main__":
    load_runtime_dotenv(ROOT)

from scripts.subscriptions import list_delivery_users  # noqa: E402
from src.research_lab.paper_telegram_sender import (  # noqa: E402
    DeliveryNotAttempted,
    send_paper_telegram_previews,
)
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402
from src.utils.telegram import bot_token, send_message_to, send_photo_bytes_to  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--send", action="store_true",
                    help="actually send to active subscription users if TELEGRAM_BOT_TOKEN is configured")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.send:
        from src.research_lab.runtime_storage_rotation import install_runtime_stdout_tee

        install_runtime_stdout_tee(args.private_root, stream_id="telegram.stdout")

    users = list_delivery_users()
    ids = [
        str(user["chat_id"])
        for user in users
        if str(user.get("status") or "").lower() in {"active", "superadmin"}
    ]
    configured = bool(bot_token() and ids)

    async def _send_text(chat_id: str, text: str) -> int | None:
        try:
            return await send_message_to(chat_id, text)
        except aiohttp.ClientConnectorError as exc:
            raise DeliveryNotAttempted("telegram connection was not established") from exc

    async def _send_photo(chat_id: str, payload: bytes, caption: str) -> int | None:
        try:
            return await send_photo_bytes_to(
                chat_id,
                payload,
                caption=caption,
                parse_mode="HTML",
            )
        except aiohttp.ClientConnectorError as exc:
            raise DeliveryNotAttempted("telegram connection was not established") from exc

    summary = send_paper_telegram_previews(
        args.private_root,
        limit=args.limit,
        apply=args.send,
        paper_chat_configured=configured,
        paper_chat_ids_count=len(ids),
        recipient_ids=ids,
        send_text=_send_text if args.send and configured else None,
        send_photo=_send_photo if args.send and configured else None,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return
    mode = "send" if args.send else "dry-run"
    print(
        f"paper_telegram_sender mode={mode} read={summary['records_read']} "
        f"eligible={summary['eligible']} sent={summary['sent']} skipped={summary['skipped']} "
        f"duplicates={summary['duplicates']} errors={summary['errors']} "
        f"charts={summary['chart_sent_messages']}/{summary['chart_available_messages']} "
        f"configured={summary['configured']} targets={summary['targets']} "
        f"sends_network={summary['sends_network']}"
    )
    print(f"jsonl={summary['jsonl_path']}")
    print(f"snapshot={summary['snapshot_path']}")


if __name__ == "__main__":
    main()
