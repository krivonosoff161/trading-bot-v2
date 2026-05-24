"""Convert a browser cookie-export JSON (J2TEAM Cookies / Cookie-Editor) into a
Netscape cookies.txt — the format gallery-dl / yt-dlp need for logged-in
Instagram downloads (image carousels are behind a login wall).

How to get the input JSON:
    log into instagram.com in a browser -> "J2TEAM Cookies" (or Cookie-Editor)
    extension -> Export as file, leave the password field BLANK ->
    you get {"url": ..., "cookies": [ {name, value, domain, path, secure,
    expirationDate, session, ...}, ... ]}.

The output cookies.txt is a LIVE session token — keep it in the gitignored
media folder, treat it like a password, it expires after weeks (re-export).

Usage:
    python scripts/cookies_json_to_netscape.py <export.json> <out_cookies.txt>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def to_netscape(cookies: list[dict]) -> str:
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        domain = c.get("domain", "")
        flag = "TRUE" if domain.startswith(".") else "FALSE"  # applies to subdomains
        secure = "TRUE" if c.get("secure") else "FALSE"
        expiry = 0 if c.get("session") else int(c.get("expirationDate", 0) or 0)
        lines.append("\t".join([
            domain, flag, c.get("path", "/"), secure, str(expiry),
            c.get("name", ""), c.get("value", ""),
        ]))
    return "\n".join(lines) + "\n"


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: cookies_json_to_netscape.py <export.json> <out_cookies.txt>")
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    cookies = data.get("cookies", data) if isinstance(data, dict) else data
    Path(sys.argv[2]).write_text(to_netscape(cookies), encoding="utf-8")
    print(f"Converted {len(cookies)} cookies -> {sys.argv[2]}")


if __name__ == "__main__":
    main()
