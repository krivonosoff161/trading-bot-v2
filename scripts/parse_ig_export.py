"""Parse an Instagram "Download Your Information" export (Messages, JSON) and
extract all shared reel/post URLs from a DM thread.

IG export layout:
    <export>/.../messages/inbox/<thread_dir>/message_1.json (message_2.json, ...)
A shared post/reel is a message with a "share" object holding a "link".
(Non-ASCII names in the export are mojibake-encoded, but URLs are ASCII → safe.)

Usage:
    python scripts/parse_ig_export.py <export_dir>                  # list threads + share counts
    python scripts/parse_ig_export.py <export_dir> --thread <name>  # extract URLs from matching thread
    python scripts/parse_ig_export.py <export_dir> --thread <name> --out urls.txt
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

URL_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:reel|reels|p|tv)/[A-Za-z0-9_\-]+", re.I
)


def find_threads(export_dir: Path) -> list[Path]:
    return sorted({p.parent for p in export_dir.rglob("message_*.json")})


def thread_urls(thread_dir: Path) -> list[str]:
    urls: list[str] = []
    for jf in sorted(thread_dir.glob("message_*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for msg in data.get("messages", []):
            share = msg.get("share") or {}
            urls += URL_RE.findall(share.get("link", "") or "")
            urls += URL_RE.findall(msg.get("content", "") or "")
    return list(dict.fromkeys(u.rstrip("/") for u in urls))  # dedupe, keep order


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract shared reel/post URLs from an IG messages export.")
    parser.add_argument("export_dir", type=Path)
    parser.add_argument("--thread", help="substring of the thread folder name to target")
    parser.add_argument("--out", type=Path, help="write URLs here (one per line)")
    args = parser.parse_args()

    threads = find_threads(args.export_dir)
    if not threads:
        raise SystemExit("No message_*.json found — point at the unzipped export root.")

    if not args.thread:
        print(f"Тредов: {len(threads)} | ссылок на пост/reel в каждом:")
        for t in sorted(threads, key=lambda d: -len(thread_urls(d))):
            print(f"  {len(thread_urls(t)):4d}  {t.name}")
        print("\nПовтори с --thread <часть_имени> чтобы вытащить ссылки.")
        return

    targets = [t for t in threads if args.thread.lower() in t.name.lower()]
    if not targets:
        raise SystemExit(f"Тред с '{args.thread}' не найден.")
    urls = list(dict.fromkeys(u for t in targets for u in thread_urls(t)))
    print(f"Тредов сматчено: {len(targets)} | уникальных ссылок: {len(urls)}")
    if args.out:
        args.out.write_text("\n".join(urls) + "\n", encoding="utf-8")
        print(f"Записано → {args.out}")
    else:
        print("\n".join(urls))


if __name__ == "__main__":
    main()
