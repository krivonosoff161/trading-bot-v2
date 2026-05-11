"""Per-candle feature log writer."""

from __future__ import annotations

import csv
import gzip
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
FEATURE_ROOT = ROOT / "logs" / "features"
INDEX_PATH = FEATURE_ROOT / "_index.jsonl"

TF_MS = {
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1H": 60 * 60_000,
    "4H": 4 * 60 * 60_000,
}

FIELDNAMES = [
    "schema_version",
    "ts_ms",
    "ts",
    "candle_open_ts_ms",
    "candle_close_ts_ms",
    "symbol",
    "base_symbol",
    "tf",
    "open",
    "high",
    "low",
    "close",
    "volume_contracts",
    "volume_usdt",
    "ema20",
    "ema50",
    "ema20_gt_ema50",
    "adx",
    "plus_di",
    "minus_di",
    "di_spread",
    "adx_rising",
    "atr",
    "atr_pct",
    "atr_label",
    "rsi",
    "slope",
    "slope_prev",
    "bb_upper",
    "bb_middle",
    "bb_lower",
    "bb_pct_b",
    "bb_width_pct",
    "bb_expanding",
    "supertrend",
    "supertrend_dir",
    "supertrend_dist",
    "ce_long",
    "ce_short",
    "vwap_day",
    "day_high",
    "day_low",
    "day_position",
    "daily_range_pct",
    "vol_ratio_sig",
    "regime",
    "bias_1h",
    "bias_4h",
    "funding_rate",
    "open_interest",
    "oi_delta",
    "perp_div_4bar",
    "swing_high_1",
    "swing_high_2",
    "swing_high_3",
    "swing_high_4",
    "swing_low_1",
    "swing_low_2",
    "swing_low_3",
    "swing_low_4",
]


def _get(result: Any, name: str, default: Any = None) -> Any:
    if isinstance(result, Mapping):
        return result.get(name, default)
    return getattr(result, name, default)


def _tf_key(tf: str) -> str:
    return {"1H": "1h", "4H": "4h"}.get(tf, tf)


def _base_symbol(symbol: str) -> str:
    return symbol.removesuffix("-SWAP")


def _iso_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _feature_path(tf: str, symbol: str, date_utc: str) -> Path:
    return FEATURE_ROOT / tf / symbol / f"{date_utc}.csv"


def _append_index(path: Path, *, tf: str, symbol: str, date_utc: str, rows: int, start_ts_ms: int | None, end_ts_ms: int | None) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "date": date_utc,
        "symbol": symbol,
        "tf": tf,
        "path": str(path.relative_to(ROOT)),
        "start_ts_ms": start_ts_ms,
        "end_ts_ms": end_ts_ms,
        "rows": rows,
        "indexed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with INDEX_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _csv_stats(path: Path) -> tuple[int, int | None, int | None]:
    rows = 0
    start_ts_ms = None
    end_ts_ms = None
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows += 1
            try:
                ts_ms = int(row["ts_ms"])
            except (KeyError, TypeError, ValueError):
                continue
            start_ts_ms = ts_ms if start_ts_ms is None else min(start_ts_ms, ts_ms)
            end_ts_ms = ts_ms if end_ts_ms is None else max(end_ts_ms, ts_ms)
    return rows, start_ts_ms, end_ts_ms


def _rotate_old_files(tf: str, symbol: str, current_date: str) -> None:
    pair_dir = FEATURE_ROOT / tf / symbol
    if not pair_dir.exists():
        return
    for csv_path in pair_dir.glob("*.csv"):
        date_utc = csv_path.stem
        if date_utc >= current_date:
            continue
        gz_path = csv_path.with_suffix(".csv.gz")
        if gz_path.exists():
            csv_path.unlink()
            continue
        rows, start_ts_ms, end_ts_ms = _csv_stats(csv_path)
        with csv_path.open("rb") as f_in, gzip.open(gz_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        csv_path.unlink()
        _append_index(gz_path, tf=tf, symbol=symbol, date_utc=date_utc, rows=rows, start_ts_ms=start_ts_ms, end_ts_ms=end_ts_ms)


def _pick(values: list[Any], index: int) -> Any:
    return values[index] if index < len(values) else None


def write_feature_row(result: Any, *, symbol: str, tf: str, candle: tuple) -> None:
    """Append one flat per-candle feature row."""
    tf_minutes_ms = TF_MS[tf]
    open_ts_ms = int(candle[0])
    close_ts_ms = open_ts_ms + tf_minutes_ms
    date_utc = datetime.fromtimestamp(close_ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

    _rotate_old_files(tf, symbol, date_utc)
    path = _feature_path(tf, symbol, date_utc)
    path.parent.mkdir(parents=True, exist_ok=True)

    indicators = _get(result, "indicators", {}) or {}
    context = _get(result, "context", {}) or {}
    engine_vars = _get(result, "engine_vars", {}) or {}
    data = indicators.get(_tf_key(tf), {}) or {}
    highs = data.get("swing_highs") or []
    lows = data.get("swing_lows") or []

    ema20 = data.get("ema20")
    ema50 = data.get("ema50")
    row = {
        "schema_version": 1,
        "ts_ms": close_ts_ms,
        "ts": _iso_ms(close_ts_ms),
        "candle_open_ts_ms": open_ts_ms,
        "candle_close_ts_ms": close_ts_ms,
        "symbol": symbol,
        "base_symbol": _base_symbol(symbol),
        "tf": tf,
        "open": candle[1],
        "high": candle[2],
        "low": candle[3],
        "close": candle[4],
        "volume_contracts": candle[5],
        "volume_usdt": candle[6],
        "ema20": ema20,
        "ema50": ema50,
        "ema20_gt_ema50": bool(ema20 is not None and ema50 is not None and float(ema20) > float(ema50)),
        "adx": data.get("adx"),
        "plus_di": data.get("plus_di"),
        "minus_di": data.get("minus_di"),
        "di_spread": engine_vars.get(f"di_spread_{_tf_key(tf)}"),
        "adx_rising": engine_vars.get(f"adx_{_tf_key(tf)}_rising"),
        "atr": data.get("atr"),
        "atr_pct": data.get("atr_pct"),
        "atr_label": data.get("atr_label"),
        "rsi": data.get("rsi"),
        "slope": context.get(f"slope_{_tf_key(tf)}"),
        "slope_prev": None,
        "bb_upper": data.get("bb_upper"),
        "bb_middle": data.get("bb_middle"),
        "bb_lower": data.get("bb_lower"),
        "bb_pct_b": data.get("bb_pct_b"),
        "bb_width_pct": data.get("bb_width_pct") if "bb_width_pct" in data else data.get("bb_width"),
        "bb_expanding": engine_vars.get("bb_expanding"),
        "supertrend": data.get("supertrend"),
        "supertrend_dir": data.get("supertrend_dir"),
        "supertrend_dist": data.get("supertrend_dist"),
        "ce_long": data.get("ce_long"),
        "ce_short": data.get("ce_short"),
        "vwap_day": engine_vars.get("vwap"),
        "day_high": engine_vars.get("day_high"),
        "day_low": engine_vars.get("day_low"),
        "day_position": engine_vars.get("day_position"),
        "daily_range_pct": engine_vars.get("daily_range_pct"),
        "vol_ratio_sig": engine_vars.get("vol_ratio_sig"),
        "regime": engine_vars.get("regime") or _get(result, "regime"),
        "bias_1h": engine_vars.get("bias_1h"),
        "bias_4h": engine_vars.get("bias_4h"),
        "funding_rate": engine_vars.get("funding_val"),
        "open_interest": context.get("oi"),
        "oi_delta": engine_vars.get("oi_delta"),
        "perp_div_4bar": engine_vars.get("perp_div_4bar"),
        "swing_high_1": _pick(highs, 0),
        "swing_high_2": _pick(highs, 1),
        "swing_high_3": _pick(highs, 2),
        "swing_high_4": _pick(highs, 3),
        "swing_low_1": _pick(lows, 0),
        "swing_low_2": _pick(lows, 1),
        "swing_low_3": _pick(lows, 2),
        "swing_low_4": _pick(lows, 3),
    }

    write_header = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(row)
