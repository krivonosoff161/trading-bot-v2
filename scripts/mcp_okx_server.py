"""
MCP server — read-only OKX tools for Claude Code.
Exposes: candles, ticker, active_universe, pump_stats, positions, balance.
Transport: stdio (Claude Code connects automatically via settings.json).
"""

import base64
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

# --- paths ---
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils.runtime_root import load_runtime_dotenv  # noqa: E402

if __name__ == "__main__":
    load_runtime_dotenv(ROOT)

API_KEY = os.getenv("OKX_API_KEY", "")
SECRET_KEY = os.getenv("OKX_SECRET_KEY", "")
PASSPHRASE = os.getenv("OKX_PASSPHRASE", "")
IS_DEMO = os.getenv("OKX_IS_DEMO", "0") == "1"
BASE_URL = "https://www.okx.com"

UNIVERSE_CACHE = ROOT / "scripts/ws/cache/active_universe.json"
PUMP_SIGNALS = ROOT / "logs/pump/pump_signals.jsonl"
PUMP_LABELS = ROOT / "logs/pump/pump_labels.jsonl"

mcp = FastMCP("okx-trading-bot")


# --- auth helpers ---

def _sign(timestamp: str, method: str, path: str, body: str = "") -> str:
    msg = f"{timestamp}{method}{path}{body}"
    mac = hmac.new(SECRET_KEY.encode(), msg.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()


def _auth_headers(method: str, path: str, body: str = "") -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    headers = {
        "OK-ACCESS-KEY": API_KEY,
        "OK-ACCESS-SIGN": _sign(ts, method, path, body),
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json",
    }
    if IS_DEMO:
        headers["x-simulated-trading"] = "1"
    return headers


def _get_public(path: str, params: dict = None) -> dict:
    try:
        r = httpx.get(BASE_URL + path, params=params, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def _get_private(path: str, params: dict = None) -> dict:
    query = ""
    if params:
        query = "?" + "&".join(f"{k}={v}" for k, v in params.items())
    full_path = path + query
    try:
        r = httpx.get(BASE_URL + full_path, headers=_auth_headers("GET", full_path), timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


# --- tools ---

@mcp.tool()
def get_candles(symbol: str, bar: str = "1m", limit: int = 50) -> str:
    """
    Get OHLCV candles for a symbol from OKX.
    symbol: e.g. 'BTC-USDT' or 'BTC-USDT-SWAP'
    bar: 1m, 5m, 15m, 1H, 4H, 1D
    limit: 1-300
    Returns: newest-first list of [ts, open, high, low, close, vol]
    """
    inst_id = symbol if symbol.endswith("-SWAP") else f"{symbol}-SWAP"
    data = _get_public("/api/v5/market/candles", {"instId": inst_id, "bar": bar, "limit": str(limit)})
    if "error" in data:
        return f"ERROR: {data['error']}"
    if data.get("code") != "0":
        return f"OKX error {data.get('code')}: {data.get('msg')}"
    candles = data.get("data", [])
    if not candles:
        return "No candles returned"
    rows = []
    for c in candles[:20]:  # show first 20 in text
        ts = datetime.fromtimestamp(int(c[0]) / 1000, tz=timezone.utc).strftime("%H:%M")
        rows.append(f"  {ts}  O={c[1]}  H={c[2]}  L={c[3]}  C={c[4]}  V={float(c[5]):.1f}")
    summary = f"{inst_id} | {bar} | {len(candles)} candles (showing {min(20, len(candles))})\n"
    return summary + "\n".join(rows)


@mcp.tool()
def get_ticker(symbol: str) -> str:
    """
    Get current price and 24h stats for a symbol.
    symbol: e.g. 'BTC-USDT' or 'ETH-USDT-SWAP'
    """
    inst_id = symbol if symbol.endswith("-SWAP") else f"{symbol}-SWAP"
    data = _get_public("/api/v5/market/ticker", {"instId": inst_id})
    if "error" in data:
        return f"ERROR: {data['error']}"
    if data.get("code") != "0":
        return f"OKX error {data.get('code')}: {data.get('msg')}"
    d = data.get("data", [{}])[0]
    return (
        f"{inst_id}\n"
        f"  Last:    {d.get('last')}\n"
        f"  Bid/Ask: {d.get('bidPx')} / {d.get('askPx')}\n"
        f"  24h chg: {d.get('sodUtc8')} open → {float(d.get('last',0)) - float(d.get('sodUtc8',0)):.4f} chg\n"
        f"  Vol 24h: {float(d.get('vol24h',0)):.0f} contracts\n"
        f"  VolCcy:  {float(d.get('volCcy24h',0)):.0f} USDT"
    )


@mcp.tool()
def get_active_universe() -> str:
    """
    Show what pairs the live screener currently tracks as hot (vol_spike + price_move).
    Reads scripts/ws/cache/active_universe.json
    """
    if not UNIVERSE_CACHE.exists():
        return "Cache file not found. Is ws_screener_live.py running?"
    try:
        data = json.loads(UNIVERSE_CACHE.read_text())
        active = data.get("active", [])
        updated = data.get("updated_at", "?")
        monitored = data.get("all_monitored", "?")
        if not active:
            return f"Active universe is EMPTY (updated {updated}, monitoring {monitored} pairs)"
        lines = [f"Active universe ({len(active)} pairs, updated {updated}, total monitored: {monitored}):"]
        for p in active:
            lines.append(f"  {p}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error reading cache: {e}"


@mcp.tool()
def get_pump_stats() -> str:
    """
    Show pump engine paper trading stats: WR, PF, avg_R, recent signals.
    Reads logs/pump/pump_signals.jsonl + pump_labels.jsonl
    """
    if not PUMP_SIGNALS.exists():
        return "pump_signals.jsonl not found. Has pump engine fired any signals?"

    signals: dict = {}
    try:
        for line in PUMP_SIGNALS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                s = json.loads(line)
                signals[s["id"]] = s
    except Exception as e:
        return f"Error reading signals: {e}"

    labels: dict = {}
    if PUMP_LABELS.exists():
        try:
            for line in PUMP_LABELS.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    lb = json.loads(line)
                    labels[lb["id"]] = lb
        except Exception as e:
            return f"Error reading labels: {e}"

    total = len(signals)
    labeled = len(labels)
    if labeled == 0:
        return f"Total signals: {total} | No labeled outcomes yet (labels file empty or missing)"

    wins = sum(1 for lb in labels.values() if lb.get("outcome") == "TP")
    losses = sum(1 for lb in labels.values() if lb.get("outcome") == "SL")
    wr = wins / labeled * 100 if labeled else 0
    r_values = [lb.get("r_multiple", 0) for lb in labels.values() if lb.get("r_multiple") is not None]
    avg_r = sum(r_values) / len(r_values) if r_values else 0
    gross_win = sum(r for r in r_values if r > 0)
    gross_loss = abs(sum(r for r in r_values if r < 0))
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

    # recent 10 signals
    recent = sorted(signals.values(), key=lambda s: s.get("ts", ""), reverse=True)[:10]
    recent_lines = []
    for s in recent:
        lb = labels.get(s["id"])
        outcome = lb.get("outcome", "open") if lb else "open"
        r = f"{lb.get('r_multiple', 0):+.2f}R" if lb else "open"
        recent_lines.append(f"  {s.get('ts','?')[:16]}  {s.get('symbol','?'):<20}  {outcome:<4}  {r}")

    return (
        f"Pump Engine Paper Stats\n"
        f"  Total signals: {total} | Labeled: {labeled}\n"
        f"  WR: {wr:.1f}%  ({wins}W / {losses}L)\n"
        f"  PF: {pf:.2f}  avg_R: {avg_r:+.3f}\n"
        f"\nRecent 10 signals:\n" + "\n".join(recent_lines)
    )


@mcp.tool()
def get_positions() -> str:
    """
    Get currently open positions on OKX (uses API credentials from .env).
    Shows symbol, side, size, unrealized PnL, entry price.
    """
    if not API_KEY:
        return "No API credentials in .env (OKX_API_KEY missing)"
    data = _get_private("/api/v5/account/positions", {"instType": "SWAP"})
    if "error" in data:
        return f"ERROR: {data['error']}"
    if data.get("code") != "0":
        return f"OKX error {data.get('code')}: {data.get('msg')}"
    positions = data.get("data", [])
    if not positions:
        return "No open positions"
    lines = [f"Open positions ({len(positions)}):"]
    for p in positions:
        side = "LONG" if p.get("posSide") == "long" else "SHORT" if p.get("posSide") == "short" else p.get("posSide","?")
        if float(p.get("pos", 0)) > 0:
            side = "LONG"
        elif float(p.get("pos", 0)) < 0:
            side = "SHORT"
        lines.append(
            f"  {p.get('instId','?'):<22}  {side:<5}  "
            f"pos={p.get('pos')}  entry={p.get('avgPx')}  "
            f"uPnL={float(p.get('upl',0)):+.4f}"
        )
    return "\n".join(lines)


@mcp.tool()
def get_balance() -> str:
    """
    Get account USDT balance from OKX (uses API credentials from .env).
    """
    if not API_KEY:
        return "No API credentials in .env (OKX_API_KEY missing)"
    data = _get_private("/api/v5/account/balance", {"ccy": "USDT"})
    if "error" in data:
        return f"ERROR: {data['error']}"
    if data.get("code") != "0":
        return f"OKX error {data.get('code')}: {data.get('msg')}"
    details = data.get("data", [{}])[0].get("details", [])
    if not details:
        return "No balance data returned"
    lines = ["Account balance:"]
    for d in details:
        lines.append(
            f"  {d.get('ccy','?'):<6}  equity={d.get('eq')}  "
            f"available={d.get('availEq')}  frozen={d.get('frozenBal')}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
