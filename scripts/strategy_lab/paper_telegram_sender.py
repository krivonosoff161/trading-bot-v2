"""Send validated paper Telegram previews through PAPER_CHAT_ID only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.paper_telegram_sender import send_paper_telegram_previews  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402
from src.utils.telegram import chat_ids, send_message_to, telegram_status  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--send", action="store_true",
                    help="actually send to PAPER_CHAT_ID if TELEGRAM_BOT_TOKEN is configured")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    status = telegram_status(chat_env="PAPER_CHAT_ID")
    ids = chat_ids("PAPER_CHAT_ID")

    async def _send_text(text: str) -> int | None:
        return await send_message_to(ids[0], text)

    summary = send_paper_telegram_previews(
        args.private_root,
        limit=args.limit,
        apply=args.send,
        paper_chat_configured=bool(status["configured"] and ids),
        paper_chat_ids_count=int(status["chat_ids_count"]),
        send_text=_send_text if args.send and status["configured"] and ids else None,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return
    mode = "send" if args.send else "dry-run"
    print(
        f"paper_telegram_sender mode={mode} read={summary['records_read']} "
        f"eligible={summary['eligible']} sent={summary['sent']} skipped={summary['skipped']} "
        f"errors={summary['errors']} configured={summary['configured']} sends_network={summary['sends_network']}"
    )
    print(f"jsonl={summary['jsonl_path']}")
    print(f"snapshot={summary['snapshot_path']}")


if __name__ == "__main__":
    main()
