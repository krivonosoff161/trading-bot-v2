"""OKX instrument metadata — contract values (ctVal) for position sizing."""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_CACHE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ws" / "cache" / "ctvals_latest.json"


def fetch_ctvals(cache_path: Path = _CACHE_PATH) -> dict[str, float]:
    """Fetch contract values for all USDT-SWAP instruments from OKX.

    Falls back to local cache if network is unavailable.
    """
    url = "https://www.okx.com/api/v5/public/instruments?" + urlencode({"instType": "SWAP"})
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
        if payload.get("code") != "0":
            raise RuntimeError(
                f"OKX instruments failed: code={payload.get('code')} msg={payload.get('msg', '')}"
            )
        ctvals = {
            row["instId"]: float(row["ctVal"])
            for row in payload.get("data", [])
            if row.get("instId", "").endswith("-USDT-SWAP")
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(ctvals, indent=2, sort_keys=True), encoding="utf-8")
        return ctvals
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError):
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
        raise
