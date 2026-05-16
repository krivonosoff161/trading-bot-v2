"""Backtest and research harness for ws_pump_orchestrator.

The script has three layers:
  1. live log analysis, no API required;
  2. candle cache + Path B / approximate Path A simulation;
  3. directed parameter sweeps and final text report.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

try:
    import yaml
except Exception:  # pragma: no cover - optional in some local shells
    yaml = None


ROOT = Path(__file__).resolve().parents[2]
LIVE_SIGNALS = ROOT / "logs" / "pump" / "pump_signals.jsonl"
LIVE_LABELS = ROOT / "logs" / "pump" / "pump_labels.jsonl"
ACTIVE_UNIVERSE = ROOT / "scripts" / "ws" / "cache" / "active_universe.json"
WS_CACHE = ROOT / "scripts" / "ws" / "cache"
PUMP_CACHE = ROOT / "scripts" / "backtest" / "cache" / "pump"
RESULTS_DIR = ROOT / "scripts" / "backtest" / "results"
REPORT_PATH = RESULTS_DIR / "pump_sweep_report.txt"

OKX_HISTORY = "https://www.okx.com/api/v5/market/history-candles"
EXTRA_SYMBOLS = {
    "LAYER-USDT-SWAP",
    "BASED-USDT-SWAP",
    "AI-USDT-SWAP",
    "XRP-USDT-SWAP",
    "DOGE-USDT-SWAP",
    "BTC-USDT-SWAP",
    "ETH-USDT-SWAP",
    "SOL-USDT-SWAP",
}
WIN_REASONS = {"TP"}
LOSS_REASONS = {"SL"}
COUNTED_REASONS = WIN_REASONS | LOSS_REASONS


@dataclass(slots=True)
class SimParams:
    vol_mult: float = 2.0
    price_pct: float = 1.5
    min_usd_vol: float = 30_000.0
    pump_phase_max_pct: float = 3.0
    alert_cooldown_sec: int = 300
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 2.5
    min_tp_pct: float = 1.0
    fee_rt_pct: float = 0.10
    cb_daily_loss_pct: float = 4.0
    cb_daily_halt_cooldown_min: int = 120
    cb_cooldown_sl: int = 3
    cb_cooldown_min: int = 30
    session_ban_sl_no_tp: int = 3
    main_sl_to_counter: int = 99
    counter_sl_to_ban: int = 1
    max_open_positions: int = 2
    paper_max_hold_min: float = 9999.0
    confirmation_reversal_max_pct: float = 0.5
    confirmation_vol_ratio: float = 0.8
    path: str = "B"
    name: str = "default"


@dataclass(slots=True)
class PreparedSymbol:
    sym: str
    ts_ms: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    vol: np.ndarray
    dollar_vol: np.ndarray
    baseline_vol: np.ndarray
    atr: np.ndarray
    baseline_price: float


@dataclass(slots=True)
class Position:
    sym: str
    side: str
    section: str
    entry_ts_ms: int
    entry_price: float
    sl_price: float
    tp_price: float
    atr: float
    vol_ratio: float
    pct_move: float
    dollar_vol: float
    mfe_pct: float = 0.0
    mae_pct: float = 0.0


@dataclass(slots=True)
class PairState:
    section: str = "main"
    main_direction: str = "buy"
    position: Position | None = None
    last_signal_ts_ms: int = -10**18
    cooldown_until_ms: int = 0
    banned_until_ms: int = 0
    main_sl_streak: int = 0
    counter_sl_streak: int = 0
    total_sl_streak: int = 0
    sl_today: int = 0
    tp_today: int = 0
    day: str = ""
    pending_side: str = ""
    pending_vol: float = 0.0
    pending_signal_close: float = 0.0
    pending_since_ms: int = 0


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"skip invalid json: {path}:{line_no}: {exc}")
    return rows


def safe_float(value, default: float = float("nan")) -> float:
    if value in (None, ""):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def parse_ts(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None
    try:
        return pd.Timestamp(value).tz_convert("UTC")
    except Exception:
        try:
            ts = pd.Timestamp(value)
            return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        except Exception:
            return None


def load_config_defaults() -> SimParams:
    params = SimParams()
    if yaml is None:
        return params
    try:
        project = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return params
    cfg = project.get("pump_orchestrator", {}) or {}
    fields = {name for name in SimParams.__dataclass_fields__}
    updates = {}
    for key, value in cfg.items():
        if key == "max_main_slots":
            updates["max_open_positions"] = int(value)
        elif key in fields:
            updates[key] = value
    if "main_sl_to_counter" in updates:
        updates["main_sl_to_counter"] = int(updates["main_sl_to_counter"])
    return replace(params, **updates)


def load_live_joined() -> tuple[list[dict], list[dict], list[dict]]:
    signals = {
        str(row.get("signal_id")): row
        for row in read_jsonl(LIVE_SIGNALS)
        if row.get("type") in (None, "ENTRY") and row.get("signal_id")
    }
    labels = [
        row
        for row in read_jsonl(LIVE_LABELS)
        if row.get("type") in (None, "EXIT") and row.get("signal_id")
    ]

    joined = []
    for label in labels:
        sig = signals.get(str(label.get("signal_id")))
        if not sig:
            continue
        merged = dict(sig)
        merged.update({f"label_{k}": v for k, v in label.items()})
        merged["exit_reason"] = str(label.get("exit_reason", "")).upper()
        merged["net_pnl_pct"] = safe_float(label.get("net_pnl_pct"))
        merged["hold_min"] = safe_float(label.get("hold_min"))
        merged["mfe_r"] = safe_float(label.get("mfe_r"))
        merged["mae_r"] = safe_float(label.get("mae_r"))
        merged["entry_price"] = safe_float(label.get("entry_price"), safe_float(sig.get("signal_close")))
        merged["atr_pct"] = safe_float(sig.get("atr")) / merged["entry_price"] * 100.0 if merged["entry_price"] else float("nan")
        ts = parse_ts(sig.get("ts_utc") or label.get("opened_at"))
        merged["hour_utc"] = int(ts.hour) if ts is not None else None
        joined.append(merged)
    return list(signals.values()), labels, joined


def metric_summary(rows: Iterable[dict], min_n: int = 0) -> dict:
    rows = list(rows)
    counted = [r for r in rows if str(r.get("exit_reason", "")).upper() in COUNTED_REASONS]
    n = len(counted)
    wins = sum(1 for r in counted if str(r.get("exit_reason", "")).upper() in WIN_REASONS)
    losses = sum(1 for r in counted if str(r.get("exit_reason", "")).upper() in LOSS_REASONS)
    pnl = [safe_float(r.get("net_pnl_pct")) for r in counted]
    pnl = [x for x in pnl if math.isfinite(x)]
    pos = sum(x for x in pnl if x > 0)
    neg = abs(sum(x for x in pnl if x < 0))

    def avg(key: str) -> float:
        vals = [safe_float(r.get(key)) for r in counted]
        vals = [x for x in vals if math.isfinite(x)]
        return float(np.mean(vals)) if vals else float("nan")

    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "wr": wins / n * 100.0 if n else float("nan"),
        "avg_net": float(np.mean(pnl)) if pnl else float("nan"),
        "pf": pos / neg if neg > 0 else (float("inf") if pos > 0 else float("nan")),
        "avg_hold_min": avg("hold_min"),
        "avg_mfe_r": avg("mfe_r"),
        "avg_mae_r": avg("mae_r"),
        "insufficient": n < min_n,
    }


def fmt_pct(value: float, digits: int = 1) -> str:
    return "n/a" if not math.isfinite(value) else f"{value:.{digits}f}%"


def fmt_num(value: float, digits: int = 2) -> str:
    return "n/a" if not math.isfinite(value) else f"{value:.{digits}f}"


def live_breakdowns(joined: list[dict]) -> list[tuple[str, dict]]:
    specs = [
        ("vol_ratio < 2.0", lambda r: safe_float(r.get("vol_ratio")) < 2.0),
        ("vol_ratio 2.0-3.0", lambda r: 2.0 <= safe_float(r.get("vol_ratio")) <= 3.0),
        ("vol_ratio > 3.0", lambda r: safe_float(r.get("vol_ratio")) > 3.0),
        ("dollar_vol < 50k", lambda r: safe_float(r.get("dollar_vol")) < 50_000),
        ("dollar_vol 50k-200k", lambda r: 50_000 <= safe_float(r.get("dollar_vol")) <= 200_000),
        ("dollar_vol > 200k", lambda r: safe_float(r.get("dollar_vol")) > 200_000),
        ("section=main", lambda r: str(r.get("section") or r.get("label_section") or "").lower() == "main"),
        ("section=counter", lambda r: str(r.get("section") or r.get("label_section") or "").lower() == "counter"),
        ("hold_min < 5", lambda r: safe_float(r.get("hold_min")) < 5),
        ("hold_min 5-15", lambda r: 5 <= safe_float(r.get("hold_min")) <= 15),
        ("hold_min > 15", lambda r: safe_float(r.get("hold_min")) > 15),
        ("atr/price < 0.3%", lambda r: safe_float(r.get("atr_pct")) < 0.3),
        ("atr/price 0.3-0.8%", lambda r: 0.3 <= safe_float(r.get("atr_pct")) <= 0.8),
        ("atr/price > 0.8%", lambda r: safe_float(r.get("atr_pct")) > 0.8),
    ]
    return [(name, metric_summary([row for row in joined if fn(row)], min_n=10)) for name, fn in specs]


def render_breakdown_table(rows: list[tuple[str, dict]]) -> list[str]:
    lines = ["Filter                     n     WR   Avg_net  Avg_mfe_r  Avg_mae_r"]
    for name, m in rows:
        if m["insufficient"]:
            lines.append(f"{name:<25} {m['n']:>3}  insufficient data")
            continue
        lines.append(
            f"{name:<25} {m['n']:>3}  {fmt_pct(m['wr']):>6}  "
            f"{fmt_pct(m['avg_net'], 2):>8}  {fmt_num(m['avg_mfe_r']):>9}  {fmt_num(m['avg_mae_r']):>9}"
        )
    return lines


def load_symbol_universe(signals: list[dict]) -> list[str]:
    symbols = {str(row.get("sym")) for row in signals if row.get("sym")}
    symbols.update(EXTRA_SYMBOLS)
    try:
        payload = json.loads(ACTIVE_UNIVERSE.read_text(encoding="utf-8"))
        meta = payload.get("symbols")
        if isinstance(meta, dict):
            symbols.update(str(sym) for sym in meta.keys())
        active = payload.get("active")
        if isinstance(active, list):
            symbols.update(str(sym) for sym in active)
    except Exception:
        pass
    return sorted(sym for sym in symbols if sym and sym != "None")


def pkl_cache_candidates(sym: str, days: int) -> list[Path]:
    return [
        PUMP_CACHE / f"{sym}_1m_{days}d.pkl",
        PUMP_CACHE / f"{sym}_1m_30d.pkl",
        WS_CACHE / f"{sym}_1m_{days}d.pkl",
        WS_CACHE / f"{sym}_1m_30d.pkl",
        WS_CACHE / f"{sym}_1m_10d.pkl",
    ]


def normalize_frame(df: pd.DataFrame, sym: str) -> pd.DataFrame:
    df = df.copy()
    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        df = df.set_index("ts")
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    rename = {
        "volume": "vol",
        "vol_contracts": "vol",
        "vol_ccy_usdt": "dollar_vol",
        "quote_vol": "dollar_vol",
        "vol_usdt": "dollar_vol",
    }
    df = df.rename(columns=rename)
    if "dollar_vol" not in df.columns:
        if "vol_ccy_quote" in df.columns:
            df["dollar_vol"] = df["vol_ccy_quote"]
        else:
            df["dollar_vol"] = df["close"].astype(float) * df["vol"].astype(float)
    keep = ["open", "high", "low", "close", "vol", "dollar_vol"]
    missing = [col for col in keep if col not in df.columns]
    if missing:
        raise ValueError(f"{sym}: missing candle columns {missing}")
    for col in keep:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[keep].dropna().sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df.attrs["symbol"] = sym
    return df


def load_cached_frame(
    sym: str,
    days: int,
    required_start: pd.Timestamp | None = None,
    required_end: pd.Timestamp | None = None,
) -> pd.DataFrame | None:
    for path in pkl_cache_candidates(sym, days):
        if not path.exists():
            continue
        try:
            df = normalize_frame(pd.read_pickle(path), sym)
            if required_start is not None and df.index.max() < required_start:
                continue
            if required_end is not None and df.index.min() > required_end:
                continue
            if required_start is not None and required_end is not None:
                overlap = df.loc[(df.index >= required_start) & (df.index <= required_end)]
                if len(overlap) < 60:
                    continue
            return df
        except Exception as exc:
            print(f"cache read failed: {path}: {exc}")
    return None


def okx_request(params: dict, retries: int = 5) -> dict:
    url = OKX_HISTORY + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt + 1 < retries:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt + 1 < retries:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise
    raise RuntimeError("unreachable retry loop")


def fetch_okx_1m(sym: str, days: int, end_ts: pd.Timestamp | None = None) -> pd.DataFrame:
    end_ts = end_ts or pd.Timestamp.utcnow().tz_convert("UTC")
    start_ts = end_ts - pd.Timedelta(days=days)
    after_ms = int(end_ts.timestamp() * 1000) + 60_000
    start_ms = int(start_ts.timestamp() * 1000)
    rows: list[list] = []
    seen_oldest = None

    while after_ms > start_ms:
        payload = okx_request({"instId": sym, "bar": "1m", "limit": "300", "after": str(after_ms)})
        if payload.get("code") != "0":
            raise RuntimeError(f"OKX {sym} failed: code={payload.get('code')} msg={payload.get('msg')}")
        data = payload.get("data", [])
        if not data:
            break
        closed = [row for row in data if len(row) > 8 and str(row[8]) == "1"]
        rows.extend(closed)
        ts_values = [int(row[0]) for row in data if row and row[0]]
        if not ts_values:
            break
        oldest = min(ts_values)
        if seen_oldest == oldest:
            break
        seen_oldest = oldest
        after_ms = oldest - 1
        if oldest <= start_ms:
            break
        time.sleep(0.05)

    if not rows:
        raise RuntimeError(f"no OKX candles returned for {sym}")

    records = []
    for row in rows:
        ts_ms = int(row[0])
        if ts_ms < start_ms:
            continue
        records.append(
            {
                "ts": pd.Timestamp(ts_ms, unit="ms", tz="UTC"),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "vol": float(row[5]),
                "dollar_vol": float(row[6]),
            }
        )
    if not records:
        raise RuntimeError(f"no in-range OKX candles for {sym}")
    df = pd.DataFrame.from_records(records).drop_duplicates("ts").sort_values("ts").set_index("ts")
    return normalize_frame(df, sym)


def ensure_candle_cache(
    symbols: list[str],
    days: int,
    fetch: bool,
    end_ts: pd.Timestamp | None = None,
    required_start: pd.Timestamp | None = None,
    required_end: pd.Timestamp | None = None,
    workers: int = 4,
) -> dict[str, pd.DataFrame]:
    PUMP_CACHE.mkdir(parents=True, exist_ok=True)
    frames: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for idx, sym in enumerate(symbols, start=1):
        cached = load_cached_frame(sym, days, required_start=required_start, required_end=required_end)
        if cached is not None:
            frames[sym] = cached
            continue
        if not fetch:
            print(f"missing cache, skip: {sym}")
            continue
        missing.append(sym)

    if fetch and missing:
        def fetch_one(sym: str) -> tuple[str, pd.DataFrame | None, str | None]:
            try:
                df = fetch_okx_1m(sym, days=days, end_ts=end_ts)
                out = PUMP_CACHE / f"{sym}_1m_{days}d.pkl"
                df.to_pickle(out)
                return sym, df, None
            except Exception as exc:
                return sym, None, str(exc)

        max_workers = max(1, int(workers))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(fetch_one, sym): sym for sym in missing}
            for done, future in enumerate(as_completed(futures), start=1):
                sym, df, err = future.result()
                if err:
                    print(f"fetch failed {done}/{len(missing)} {sym}: {err}")
                    continue
                assert df is not None
                frames[sym] = df
                print(f"fetch ok {done}/{len(missing)} {sym}: rows={len(df)}")
    return frames


def prepare_market(
    frames: dict[str, pd.DataFrame],
    entry_start: pd.Timestamp,
    entry_end: pd.Timestamp,
    close_end: pd.Timestamp,
) -> tuple[dict[str, PreparedSymbol], list[tuple[int, str, int]]]:
    prepared: dict[str, PreparedSymbol] = {}
    events: list[tuple[int, str, int]] = []
    warmup_start = entry_start - pd.Timedelta(minutes=20)

    for sym, frame in frames.items():
        df = frame.loc[(frame.index >= warmup_start) & (frame.index <= close_end)].copy()
        if len(df) < 20:
            continue
        vol = df["vol"].to_numpy(dtype="float64")
        high = df["high"].to_numpy(dtype="float64")
        low = df["low"].to_numpy(dtype="float64")
        baseline_vol = np.full(len(df), np.nan, dtype="float64")
        atr = np.full(len(df), np.nan, dtype="float64")
        for i in range(10, len(df)):
            baseline_vol[i] = float(np.mean(vol[i - 10 : i]))
            atr[i] = float(np.mean(high[i - 10 : i] - low[i - 10 : i]))

        ts_ms = (df.index.view("int64") // 1_000_000).astype("int64")
        start_ms = int(entry_start.timestamp() * 1000)
        close_end_ms = int(close_end.timestamp() * 1000)
        baseline_rows = df.loc[df.index <= entry_start]
        baseline_price = float((baseline_rows["close"].iloc[-1] if not baseline_rows.empty else df["close"].iloc[0]))

        ps = PreparedSymbol(
            sym=sym,
            ts_ms=ts_ms,
            open=df["open"].to_numpy(dtype="float64"),
            high=df["high"].to_numpy(dtype="float64"),
            low=df["low"].to_numpy(dtype="float64"),
            close=df["close"].to_numpy(dtype="float64"),
            vol=vol,
            dollar_vol=df["dollar_vol"].to_numpy(dtype="float64"),
            baseline_vol=baseline_vol,
            atr=atr,
            baseline_price=baseline_price,
        )
        prepared[sym] = ps
        for i, ts in enumerate(ts_ms):
            if start_ms <= int(ts) <= close_end_ms:
                events.append((int(ts), sym, i))

    events.sort(key=lambda x: (x[0], x[1]))
    return prepared, events


def day_from_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def hour_from_ms(ts_ms: int) -> int:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).hour


def open_position(ps: PreparedSymbol, i: int, state: PairState, params: SimParams, side: str, vol_ratio: float, pct_move: float) -> Position:
    entry = float(ps.close[i])
    atr = float(ps.atr[i])
    min_tp = params.min_tp_pct / 100.0
    if side == "buy":
        sl = round(entry - atr * params.sl_atr_mult, 8)
        tp = round(max(entry + atr * params.tp_atr_mult, entry * (1.0 + min_tp)), 8)
    else:
        sl = round(entry + atr * params.sl_atr_mult, 8)
        tp = round(min(entry - atr * params.tp_atr_mult, entry * (1.0 - min_tp)), 8)
    return Position(
        sym=ps.sym,
        side=side,
        section=state.section,
        entry_ts_ms=int(ps.ts_ms[i]),
        entry_price=entry,
        sl_price=sl,
        tp_price=tp,
        atr=atr,
        vol_ratio=float(vol_ratio),
        pct_move=float(pct_move),
        dollar_vol=float(ps.dollar_vol[i]),
    )


def close_position_if_hit(pos: Position, ps: PreparedSymbol, i: int, params: SimParams) -> dict | None:
    high = float(ps.high[i])
    low = float(ps.low[i])
    close = float(ps.close[i])
    ts_ms = int(ps.ts_ms[i])

    if pos.side == "buy":
        gain = (high - pos.entry_price) / pos.entry_price * 100.0
        loss = (low - pos.entry_price) / pos.entry_price * 100.0
        if low <= pos.sl_price:
            reason, exit_price = "SL", pos.sl_price
        elif high >= pos.tp_price:
            reason, exit_price = "TP", pos.tp_price
        else:
            reason, exit_price = None, None
    else:
        gain = (pos.entry_price - low) / pos.entry_price * 100.0
        loss = (pos.entry_price - high) / pos.entry_price * 100.0
        if high >= pos.sl_price:
            reason, exit_price = "SL", pos.sl_price
        elif low <= pos.tp_price:
            reason, exit_price = "TP", pos.tp_price
        else:
            reason, exit_price = None, None

    pos.mfe_pct = max(pos.mfe_pct, gain)
    pos.mae_pct = min(pos.mae_pct, loss)
    hold_min = (ts_ms - pos.entry_ts_ms) / 60_000.0
    if reason is None and hold_min >= params.paper_max_hold_min:
        reason, exit_price = "TIME", close
    if reason is None or exit_price is None:
        return None

    if pos.side == "buy":
        gross = (exit_price - pos.entry_price) / pos.entry_price * 100.0
    else:
        gross = (pos.entry_price - exit_price) / pos.entry_price * 100.0
    net = gross - params.fee_rt_pct
    sl_dist_pct = abs(pos.entry_price - pos.sl_price) / pos.entry_price * 100.0
    return {
        "signal_id": f"bt-{pos.sym}-{pos.entry_ts_ms}",
        "sym": pos.sym,
        "exit_reason": reason,
        "entry_price": pos.entry_price,
        "exit_price": exit_price,
        "net_pnl_pct": net,
        "hold_min": hold_min,
        "mfe_pct": pos.mfe_pct,
        "mae_pct": pos.mae_pct,
        "mfe_r": pos.mfe_pct / sl_dist_pct if sl_dist_pct > 0 else float("nan"),
        "mae_r": pos.mae_pct / sl_dist_pct if sl_dist_pct > 0 else float("nan"),
        "section": pos.section,
        "direction": "PUMP" if pos.side == "buy" else "DUMP",
        "vol_ratio": pos.vol_ratio,
        "pct_move": pos.pct_move,
        "dollar_vol": pos.dollar_vol,
        "atr": pos.atr,
        "atr_pct": pos.atr / pos.entry_price * 100.0 if pos.entry_price else float("nan"),
        "entry_ts_ms": pos.entry_ts_ms,
        "closed_ts_ms": ts_ms,
        "hour_utc": hour_from_ms(pos.entry_ts_ms),
    }


def update_streaks(state: PairState, is_sl: bool, ts_ms: int, params: SimParams) -> None:
    if is_sl:
        state.sl_today += 1
        state.total_sl_streak += 1
        if state.tp_today == 0 and state.sl_today >= params.session_ban_sl_no_tp:
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            eod = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc) + timedelta(days=1)
            state.banned_until_ms = int(eod.timestamp() * 1000)
            state.section = "banned"
            return
        if state.total_sl_streak >= params.cb_cooldown_sl:
            state.cooldown_until_ms = ts_ms + params.cb_cooldown_min * 60_000
            state.total_sl_streak = 0
        if state.section == "main":
            state.main_sl_streak += 1
            if state.main_sl_streak >= params.main_sl_to_counter:
                state.section = "counter"
                state.counter_sl_streak = 0
        elif state.section == "counter":
            state.counter_sl_streak += 1
            if state.counter_sl_streak >= params.counter_sl_to_ban:
                state.banned_until_ms = ts_ms + 24 * 60 * 60_000
                state.section = "banned"
    else:
        state.tp_today += 1
        state.main_sl_streak = 0
        state.counter_sl_streak = 0
        state.total_sl_streak = 0


def simulate(
    market: dict[str, PreparedSymbol],
    events: list[tuple[int, str, int]],
    params: SimParams,
    entry_start: pd.Timestamp,
    entry_end: pd.Timestamp,
) -> list[dict]:
    states = {sym: PairState(main_direction="buy") for sym in market}
    labels: list[dict] = []
    entry_start_ms = int(entry_start.timestamp() * 1000)
    entry_end_ms = int(entry_end.timestamp() * 1000)
    cb_halted = False
    cb_daily_pnl = 0.0
    cb_halted_at = 0
    current_day = ""

    for ts_ms, sym, i in events:
        ps = market[sym]
        state = states[sym]
        day = day_from_ms(ts_ms)

        if day != current_day:
            current_day = day
            cb_halted = False
            cb_daily_pnl = 0.0
            cb_halted_at = 0
        if state.day != day:
            state.day = day
            state.sl_today = 0
            state.tp_today = 0
            if state.section == "banned" and ts_ms >= state.banned_until_ms:
                state.section = "main"
                state.banned_until_ms = 0

        if cb_halted and cb_halted_at and ts_ms - cb_halted_at >= params.cb_daily_halt_cooldown_min * 60_000:
            cb_halted = False
            cb_daily_pnl = 0.0
            cb_halted_at = 0

        if state.position is not None:
            closed = close_position_if_hit(state.position, ps, i, params)
            if closed:
                labels.append(closed)
                state.last_signal_ts_ms = ts_ms
                state.position = None
                cb_daily_pnl += float(closed["net_pnl_pct"])
                if cb_daily_pnl <= -params.cb_daily_loss_pct:
                    cb_halted = True
                    cb_halted_at = ts_ms
                update_streaks(state, closed["exit_reason"] == "SL", ts_ms, params)
                continue

        if not (entry_start_ms <= ts_ms <= entry_end_ms):
            continue
        if cb_halted or state.position is not None:
            continue
        if sum(1 for s in states.values() if s.position is not None) >= params.max_open_positions:
            continue
        if ts_ms < state.cooldown_until_ms or ts_ms < state.banned_until_ms or state.section == "banned":
            continue

        baseline_vol = float(ps.baseline_vol[i])
        atr = float(ps.atr[i])
        if i < 10 or not math.isfinite(baseline_vol) or baseline_vol <= 0 or not math.isfinite(atr) or atr <= 0:
            continue

        if params.path.upper() == "A" and state.pending_side:
            if ts_ms - state.pending_since_ms > 120_000:
                state.pending_side = ""
                continue
            close = float(ps.close[i])
            open_ = float(ps.open[i])
            if state.pending_side == "buy":
                direction_ok = close >= open_
                reversal_pct = (state.pending_signal_close - close) / state.pending_signal_close * 100.0
            else:
                direction_ok = close <= open_
                reversal_pct = (close - state.pending_signal_close) / state.pending_signal_close * 100.0
            reversal_ok = reversal_pct < params.confirmation_reversal_max_pct
            vol_ok = float(ps.vol[i]) >= baseline_vol * params.confirmation_vol_ratio
            side = state.pending_side
            pending_vol = state.pending_vol
            state.pending_side = ""
            if direction_ok and reversal_ok and vol_ok:
                state.last_signal_ts_ms = ts_ms
                state.position = open_position(ps, i, state, params, side, pending_vol, 0.0)
            continue

        if ts_ms - state.last_signal_ts_ms < params.alert_cooldown_sec * 1000:
            continue

        current_open = float(ps.open[i])
        current_close = float(ps.close[i])
        if current_open <= 0:
            continue
        vol_ratio = float(ps.vol[i]) / baseline_vol
        price_move = abs(current_close - current_open) / current_open * 100.0
        if vol_ratio < params.vol_mult or price_move < params.price_pct:
            continue
        if vol_ratio >= 2.0 and price_move < 0.5:
            continue
        if float(ps.dollar_vol[i]) < params.min_usd_vol:
            continue

        if state.section == "main" and ps.baseline_price > 0:
            phase_pct = abs(current_close - ps.baseline_price) / ps.baseline_price * 100.0
            if phase_pct > params.pump_phase_max_pct:
                continue

        if state.section == "main":
            side = "buy" if current_close > current_open else "sell"
            state.main_direction = side
        else:
            side = "sell" if state.main_direction == "buy" else "buy"

        if params.path.upper() == "A":
            state.pending_side = side
            state.pending_vol = vol_ratio
            state.pending_signal_close = current_close
            state.pending_since_ms = ts_ms
            continue

        state.last_signal_ts_ms = ts_ms
        state.position = open_position(ps, i, state, params, side, vol_ratio, price_move)

    return labels


def config_row(params: SimParams, labels: list[dict], group: str) -> dict:
    m = metric_summary(labels)
    row = {
        "group": group,
        "name": params.name,
        "vol_mult": params.vol_mult,
        "price_pct": params.price_pct,
        "min_usd_vol": params.min_usd_vol,
        "sl_atr": params.sl_atr_mult,
        "tp_atr": params.tp_atr_mult,
        "min_tp_pct": params.min_tp_pct,
        "phase": params.pump_phase_max_pct,
        "cooldown": params.alert_cooldown_sec,
        "confirm_rev": params.confirmation_reversal_max_pct,
        "confirm_vol": params.confirmation_vol_ratio,
        "path": params.path,
    }
    row.update(m)
    return row


def run_sweeps(
    market: dict[str, PreparedSymbol],
    events: list[tuple[int, str, int]],
    base: SimParams,
    entry_start: pd.Timestamp,
    entry_end: pd.Timestamp,
) -> list[dict]:
    results: list[dict] = []

    for vol_mult, price_pct, min_usd_vol in itertools.product([1.5, 2.0, 2.5, 3.0], [0.8, 1.2, 1.5, 2.0], [20_000, 50_000, 100_000]):
        p = replace(base, vol_mult=vol_mult, price_pct=price_pct, min_usd_vol=min_usd_vol, path="B", name="A")
        results.append(config_row(p, simulate(market, events, p, entry_start, entry_end), "A_detection"))

    for sl_atr, tp_atr, min_tp in itertools.product([1.0, 1.5, 2.0], [2.0, 2.5, 3.0, 4.0], [0.8, 1.0, 1.5]):
        p = replace(base, sl_atr_mult=sl_atr, tp_atr_mult=tp_atr, min_tp_pct=min_tp, path="B", name="B")
        results.append(config_row(p, simulate(market, events, p, entry_start, entry_end), "B_geometry"))

    for confirm_rev, confirm_vol in itertools.product([0.3, 0.5, 0.8, 1.2], [0.6, 0.8, 1.0]):
        p = replace(base, confirmation_reversal_max_pct=confirm_rev, confirmation_vol_ratio=confirm_vol, path="A", name="C")
        results.append(config_row(p, simulate(market, events, p, entry_start, entry_end), "C_confirmation"))

    for phase, cooldown in itertools.product([1.5, 2.0, 3.0, 5.0], [120, 300, 600]):
        p = replace(base, pump_phase_max_pct=phase, alert_cooldown_sec=cooldown, path="B", name="D")
        results.append(config_row(p, simulate(market, events, p, entry_start, entry_end), "D_phase"))

    return results


def validation_diagnostics(
    market: dict[str, PreparedSymbol],
    events: list[tuple[int, str, int]],
    base: SimParams,
    entry_start: pd.Timestamp,
    entry_end: pd.Timestamp,
) -> list[dict]:
    specs = [
        ("base_path_b", base),
        ("slots999", replace(base, max_open_positions=999)),
        ("phase999", replace(base, pump_phase_max_pct=999)),
        ("slots999_phase999", replace(base, max_open_positions=999, pump_phase_max_pct=999)),
        ("no_usd_slots999_phase999", replace(base, max_open_positions=999, pump_phase_max_pct=999, min_usd_vol=0)),
        ("loose_price", replace(base, max_open_positions=999, pump_phase_max_pct=999, price_pct=0.8, min_usd_vol=20_000)),
        ("path_a_approx", replace(base, path="A", max_open_positions=999, pump_phase_max_pct=999)),
    ]
    out = []
    for name, params in specs:
        labels = simulate(market, events, params, entry_start, entry_end)
        row = {"diagnostic": name}
        row.update(metric_summary(labels))
        out.append(row)
    return out


def hypothesis_results(rows: list[dict]) -> dict:
    buckets = {
        "vol_2_3": [r for r in rows if 2 <= safe_float(r.get("vol_ratio")) < 3],
        "vol_3_5": [r for r in rows if 3 <= safe_float(r.get("vol_ratio")) <= 5],
        "vol_gt_5": [r for r in rows if safe_float(r.get("vol_ratio")) > 5],
        "atr_lt_0p2": [r for r in rows if safe_float(r.get("atr_pct")) < 0.2],
        "atr_rest": [r for r in rows if safe_float(r.get("atr_pct")) >= 0.2],
        "main": [r for r in rows if str(r.get("section") or r.get("label_section") or "").lower() == "main"],
        "counter": [r for r in rows if str(r.get("section") or r.get("label_section") or "").lower() == "counter"],
    }
    hourly = []
    for hour in range(24):
        subset = [r for r in rows if r.get("hour_utc") == hour]
        m = metric_summary(subset, min_n=10)
        if not m["insufficient"]:
            hourly.append((hour, m))
    return {
        "buckets": {name: metric_summary(subset, min_n=10) for name, subset in buckets.items()},
        "hourly": hourly,
    }


def best_named_breakdown(breakdowns: list[tuple[str, dict]], prefix: str) -> tuple[str, dict] | None:
    candidates = [(name, m) for name, m in breakdowns if name.startswith(prefix) and not m["insufficient"] and math.isfinite(m["wr"])]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[1]["wr"])


def render_report(
    live_signals: list[dict],
    live_labels: list[dict],
    live_joined: list[dict],
    validation_labels: list[dict] | None,
    sweep_results: list[dict] | None,
    diagnostics: list[dict] | None,
    frames_loaded: int,
    symbols_requested: int,
) -> str:
    live_m = metric_summary(live_joined)
    bt_m = metric_summary(validation_labels or [])
    breakdowns = live_breakdowns(live_joined)
    hyp = hypothesis_results(live_joined)
    validation_ok = bool(validation_labels) and math.isfinite(bt_m["wr"]) and abs(bt_m["wr"] - live_m["wr"]) <= 10.0

    lines = ["=== PUMP BACKTEST REPORT ===", ""]
    if validation_labels is None:
        lines.append(f"VALIDATION: not run. Live WR={fmt_pct(live_m['wr'])}")
    else:
        lines.append(
            f"VALIDATION: backtest WR={fmt_pct(bt_m['wr'])} vs live WR={fmt_pct(live_m['wr'])} "
            f"[{'OK' if validation_ok else 'FAIL'}]"
        )
        lines += [
            "",
            "VALIDATION REPORT",
            "=================",
            "              Backtest    Live",
            f"Signals:       {len(validation_labels):>7}    {len(live_signals):>4}",
            f"WR (excl TIME): {fmt_pct(bt_m['wr']):>7}    {fmt_pct(live_m['wr']):>7}",
            f"Avg hold_min:  {fmt_num(bt_m['avg_hold_min'], 1):>7}    {fmt_num(live_m['avg_hold_min'], 1):>7}",
            f"Avg net_pnl:   {fmt_pct(bt_m['avg_net'], 2):>7}    {fmt_pct(live_m['avg_net'], 2):>7}",
            f"Avg mfe_r:     {fmt_num(bt_m['avg_mfe_r']):>7}    {fmt_num(live_m['avg_mfe_r']):>7}",
            f"Avg mae_r:     {fmt_num(bt_m['avg_mae_r']):>7}    {fmt_num(live_m['avg_mae_r']):>7}",
            f"Frames loaded: {frames_loaded}/{symbols_requested}",
        ]
        if not validation_ok:
            lines += [
                "",
                "Validation gap notes:",
                "- Live entries are dominated by Path A screener pending/confirmation, while validation uses standalone Path B.",
                "- Historical active_universe membership is not logged, so the backtest approximates the tradable pool from live symbols.",
                "- Early live labels lack MFE/MAE fields; those averages use available rows only.",
            ]
        if diagnostics:
            lines += ["", "VALIDATION SENSITIVITY:"]
            for row in diagnostics:
                lines.append(
                    f"- {row['diagnostic']}: n={row['n']} WR={fmt_pct(row['wr'])} "
                    f"PF={fmt_num(row['pf'])} Avg_net={fmt_pct(row['avg_net'], 2)}"
                )

    lines += ["", "TOP-5 CONFIGURATIONS:"]
    if not sweep_results:
        lines.append("No sweep results.")
    else:
        over_target = [row for row in sweep_results if row["n"] > 30 and math.isfinite(row["wr"]) and row["wr"] > 55.0]
        lines.append(f"WR>55% and n>30 candidates: {len(over_target)}")
        top = [
            row for row in sweep_results
            if row["n"] >= 30 and math.isfinite(row["wr"]) and math.isfinite(row["pf"])
        ]
        top.sort(key=lambda r: (r["pf"], r["wr"], r["avg_net"]), reverse=True)
        unique_top = []
        seen_keys = set()
        for row in top:
            key = (
                row["vol_mult"],
                row["price_pct"],
                row["sl_atr"],
                row["tp_atr"],
                row["min_tp_pct"],
                row["min_usd_vol"],
                row["phase"],
                row["cooldown"],
                row.get("path"),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique_top.append(row)
        lines += [
            "| # | vol_mult | price_pct | sl_atr | tp_atr | min_usd_vol | n  | WR  | PF  | Avg_net |",
            "|---|----------|-----------|--------|--------|-------------|----|-----|-----|---------|",
        ]
        for rank, row in enumerate(unique_top[:5], start=1):
            lines.append(
                f"| {rank} | {row['vol_mult']:.1f} | {row['price_pct']:.1f} | "
                f"{row['sl_atr']:.1f} | {row['tp_atr']:.1f} | {row['min_usd_vol']:.0f} | "
                f"{row['n']} | {fmt_pct(row['wr'])} | {fmt_num(row['pf'])} | {fmt_pct(row['avg_net'], 2)} |"
            )
        if not unique_top:
            lines.append("| - | insufficient sweep candidates with n>=30 | | | | | | | | |")

    best_vol = best_named_breakdown(breakdowns, "vol_ratio")
    best_dollar = best_named_breakdown(breakdowns, "dollar_vol")
    main_m = hyp["buckets"]["main"]
    counter_m = hyp["buckets"]["counter"]
    counter_text = (
        f"main WR={fmt_pct(main_m['wr'])} vs counter WR={fmt_pct(counter_m['wr'])}"
        if not counter_m["insufficient"] else f"main WR={fmt_pct(main_m['wr'])}; counter insufficient data"
    )

    lines += ["", "LIVE DATA ANALYSIS - KEY FINDINGS:"]
    lines.append(f"- Best vol bucket: {best_vol[0]} -> WR={fmt_pct(best_vol[1]['wr'])}" if best_vol else "- Best vol bucket: insufficient data")
    lines.append(f"- Best dollar_vol bucket: {best_dollar[0]} -> WR={fmt_pct(best_dollar[1]['wr'])}" if best_dollar else "- Best dollar_vol bucket: insufficient data")
    lines.append(f"- Counter mode: {counter_text}")
    lines += ["", "LIVE BREAKDOWN TABLE:"]
    lines.extend(render_breakdown_table(breakdowns))

    b = hyp["buckets"]
    late_base = b["vol_2_3"]
    late_high = b["vol_gt_5"]
    late_confirmed = (
        not late_base["insufficient"]
        and not late_high["insufficient"]
        and math.isfinite(late_high["avg_mfe_r"])
        and math.isfinite(late_base["avg_mfe_r"])
        and late_high["avg_mfe_r"] < late_base["avg_mfe_r"]
    )
    atr_confirmed = (
        not b["atr_lt_0p2"]["insufficient"]
        and not b["atr_rest"]["insufficient"]
        and b["atr_lt_0p2"]["wr"] < b["atr_rest"]["wr"]
    )
    hourly = hyp["hourly"]
    best_hours = [h for h, m in hourly if m["wr"] >= 60.0]
    dead_hours = [h for h, m in hourly if m["wr"] < 40.0]
    disable_counter = (not counter_m["insufficient"] and counter_m["wr"] < 30.0)

    lines += [
        "",
        "HYPOTHESIS RESULTS:",
        (
            f"- Late entry (vol>5x): mfe_r={fmt_num(late_high['avg_mfe_r'])} "
            f"vs baseline={fmt_num(late_base['avg_mfe_r'])} -> "
            f"[{'CONFIRMED' if late_confirmed else 'REJECTED/INSUFFICIENT'}]"
        ),
        (
            f"- ATR filter: atr<0.2% WR={fmt_pct(b['atr_lt_0p2']['wr'])} "
            f"vs rest WR={fmt_pct(b['atr_rest']['wr'])} -> "
            f"[{'CONFIRMED' if atr_confirmed else 'REJECTED/INSUFFICIENT'}]"
        ),
        (
            f"- Time of day: best hours UTC {best_hours or 'none'}; "
            f"dead hours {dead_hours or 'none'}"
        ),
        f"- Counter mode: [{'DISABLE' if disable_counter else 'KEEP/INSUFFICIENT'}] - {counter_text}",
    ]

    recs = []
    if best_vol:
        recs.append(f"Prefer {best_vol[0]} live bucket; it has WR={fmt_pct(best_vol[1]['wr'])}.")
    if best_dollar:
        recs.append(f"Use liquidity filter around {best_dollar[0]}; it is the strongest dollar_vol bucket.")
    if atr_confirmed:
        recs.append("Add min_atr_pct >= 0.2% guard.")
    if disable_counter:
        recs.append("Disable counter mode until a larger sample proves edge.")
    if validation_labels is not None and not validation_ok:
        recs.append("Do not deploy sweep winners directly; first log historical active_universe/pending events for Path A validation.")
    if not recs:
        recs.append("Keep current config; available samples do not justify a stronger filter.")

    lines += ["", "FINAL RECOMMENDATIONS (priority order):"]
    for idx, rec in enumerate(recs[:5], start=1):
        lines.append(f"{idx}. {rec}")
    lines.append("")
    return "\n".join(lines)


def print_live_analysis() -> None:
    signals, labels, joined = load_live_joined()
    live_m = metric_summary(joined)
    print(f"Live signals={len(signals)} labels={len(labels)} counted exits={live_m['n']} WR={fmt_pct(live_m['wr'])}")
    for line in render_breakdown_table(live_breakdowns(joined)):
        print(line)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["live", "validate", "sweep", "full", "report"], default="full")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--fetch", action="store_true", help="Fetch missing candles from OKX.")
    parser.add_argument("--workers", type=int, default=4, help="Parallel OKX fetch workers.")
    parser.add_argument("--max-symbols", type=int, default=0, help="Limit symbols for quick debugging.")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    PUMP_CACHE.mkdir(parents=True, exist_ok=True)

    signals, labels, joined = load_live_joined()
    if args.mode == "live":
        print_live_analysis()
        report = render_report(signals, labels, joined, None, None, None, frames_loaded=0, symbols_requested=0)
        REPORT_PATH.write_text(report, encoding="utf-8")
        print(f"wrote {REPORT_PATH}")
        return 0

    if not signals:
        raise SystemExit("no live pump signals found")
    signal_times = [parse_ts(row.get("ts_utc")) for row in signals]
    signal_times = [ts for ts in signal_times if ts is not None]
    label_close_times = [parse_ts(row.get("closed_at")) for row in labels]
    label_close_times = [ts for ts in label_close_times if ts is not None]
    entry_start = min(signal_times)
    entry_end = max(signal_times)
    close_end = max(label_close_times) if label_close_times else entry_end + pd.Timedelta(days=1)
    close_end = max(close_end, entry_end + pd.Timedelta(hours=6))

    symbols = load_symbol_universe(signals)
    if args.max_symbols > 0:
        symbols = symbols[: args.max_symbols]
    frames = ensure_candle_cache(
        symbols,
        args.days,
        fetch=args.fetch,
        end_ts=close_end + pd.Timedelta(minutes=5),
        required_start=entry_start - pd.Timedelta(minutes=20),
        required_end=close_end,
        workers=args.workers,
    )
    market, events = prepare_market(frames, entry_start, entry_end, close_end)
    base = load_config_defaults()

    validation_labels = simulate(market, events, replace(base, path="B", name="validation"), entry_start, entry_end)
    diagnostics = validation_diagnostics(market, events, base, entry_start, entry_end)
    sweep_results = None
    if args.mode in {"sweep", "full"}:
        sweep_results = run_sweeps(market, events, base, entry_start, entry_end)
        sweep_path = RESULTS_DIR / "pump_sweep_results.csv"
        pd.DataFrame(sweep_results).to_csv(sweep_path, index=False)
        print(f"wrote {sweep_path}")
    elif args.mode == "report":
        sweep_path = RESULTS_DIR / "pump_sweep_results.csv"
        if sweep_path.exists():
            sweep_results = pd.read_csv(sweep_path).to_dict("records")

    report = render_report(
        signals,
        labels,
        joined,
        validation_labels,
        sweep_results,
        diagnostics,
        frames_loaded=len(market),
        symbols_requested=len(symbols),
    )
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report)
    print(f"wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
