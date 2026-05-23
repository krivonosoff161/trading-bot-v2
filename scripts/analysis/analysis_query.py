"""Join signal snapshot, labels, feature rows, and tape rows into one DataFrame."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

SIGNAL_SNAPSHOT = ROOT / "logs" / "signals" / "signal_snapshot.jsonl"
MAIN_SIGNALS = ROOT / "logs" / "signals" / "main_signals.jsonl"
MAIN_LABELS = ROOT / "logs" / "signals" / "main_signals_labels.jsonl"
SIGNAL_LOG = ROOT / "logs" / "signals" / "signal_log.jsonl"
SIGNAL_LABELS = ROOT / "logs" / "signals" / "signal_labels.jsonl"
PUMP_SIGNALS = ROOT / "logs" / "pump" / "pump_signals.jsonl"
PUMP_LABELS = ROOT / "logs" / "pump" / "pump_labels.jsonl"
FEATURE_ROOT = ROOT / "logs" / "features"
TAPE_DIR = Path(os.getenv("TAPE_DATA_DIR") or (ROOT / "scripts" / "tape"))

FEATURE_TFS = ["5m", "15m", "1H", "4H"]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _ts_ms_from_iso(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _iso_from_ms(ts_ms: int | float | None) -> str | None:
    if ts_ms is None or pd.isna(ts_ms):
        return None
    return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _date_keys(ts_from_ms: int, ts_to_ms: int) -> list[str]:
    start = datetime.fromtimestamp(ts_from_ms / 1000, tz=timezone.utc).date()
    end = datetime.fromtimestamp(ts_to_ms / 1000, tz=timezone.utc).date()
    out = []
    cur = start
    while cur <= end:
        out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def _swap_symbol(symbol: str | None) -> str:
    if not symbol:
        return ""
    return symbol if symbol.endswith("-SWAP") else f"{symbol}-SWAP"


def _base_symbol(symbol: str | None) -> str:
    return (symbol or "").removesuffix("-SWAP")


def _norm_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "signal_id": row.get("signal_id"),
        "source": row.get("source"),
        "ts": row.get("ts"),
        "ts_ms": row.get("ts_ms") or _ts_ms_from_iso(row.get("ts")),
        "symbol": row.get("symbol"),
        "side": row.get("side"),
        "trade_style": row.get("trade_style"),
        "regime": row.get("regime"),
        "entry": row.get("entry"),
        "sl": row.get("sl"),
        "tp1": row.get("tp1"),
        "tp2": row.get("tp2"),
        "exit_rule": row.get("exit_rule"),
        "hold_min": row.get("hold_min"),
        "snapshot_json": row,
    }


def _norm_main(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "signal_id": row.get("id") or row.get("signal_id"),
        "source": row.get("source", "ws_main_screener"),
        "ts": row.get("ts"),
        "ts_ms": row.get("ts_ms") or _ts_ms_from_iso(row.get("ts")),
        "symbol": row.get("symbol"),
        "side": row.get("side"),
        "trade_style": row.get("trade_style"),
        "regime": row.get("regime"),
        "entry": row.get("entry"),
        "sl": row.get("sl"),
        "tp1": row.get("tp1"),
        "tp2": row.get("tp2"),
        "exit_rule": row.get("exit_rule"),
        "hold_min": row.get("hold_min"),
        "snapshot_json": None,
    }


def _norm_signal_log(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "signal_id": row.get("signal_id"),
        "source": row.get("source", "ws_scanner"),
        "ts": _iso_from_ms(row.get("ts_ms")),
        "ts_ms": row.get("ts_ms"),
        "symbol": row.get("symbol"),
        "side": row.get("side"),
        "trade_style": row.get("style") or row.get("trade_style"),
        "regime": row.get("regime"),
        "entry": row.get("close"),
        "sl": row.get("sl"),
        "tp1": row.get("tp"),
        "tp2": row.get("tp2"),
        "exit_rule": row.get("exit_rule"),
        "hold_min": row.get("max_hold_min"),
        "snapshot_json": None,
    }


def _norm_pump(row: dict[str, Any]) -> dict[str, Any]:
    direction = row.get("direction")
    side = "buy" if direction == "PUMP" else ("sell" if direction == "DUMP" else row.get("side"))
    return {
        "signal_id": row.get("signal_id"),
        "source": "pump",
        "ts": row.get("ts_utc"),
        "ts_ms": _ts_ms_from_iso(row.get("ts_utc")),
        "symbol": row.get("sym"),
        "side": side,
        "trade_style": "PUMP",
        "regime": row.get("section"),
        "entry": row.get("signal_close"),
        "sl": row.get("paper_sl"),
        "tp1": row.get("paper_tp"),
        "tp2": None,
        "exit_rule": row.get("exit_rule"),
        "hold_min": None,
        "snapshot_json": None,
    }


def load_signals() -> pd.DataFrame:
    merged: dict[str, dict[str, Any]] = {}
    for row in _load_jsonl(MAIN_SIGNALS):
        norm = _norm_main(row)
        if norm["signal_id"]:
            merged[norm["signal_id"]] = norm
    for row in _load_jsonl(SIGNAL_LOG):
        norm = _norm_signal_log(row)
        if norm["signal_id"]:
            merged.setdefault(norm["signal_id"], norm)
    for row in _load_jsonl(PUMP_SIGNALS):
        norm = _norm_pump(row)
        if norm["signal_id"]:
            merged.setdefault(norm["signal_id"], norm)
    for row in _load_jsonl(SIGNAL_SNAPSHOT):
        norm = _norm_snapshot(row)
        signal_id = norm["signal_id"]
        if not signal_id:
            continue
        base = merged.get(signal_id, {})
        base.update({k: v for k, v in norm.items() if v is not None})
        merged[signal_id] = base
    return pd.DataFrame(list(merged.values()))


def load_labels() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in _load_jsonl(MAIN_LABELS):
        rows.append({
            "signal_id": row.get("signal_id"),
            "outcome": row.get("outcome"),
            "exit_price": row.get("exit_price"),
            "elapsed_m": row.get("hold_min"),
            "label_json": row,
        })
    for row in _load_jsonl(SIGNAL_LABELS):
        rows.append({
            "signal_id": row.get("signal_id"),
            "outcome": row.get("outcome"),
            "exit_price": row.get("exit_price"),
            "elapsed_m": row.get("elapsed_m"),
            "exit_r": row.get("exit_r"),
            "mfe_r": row.get("mfe_r"),
            "mae_r": row.get("mae_r"),
            "label_json": row,
        })
    for row in _load_jsonl(PUMP_LABELS):
        closed_at = row.get("closed_at")
        rows.append({
            "signal_id": row.get("signal_id"),
            "outcome": row.get("exit_reason"),
            "exit_price": row.get("exit_price"),
            "elapsed_m": row.get("hold_min"),
            "exit_ts_ms": _ts_ms_from_iso(closed_at),
            "label_json": row,
        })
    return pd.DataFrame(rows)


def signal_table() -> pd.DataFrame:
    signals = load_signals()
    labels = load_labels()
    if signals.empty:
        return signals
    if labels.empty:
        signals["outcome"] = None
        return signals
    return signals.merge(labels, on="signal_id", how="left", suffixes=("", "_label"))


def _read_feature_files(symbol: str, ts_from_ms: int, ts_to_ms: int) -> pd.DataFrame:
    frames = []
    symbols = list(dict.fromkeys([symbol, _swap_symbol(symbol), _base_symbol(symbol)]))
    for tf in FEATURE_TFS:
        for sym in symbols:
            if not sym:
                continue
            for date_key in _date_keys(ts_from_ms, ts_to_ms):
                plain = FEATURE_ROOT / tf / sym / f"{date_key}.csv"
                gz = FEATURE_ROOT / tf / sym / f"{date_key}.csv.gz"
                path = plain if plain.exists() else gz
                if not path.exists():
                    continue
                df = pd.read_csv(path, compression="infer")
                if "ts_ms" not in df.columns:
                    continue
                df = df[(df["ts_ms"] >= ts_from_ms) & (df["ts_ms"] <= ts_to_ms)].copy()
                if not df.empty:
                    frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _tape_paths(symbol: str, date_key: str) -> list[Path]:
    swap = _swap_symbol(symbol)
    return [
        TAPE_DIR / swap / f"{date_key}.csv",
        TAPE_DIR / swap / f"{date_key}.csv.gz",
        TAPE_DIR / f"{date_key}.csv",
        TAPE_DIR / f"{date_key}.csv.gz",
    ]


def _read_tape(symbol: str, ts_from_ms: int, ts_to_ms: int) -> pd.DataFrame:
    frames = []
    swap = _swap_symbol(symbol)
    for date_key in _date_keys(ts_from_ms, ts_to_ms):
        for path in _tape_paths(symbol, date_key):
            if not path.exists():
                continue
            opener = gzip.open if path.suffix == ".gz" else open
            with opener(path, "rt", encoding="utf-8", newline="") as f:
                try:
                    df = pd.read_csv(f)
                except pd.errors.EmptyDataError:
                    continue
            if "symbol" in df.columns:
                df = df[df["symbol"].isin([swap, _base_symbol(swap), symbol])].copy()
            if "ts_ms" not in df.columns:
                continue
            df["ts_ms"] = pd.to_numeric(df["ts_ms"], errors="coerce")
            df = df[(df["ts_ms"] >= ts_from_ms) & (df["ts_ms"] <= ts_to_ms)].copy()
            if not df.empty:
                frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _feature_records(df: pd.DataFrame, signal: pd.Series, label: dict[str, Any]) -> list[dict[str, Any]]:
    if df.empty:
        return []
    rows = []
    entry_ts = int(signal["ts_ms"])
    for row in df.to_dict("records"):
        event_ts = int(row.get("ts_ms"))
        row.update({
            "signal_id": signal["signal_id"],
            "record_type": "feature",
            "source": signal.get("source"),
            "entry_ts_ms": entry_ts,
            "event_ts_ms": event_ts,
            "minutes_from_entry": round((event_ts - entry_ts) / 60_000, 3),
            "outcome": label.get("outcome"),
        })
        rows.append(row)
    return rows


def _tick_records(df: pd.DataFrame, signal: pd.Series, label: dict[str, Any], target_ts_ms: int) -> list[dict[str, Any]]:
    if df.empty:
        return []
    out = []
    entry_ts = int(signal["ts_ms"])
    side_mult = df["side"].map({"buy": 1, "sell": -1}).fillna(0)
    df = df.copy()
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["size"] = pd.to_numeric(df["size"], errors="coerce")
    df["signed_size"] = side_mult * df["size"]
    df["signed_usdt"] = df["signed_size"] * df["price"]
    for row in df.to_dict("records"):
        event_ts = int(row.get("ts_ms"))
        out.append({
            "signal_id": signal["signal_id"],
            "record_type": "tick_raw",
            "source": signal.get("source"),
            "symbol": row.get("symbol"),
            "side": signal.get("side"),
            "trade_style": signal.get("trade_style"),
            "regime": signal.get("regime"),
            "outcome": label.get("outcome"),
            "entry_ts_ms": entry_ts,
            "event_ts_ms": event_ts,
            "minutes_from_entry": round((event_ts - entry_ts) / 60_000, 3),
            "minutes_from_target": round((event_ts - target_ts_ms) / 60_000, 3),
            "tick_side": row.get("side"),
            "tick_price": row.get("price"),
            "tick_size": row.get("size"),
            "tick_signed_size": row.get("signed_size"),
            "tick_signed_usdt": row.get("signed_usdt"),
            "trade_id": row.get("trade_id"),
        })
    minute = (df.assign(minute_ts=(df["ts_ms"].astype("int64") // 60_000) * 60_000)
                .groupby("minute_ts", as_index=False)
                .agg(
                    tick_cvd_1m=("signed_size", "sum"),
                    tick_signed_usdt_1m=("signed_usdt", "sum"),
                    tick_trade_count_1m=("ts_ms", "count"),
                    tick_buy_count_1m=("side", lambda s: int((s == "buy").sum())),
                ))
    minute["tick_buy_ratio_1m"] = minute["tick_buy_count_1m"] / minute["tick_trade_count_1m"]
    minute["tick_cvd_5m"] = minute["tick_cvd_1m"].rolling(5, min_periods=1).sum()
    for row in minute.to_dict("records"):
        event_ts = int(row["minute_ts"])
        out.append({
            "signal_id": signal["signal_id"],
            "record_type": "tick_1m",
            "source": signal.get("source"),
            "symbol": _swap_symbol(signal.get("symbol")),
            "side": signal.get("side"),
            "trade_style": signal.get("trade_style"),
            "regime": signal.get("regime"),
            "outcome": label.get("outcome"),
            "entry_ts_ms": entry_ts,
            "event_ts_ms": event_ts,
            "minutes_from_entry": round((event_ts - entry_ts) / 60_000, 3),
            "minutes_from_target": round((event_ts - target_ts_ms) / 60_000, 3),
            **{k: v for k, v in row.items() if k != "minute_ts"},
        })
    return out


def build_dataframe(signal_rows: pd.DataFrame, *, around: str = "entry", features_window_min: int = 30, ticks_window_min: int = 5) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for _, signal in signal_rows.iterrows():
        if pd.isna(signal.get("ts_ms")):
            continue
        entry_ts = int(signal["ts_ms"])
        elapsed_m = signal.get("elapsed_m")
        exit_ts = signal.get("exit_ts_ms")
        if pd.isna(exit_ts) and elapsed_m is not None and not pd.isna(elapsed_m):
            exit_ts = entry_ts + int(float(elapsed_m) * 60_000)
        target_ts = int(exit_ts) if around == "exit" and exit_ts is not None and not pd.isna(exit_ts) else entry_ts
        label = {
            "outcome": None if pd.isna(signal.get("outcome")) else signal.get("outcome"),
            "label_json": signal.get("label_json"),
        }

        records.append({
            "signal_id": signal["signal_id"],
            "record_type": "snapshot",
            "source": signal.get("source"),
            "symbol": signal.get("symbol"),
            "side": signal.get("side"),
            "trade_style": signal.get("trade_style"),
            "regime": signal.get("regime"),
            "outcome": label["outcome"],
            "entry_ts_ms": entry_ts,
            "event_ts_ms": entry_ts,
            "entry": signal.get("entry"),
            "sl": signal.get("sl"),
            "tp1": signal.get("tp1"),
            "tp2": signal.get("tp2"),
            "exit_rule": signal.get("exit_rule"),
            "hold_min": signal.get("hold_min"),
            "snapshot_json": signal.get("snapshot_json"),
            "label_json": signal.get("label_json"),
        })

        f_from = entry_ts - features_window_min * 60_000
        f_to = entry_ts + features_window_min * 60_000
        feature_df = _read_feature_files(str(signal.get("symbol")), f_from, f_to)
        records.extend(_feature_records(feature_df, signal, label))

        t_from = target_ts - ticks_window_min * 60_000
        t_to = target_ts + ticks_window_min * 60_000
        tape_df = _read_tape(str(signal.get("symbol")), t_from, t_to)
        records.extend(_tick_records(tape_df, signal, label, target_ts))
    return pd.DataFrame(records)


def select_signals(args: argparse.Namespace) -> pd.DataFrame:
    table = signal_table()
    if table.empty:
        return table
    if args.signal_id:
        return table[table["signal_id"] == args.signal_id].copy()
    if args.where:
        return table.query(args.where, engine="python").copy()
    return table.tail(1).copy()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--signal-id")
    group.add_argument("--where", help="pandas query over joined signal/label table")
    parser.add_argument("--around", choices=["entry", "exit"], default="entry")
    parser.add_argument("--features-window-min", type=int, default=30)
    parser.add_argument("--ticks-window-min", type=int, default=5)
    parser.add_argument("--out", choices=["table", "csv", "json"], default="table")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    selected = select_signals(args)
    if selected.empty:
        print("No matching signals.")
        return 1
    df = build_dataframe(
        selected,
        around=args.around,
        features_window_min=args.features_window_min,
        ticks_window_min=args.ticks_window_min,
    )
    if args.out == "csv":
        print(df.to_csv(index=False))
    elif args.out == "json":
        print(df.to_json(orient="records", force_ascii=False))
    else:
        with pd.option_context("display.max_columns", 120, "display.max_rows", 200, "display.width", 220):
            print(df)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
