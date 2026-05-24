"""Download Instagram image carousels / posts (the /p/ links) via gallery-dl,
using an exported session cookie. Image posts are behind a login wall, so a
cookie is required (reels are video and download anonymously — use grab_video.py).

Each post lands in its own folder:
    docs/инста трасткбрикция/_carousels/<shortcode>/

Cookie: export from a browser extension, then convert with
scripts/cookies_json_to_netscape.py. IG throttles fast request bursts, so a
per-request sleep is applied to reduce flags/bans on the account.

Usage:
    python scripts/grab_carousel.py --cookies <cookies.txt> <url> [url ...]
    python scripts/grab_carousel.py --cookies <cookies.txt> --from-file urls.txt
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "инста трасткбрикция" / "_carousels"
CODE_RE = re.compile(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_\-]+)")


def shortcode(url: str) -> str:
    m = CODE_RE.search(url)
    return m.group(1) if m else url.rstrip("/").rsplit("/", 1)[-1]


def grab(url: str, cookies: Path, sleep: float) -> bool:
    dest = OUT_DIR / shortcode(url)
    try:
        subprocess.run(
            [sys.executable, "-m", "gallery_dl", "--sleep-request", str(sleep),
             "--cookies", str(cookies), "-D", str(dest), url],
            check=True,
        )
        print(f"OK   {shortcode(url)}")
        return True
    except subprocess.CalledProcessError:
        print(f"FAIL {url}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Download IG carousels/posts via gallery-dl + cookie.")
    parser.add_argument("urls", nargs="*")
    parser.add_argument("--cookies", type=Path, required=True)
    parser.add_argument("--from-file", type=Path, help="text file with one URL per line")
    parser.add_argument("--sleep", type=float, default=2.0, help="seconds between requests")
    args = parser.parse_args()

    urls = list(args.urls)
    if args.from_file:
        urls += [ln.strip() for ln in args.from_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    urls = list(dict.fromkeys(urls))
    if not urls:
        parser.error("no URLs (pass URLs or --from-file)")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ok = sum(grab(u, args.cookies, args.sleep) for u in urls)
    print(f"Done: {ok}/{len(urls)} ok")


if __name__ == "__main__":
    main()
