"""Dry-run or send a policy-routed Telegram smoke notification."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.subscriptions import list_users  # noqa: E402
from src.utils.telegram_delivery_router import deliver_notification  # noqa: E402


async def _run(args: argparse.Namespace) -> dict:
    load_dotenv()
    users = list_users() if args.use_subscribers else []
    return await deliver_notification(
        event_type=args.event_type,
        text=args.text,
        users=users,
        chat_env=args.chat_env,
        admin_chat_env=args.admin_chat_env,
        symbol=args.symbol,
        dry_run=not args.send,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event-type", default="MARKET_SUMMARY")
    ap.add_argument("--text", default="Telegram delivery smoke: routing check.")
    ap.add_argument("--symbol", default="")
    ap.add_argument("--chat-env", default="TELEGRAM_NOTIFICATION_CHAT_ID")
    ap.add_argument("--admin-chat-env", default="TELEGRAM_ADMIN_CHAT_ID")
    ap.add_argument("--use-subscribers", action="store_true")
    ap.add_argument("--send", action="store_true", help="actually send; default is dry-run")
    args = ap.parse_args()
    print(json.dumps(asyncio.run(_run(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
