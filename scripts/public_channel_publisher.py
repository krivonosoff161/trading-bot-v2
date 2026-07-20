"""Publish public news-channel posts or paper-bot stats.

Default is dry-run. Use --send only when the operator intentionally wants a
public Telegram message.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.scout.public_channel.publisher import collect_news_to_queue, publish_news_once, publish_stats_once  # noqa: E402
from src.utils.runtime_root import load_runtime_dotenv  # noqa: E402


async def _run(args: argparse.Namespace) -> dict:
    load_runtime_dotenv(ROOT)
    if args.mode == "collect":
        return collect_news_to_queue(max_queue=args.queue_max)
    if args.mode == "stats":
        private_root = Path(args.private_root) if args.private_root else None
        return await publish_stats_once(send=args.send, chat_env=args.chat_env, private_root=private_root)
    return await publish_news_once(
        limit=args.limit,
        send=args.send,
        use_llm=args.use_llm,
        chat_env=args.chat_env,
        collect=args.mode == "news",
        max_queue=args.queue_max,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["news", "collect", "publish", "stats"], default="news")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--queue-max", type=int, default=200)
    parser.add_argument("--use-llm", action="store_true", help="use configured LLM editor; default is deterministic")
    parser.add_argument("--send", action="store_true", help="actually send to Telegram; default is dry-run")
    parser.add_argument("--chat-env", default="SCANNER_CHAT_ID")
    parser.add_argument("--private-root", default="")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
