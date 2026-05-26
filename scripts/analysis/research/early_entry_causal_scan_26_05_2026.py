# -*- coding: utf-8 -*-
"""
early_entry_causal_scan_26_05_2026.py

Read-only research helper for the 2026-05-26 early-entry task.
It compares the late FIRE bar with the diagnostic IDEAL bar, but every
feature printed for a bar uses only data available on or before that bar.
IDEAL is used only as a label/diagnostic target, not as a tradable trigger.
"""
import csv
import gzip
import io
import json
import math
import os
import sys
from datetime import datetime
from statistics import mean, median, pstdev

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
FEATURES = os.path.join(ROOT, "logs", "features")
SNAP = os.path.join(ROOT, "logs", "signals", "signal_snapshot.jsonl")
TICKS = r"E:\trading-data\ticks"
FEATURE_CACHE = {}
TICK_CACHE = {}
INCLUDE_TAPE = os.getenv("EARLY_ENTRY_INCLUDE_TAPE", "0") == "1"


def fnum(value, default=None):
    try:
        if value in ("", None):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def load_rows(tf, symbol, ts_ms):
    rows = []
    base = os.path.join(FEATURES, tf, symbol)
    if not os.path.isdir(base):
        return rows
    for off in (-1, 0):
        day = datetime.utcfromtimestamp(ts_ms / 1000 + off * 86400).strftime("%Y-%m-%d")
        cache_key = (tf, symbol, day)
        if cache_key in FEATURE_CACHE:
            rows.extend(FEATURE_CACHE[cache_key])
            continue
        day_rows = []
        for ext, opener in ((".csv.gz", gzip.open), (".csv", open)):
            path = os.path.join(base, day + ext)
            if not os.path.exists(path):
                continue
            with opener(path, "rt", encoding="utf-8", errors="ignore", newline="") as fh:
                day_rows = list(csv.DictReader(fh))
            break
        FEATURE_CACHE[cache_key] = day_rows
        rows.extend(day_rows)
    dedup = {}
    for row in rows:
        dedup[int(row["ts_ms"])] = row
    return [dedup[k] for k in sorted(dedup)]


def bar_index(rows, ts_ms):
    if not rows:
        return None
    best = min(range(len(rows)), key=lambda i: abs(int(rows[i]["ts_ms"]) - ts_ms))
    return best


def slope_deg(values):
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return None
    n = len(values)
    xs = list(range(n))
    xm = mean(xs)
    ym = mean(values)
    den = sum((x - xm) ** 2 for x in xs)
    if den <= 0 or ym == 0:
        return None
    beta = sum((x - xm) * (y - ym) for x, y in zip(xs, values)) / den
    return math.degrees(math.atan(beta / abs(ym) * 100.0))


def rel(price_a, price_b):
    if not price_a or not price_b:
        return None
    return (price_a / price_b - 1.0) * 100.0


def feature_at(rows, idx, side):
    if idx is None or idx < 0 or idx >= len(rows):
        return {}
    sgn = 1 if side in ("buy", "long") else -1
    row = rows[idx]
    prev = rows[idx - 1] if idx > 0 else row
    close = fnum(row.get("close"))
    open_ = fnum(row.get("open"))
    high = fnum(row.get("high"))
    low = fnum(row.get("low"))
    prev_close = fnum(prev.get("close"))
    ema = fnum(row.get("ema20"))
    rsi = fnum(row.get("rsi"), 50.0)
    vol = fnum(row.get("volume_usdt"), fnum(row.get("volume_contracts"), 0.0))
    prior_vols = [
        fnum(r.get("volume_usdt"), fnum(r.get("volume_contracts"), 0.0))
        for r in rows[max(0, idx - 15):idx]
    ]
    prior_ranges = []
    for r in rows[max(0, idx - 12):idx]:
        h = fnum(r.get("high"))
        l = fnum(r.get("low"))
        c = fnum(r.get("close"))
        if h and l and c:
            prior_ranges.append((h - l) / c * 100)
    closes = [fnum(r.get("close")) for r in rows[max(0, idx - 5):idx + 1]]
    prev_closes = [fnum(r.get("close")) for r in rows[max(0, idx - 6):idx]]
    rng = high - low if high is not None and low is not None else None
    body = close - open_ if close is not None and open_ is not None else None
    close_prev = close - prev_close if close is not None and prev_close is not None else None
    lower_wick = min(open_, close) - low if None not in (open_, close, low) else None
    upper_wick = high - max(open_, close) if None not in (open_, close, high) else None
    rejection = None
    if rng and rng > 0 and lower_wick is not None and upper_wick is not None:
        wick = lower_wick if sgn > 0 else upper_wick
        rejection = wick / rng
    cur_range = (high - low) / close * 100 if None not in (high, low, close) and close else None
    avg_range = mean(prior_ranges) if prior_ranges else None
    return {
        "pullback_ema_pct": -sgn * rel(close, ema) if close and ema else None,
        "extension_ema_pct": sgn * rel(close, ema) if close and ema else None,
        "rsi_exhaust": (50.0 - rsi) if sgn > 0 else (rsi - 50.0),
        "body_dir_pct": sgn * body / open_ * 100 if body is not None and open_ else None,
        "close_prev_dir_pct": sgn * close_prev / prev_close * 100 if close_prev is not None and prev_close else None,
        "slope6_dir": sgn * slope_deg(closes) if len(closes) >= 3 else None,
        "slope6_delta_dir": sgn * ((slope_deg(closes) or 0.0) - (slope_deg(prev_closes) or 0.0)) if len(prev_closes) >= 3 else None,
        "vol_ratio_5m": vol / mean(prior_vols) if vol is not None and prior_vols and mean(prior_vols) > 0 else None,
        "range_ratio": cur_range / avg_range if cur_range is not None and avg_range and avg_range > 0 else None,
        "wick_reject": rejection,
        "day_position": fnum(row.get("day_position")),
    }


def load_ticks(symbol, start_ms, end_ms):
    if not os.path.isdir(TICKS):
        return []
    out = []
    for day_ms in sorted({start_ms, end_ms}):
        day = datetime.utcfromtimestamp(day_ms / 1000).strftime("%Y-%m-%d")
        path = os.path.join(TICKS, symbol, day + ".csv.gz")
        if not os.path.exists(path):
            continue
        cache_key = (symbol, day)
        if cache_key not in TICK_CACHE:
            with gzip.open(path, "rt", encoding="utf-8", errors="ignore", newline="") as fh:
                TICK_CACHE[cache_key] = list(csv.DictReader(fh))
        for row in TICK_CACHE[cache_key]:
            ts = int(row["ts_ms"])
            if start_ms <= ts <= end_ms:
                out.append(row)
    seen = set()
    dedup = []
    for row in sorted(out, key=lambda r: (int(r["ts_ms"]), r.get("trade_id", ""))):
        key = (row["ts_ms"], row.get("trade_id"))
        if key not in seen:
            seen.add(key)
            dedup.append(row)
    return dedup


def tape_features(symbol, bar_close_ms, side):
    if not INCLUDE_TAPE:
        return {}
    ticks = load_ticks(symbol, bar_close_ms - 5 * 60_000, bar_close_ms)
    if not ticks:
        return {}
    sgn = 1 if side in ("buy", "long") else -1
    buy = sell = total = 0.0
    first = fnum(ticks[0].get("price"))
    last = fnum(ticks[-1].get("price"))
    for row in ticks:
        size = fnum(row.get("size"), 0.0)
        total += size
        if row.get("side") == "buy":
            buy += size
        elif row.get("side") == "sell":
            sell += size
    signed = buy - sell
    progress = sgn * rel(last, first) if first and last else None
    cvd_dir = sgn * signed / total if total > 0 else None
    absorption = None
    if progress is not None and cvd_dir is not None:
        absorption = cvd_dir - progress / 0.25
    return {
        "tape_n": len(ticks),
        "tape_cvd_dir": cvd_dir,
        "tape_progress_pct": progress,
        "tape_absorption": absorption,
    }


def future_fav(rows, idx, hold_bars, side):
    sgn = 1 if side in ("buy", "long") else -1
    close = fnum(rows[idx].get("close"))
    if not close:
        return 0.0
    best = 0.0
    for j in range(idx + 1, min(len(rows), idx + hold_bars + 1)):
        ext = fnum(rows[j].get("high")) if sgn > 0 else fnum(rows[j].get("low"))
        if ext is not None:
            best = max(best, sgn * (ext - close) / close * 100.0)
    return best


def summarize(name, rows, keys):
    print(f"\n{name} (n={len(rows)})")
    print(f"{'feature':22} {'mean':>9} {'median':>9} {'n':>5}")
    for key in keys:
        vals = [r[key] for r in rows if r.get(key) is not None and math.isfinite(r[key])]
        if not vals:
            continue
        print(f"{key:22} {mean(vals):9.3f} {median(vals):9.3f} {len(vals):5}")


def corr(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None and math.isfinite(x) and math.isfinite(y)]
    if len(pairs) < 5:
        return None
    xvals = [p[0] for p in pairs]
    yvals = [p[1] for p in pairs]
    sx = pstdev(xvals)
    sy = pstdev(yvals)
    if sx == 0 or sy == 0:
        return None
    return sum((x - mean(xvals)) * (y - mean(yvals)) for x, y in pairs) / len(pairs) / sx / sy


def main():
    snaps = []
    for line in open(SNAP, "r", encoding="utf-8"):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("entry_signal") == "ENTRY":
            snaps.append(row)
    fire_rows = []
    ideal_rows = []
    early_candidates = []
    deltas = []
    no_data = 0
    for sig in snaps:
        symbol = sig["symbol"]
        side = sig["side"]
        ts_ms = int(sig["ts_ms"])
        rows = load_rows("5m", symbol, ts_ms)
        if len(rows) < 30:
            no_data += 1
            continue
        fire = bar_index(rows, ts_ms)
        if fire is None:
            no_data += 1
            continue
        hold = max(6, int(sig.get("hold_min") or 75) // 5)
        lo = max(0, fire - 24)
        hi = min(len(rows) - 1, fire + hold)
        if hi <= lo:
            continue
        ideal = max(range(lo, hi + 1), key=lambda i: future_fav(rows, i, hold, side))
        fire_feat = feature_at(rows, fire, side)
        ideal_feat = feature_at(rows, ideal, side)
        fire_feat.update(tape_features(symbol, int(rows[fire]["ts_ms"]), side))
        ideal_feat.update(tape_features(symbol, int(rows[ideal]["ts_ms"]), side))
        fire_feat.update({"kind": "FIRE", "symbol": symbol, "regime": sig.get("regime"), "style": sig.get("trade_style")})
        ideal_feat.update({"kind": "IDEAL", "symbol": symbol, "regime": sig.get("regime"), "style": sig.get("trade_style")})
        fire_feat["fav"] = future_fav(rows, fire, hold, side)
        ideal_feat["fav"] = future_fav(rows, ideal, hold, side)
        fire_rows.append(fire_feat)
        ideal_rows.append(ideal_feat)
        deltas.append((ideal - fire) * 5)

        for idx in range(max(0, fire - 24), fire + 1):
            feat = feature_at(rows, idx, side)
            if not feat:
                continue
            feat["fav"] = future_fav(rows, idx, hold, side)
            feat["is_early"] = idx < fire
            feat["offset_min"] = (idx - fire) * 5
            early_candidates.append(feat)

    keys = [
        "pullback_ema_pct", "extension_ema_pct", "rsi_exhaust", "body_dir_pct",
        "close_prev_dir_pct", "slope6_dir", "slope6_delta_dir", "vol_ratio_5m",
        "range_ratio", "wick_reject", "day_position", "tape_cvd_dir",
        "tape_progress_pct", "tape_absorption",
    ]
    print(f"Signals with 5m data: {len(fire_rows)} / {len(snaps)} (missing={no_data})")
    print(f"IDEAL offset: mean={mean(deltas):+.1f} min, median={median(deltas):+.1f} min")
    summarize("FIRE bars", fire_rows, keys)
    summarize("IDEAL diagnostic bars", ideal_rows, keys)

    print("\nIDEAL - FIRE mean delta")
    print(f"{'feature':22} {'delta':>9}")
    for key in keys:
        fvals = [r[key] for r in fire_rows if r.get(key) is not None and math.isfinite(r[key])]
        ivals = [r[key] for r in ideal_rows if r.get(key) is not None and math.isfinite(r[key])]
        if fvals and ivals:
            print(f"{key:22} {mean(ivals) - mean(fvals):+9.3f}")

    print("\nCorrelation with future favourable move on pre-FIRE candidate bars")
    print(f"{'feature':22} {'r':>8}")
    fav = [r["fav"] for r in early_candidates]
    ranked = []
    for key in keys:
        r = corr([row.get(key) for row in early_candidates], fav)
        if r is not None:
            ranked.append((abs(r), r, key))
    for _, r, key in sorted(ranked, reverse=True):
        print(f"{key:22} {r:+8.3f}")

    # Deterministic trigger candidate: exhaustion/retest first, confirmation second.
    triggered = []
    for row in early_candidates:
        pullback = row.get("pullback_ema_pct")
        rsi = row.get("rsi_exhaust")
        vol = row.get("vol_ratio_5m")
        rng = row.get("range_ratio")
        rev = row.get("body_dir_pct")
        prev = row.get("close_prev_dir_pct")
        if None in (pullback, rsi, vol, rng, rev, prev):
            continue
        if (
            0.15 <= pullback <= 2.50
            and rsi >= 2.0
            and 0.45 <= vol <= 2.20
            and rng <= 1.35
            and (rev > 0 or prev > 0)
        ):
            triggered.append(row)
    fire_favs = [r["fav"] for r in fire_rows]
    trig_favs = [r["fav"] for r in triggered]
    trig_offsets = [r["offset_min"] for r in triggered]
    print("\nMinimal causal trigger draft, candle-only")
    print("rule: 0.15<=pullback_ema<=2.50 AND rsi_exhaust>=2 AND 0.45<=vol_ratio<=2.20")
    print("      AND range_ratio<=1.35 AND reversal body/close in signal direction")
    print(f"triggers={len(triggered)} pre-fire bars from {len(early_candidates)} candidates")
    if trig_favs:
        print(f"trigger fav mean={mean(trig_favs):+.3f}% median={median(trig_favs):+.3f}%")
        print(f"trigger offset mean={mean(trig_offsets):+.1f} min median={median(trig_offsets):+.1f} min")
    if fire_favs:
        print(f"fire fav mean={mean(fire_favs):+.3f}% median={median(fire_favs):+.3f}%")


if __name__ == "__main__":
    main()
