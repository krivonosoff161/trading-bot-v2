# -*- coding: utf-8 -*-
"""Orderbook + trades recorder — keyless public REST poller for the Theme-40 walls sub-lane.

No historical orderbook data exists, so wall / imbalance / spoof features cannot be backtested. This
recorder STARTS collecting that data forward, so the lane is not closed with "needs data". It polls ONLY
the OKX PUBLIC endpoints (`/api/v5/market/books`, `/api/v5/market/trades`) over plain HTTP — no API key,
no account/order/private endpoint, no secrets. Bounded by design: a wall-clock duration, a per-symbol
top-N, a stop-file, file rotation, and a disk-retention cap. Writes only under the private research root.

`http_get` is injectable so the writer/rotation/retention/status are fully testable without network.
Research-only: it records data; it never trades, never promotes, and carries no paper-ready concept.
"""
from __future__ import annotations

import gzip
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

OKX_BASE = "https://www.okx.com"
BOOKS_PATH = "/api/v5/market/books"
TRADES_PATH = "/api/v5/market/trades"
DEFAULT_DEPTH = 50            # book levels per snapshot (enough to see resting walls)
DEFAULT_INTERVAL_S = 2.0     # poll cadence
DEFAULT_MAX_DISK_MB = 200.0  # retention cap for the recorder's output
_USER_AGENT = "strategy-lab-research/1.0 (+public-market-data)"

HttpGet = Callable[[str], Any]


class RecorderError(RuntimeError):
    """Network/HTTP/parse failure (carries only a static reason, never keys/urls)."""


def _default_http_get(url: str, *, timeout: float = 10.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - public https only
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - normalize to a keyless error type
        raise RecorderError(f"public_get_failed:{type(exc).__name__}") from exc


def _url(path: str, params: dict[str, str]) -> str:
    return f"{OKX_BASE}{path}?{urllib.parse.urlencode(params)}"


def poll_symbol(symbol: str, *, depth: int, http_get: HttpGet) -> dict[str, Any]:
    """One book snapshot + recent trades for a symbol (public only). Returns a normalized record."""
    inst = symbol.replace("_", "-")
    book = http_get(_url(BOOKS_PATH, {"instId": inst, "sz": str(depth)}))
    trades = http_get(_url(TRADES_PATH, {"instId": inst, "limit": "100"}))
    b = (book.get("data") or [{}])[0] if isinstance(book, dict) else {}
    tr = trades.get("data") or [] if isinstance(trades, dict) else []
    return {"symbol": symbol, "recv_ms": _now_ms(),
            "book": {"bids": b.get("bids") or [], "asks": b.get("asks") or [], "ts": b.get("ts")},
            "trades": [{"ts": t.get("ts"), "side": t.get("side"), "px": t.get("px"), "sz": t.get("sz")}
                       for t in tr]}


_CLOCK_MS = [0]  # monotonic-ish counter; replaced by real time in record(), injectable for tests


def _now_ms() -> int:
    return _CLOCK_MS[0]


def _out_dir(private_root: Path) -> Path:
    return Path(private_root) / "microstructure" / "recordings"


def write_record(private_root: Path, record: dict[str, Any], *, date_utc: str) -> Path:
    """Append one record to a rotating per-symbol/date gzip-jsonl file under the private root."""
    out_dir = _out_dir(private_root) / date_utc
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{record['symbol']}.jsonl.gz"
    with gzip.open(path, "at", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def prune_disk(private_root: Path, *, max_disk_mb: float) -> int:
    """Delete oldest recording files until under the cap. Returns files removed (retention)."""
    out_dir = _out_dir(private_root)
    files = sorted(out_dir.rglob("*.jsonl.gz"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    removed = 0
    cap = max_disk_mb * 1024 * 1024
    while files and total > cap:
        victim = files.pop(0)
        total -= victim.stat().st_size
        victim.unlink()
        removed += 1
    return removed


def status(private_root: Path) -> dict[str, Any]:
    """Data-readiness: what the recorder has collected so far (records, symbols, disk, span)."""
    out_dir = _out_dir(Path(private_root))
    files = list(out_dir.rglob("*.jsonl.gz")) if out_dir.exists() else []
    symbols: set[str] = set()
    records = 0
    for p in files:
        symbols.add(p.stem.replace(".jsonl", ""))
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                records += sum(1 for _ in f)
        except OSError:
            continue
    disk_mb = round(sum(p.stat().st_size for p in files) / 1024 / 1024, 3)
    # crude readiness gate: walls need many snapshots across symbols/time before any replay is meaningful
    ready = records >= 10_000 and len(symbols) >= 3
    return {"records": records, "symbols": sorted(symbols), "files": len(files), "disk_mb": disk_mb,
            "readiness": "ready_for_replay" if ready else "collecting_insufficient",
            "note": "orderbook recorder is keyless public REST; walls/imbalance need this forward data; "
                    "no replay/edge until enough is collected"}


def record(private_root: Path, symbols: list[str], *, duration_s: float = 10.0,
           interval_s: float = DEFAULT_INTERVAL_S, depth: int = DEFAULT_DEPTH,
           max_disk_mb: float = DEFAULT_MAX_DISK_MB, http_get: HttpGet | None = None,
           date_utc: str = "live") -> dict[str, Any]:
    """Bounded forward collection: poll public book+trades for top-N symbols until duration or stop-file."""
    from src.research_lab.stop_intent import is_stop_requested
    private_root = Path(private_root)
    getter = http_get or _default_http_get
    start = time.monotonic()
    polls = errors = written = 0
    while time.monotonic() - start < duration_s:
        if is_stop_requested(private_root):
            break
        _CLOCK_MS[0] = int(time.time() * 1000)
        for sym in symbols:
            try:
                rec = poll_symbol(sym, depth=depth, http_get=getter)
                write_record(private_root, rec, date_utc=date_utc)
                written += 1
            except RecorderError:
                errors += 1
        polls += 1
        prune_disk(private_root, max_disk_mb=max_disk_mb)
        time.sleep(max(0.0, interval_s))
    return {"polls": polls, "written": written, "errors": errors, "stopped_early": is_stop_requested(private_root),
            "status": status(private_root)}


def main() -> None:
    import argparse
    import os
    import sys
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.research_lab.paths import DEFAULT_PRIVATE_ROOT
    ap = argparse.ArgumentParser(description="Keyless public orderbook+trades recorder (research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--symbols", default="BTC-USDT-SWAP,ETH-USDT-SWAP,SOL-USDT-SWAP")
    ap.add_argument("--duration-seconds", type=float, default=10.0)
    ap.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_S)
    ap.add_argument("--depth", type=int, default=DEFAULT_DEPTH)
    ap.add_argument("--max-disk-mb", type=float, default=DEFAULT_MAX_DISK_MB)
    ap.add_argument("--status", action="store_true", help="print data-readiness only (no collection)")
    args = ap.parse_args()
    if args.status:
        print(json.dumps(status(Path(args.private_root)), ensure_ascii=False, indent=2))
        return
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    out = record(Path(args.private_root), syms, duration_s=args.duration_seconds,
                 interval_s=args.interval_seconds, depth=args.depth, max_disk_mb=args.max_disk_mb)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
