"""
verify_signals_from_tape.py

For each signal in signal_log.jsonl, replay actual OKX trades from tape/*.csv[.gz]
and compute:
  - real first touch of TP / SL (timestamp, elapsed minutes)
  - real MFE / MAE at multiple horizons (150m, 300m, 480m, 720m, 1440m)
  - outcome under hypotheses:
      original : exit at first TP or SL touch within max_hold_min (or TIME_EXIT)
      be05     : move SL to breakeven after MFE >= 0.5R
      be10     : move SL to breakeven after MFE >= 1.0R
      hold_480 : same as original but hold = 480m
      hold_720 : same as original but hold = 720m
      hold_1440: same as original but hold = 1440m (24h)

Compares against labels from signal_labels.jsonl and prints aggregate tables.
"""
import csv
import gzip
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

ROOT        = Path(__file__).parent
SIGNAL_LOG  = ROOT / "signal_log.jsonl"
LABEL_LOG   = ROOT / "signal_labels.jsonl"
TAPE_DIR    = ROOT / "tape"

HORIZONS_M  = [150, 300, 480, 720, 1440]


def _load_signals():
    sigs = []
    with open(SIGNAL_LOG, encoding="utf-8") as f:
        for line in f:
            sigs.append(json.loads(line))
    return sigs


def _load_labels():
    labs = {}
    with open(LABEL_LOG, encoding="utf-8") as f:
        for line in f:
            l = json.loads(line)
            labs[l["signal_id"]] = l
    return labs


def _tape_path_for(date_str: str):
    csv_p = TAPE_DIR / f"{date_str}.csv"
    gz_p  = TAPE_DIR / f"{date_str}.csv.gz"
    if csv_p.exists():
        return csv_p, False
    if gz_p.exists():
        return gz_p, True
    return None, None


def _iter_tape_day(date_str, symbols_wanted, ts_from_ms, ts_to_ms):
    """Yield (ts_ms, symbol_base, side, price) for wanted symbols in [ts_from, ts_to]."""
    path, is_gz = _tape_path_for(date_str)
    if path is None:
        return
    opener = (lambda p: gzip.open(p, "rt", encoding="utf-8")) if is_gz else \
             (lambda p: open(p, encoding="utf-8"))
    with opener(path) as f:
        r = csv.reader(f)
        header = next(r, None)
        for row in r:
            if len(row) < 4: continue
            try:
                ts = int(row[0])
            except ValueError:
                continue
            if ts < ts_from_ms: continue
            if ts > ts_to_ms: continue
            sym_swap = row[1]              # e.g. BTC-USDT-SWAP
            if sym_swap not in symbols_wanted: continue
            try:
                price = float(row[3])
            except ValueError:
                continue
            # base symbol without -SWAP
            base = sym_swap[:-5] if sym_swap.endswith("-SWAP") else sym_swap
            yield ts, base, row[2], price


def _date_keys_for_range(ts_from_ms, ts_to_ms):
    """All YYYY-MM-DD keys overlapping [ts_from, ts_to]."""
    d_start = datetime.fromtimestamp(ts_from_ms/1000, tz=timezone.utc).date()
    d_end   = datetime.fromtimestamp(ts_to_ms/1000,   tz=timezone.utc).date()
    out = []
    cur = d_start
    from datetime import timedelta
    while cur <= d_end:
        out.append(cur.strftime("%Y-%m-%d"))
        cur = cur + timedelta(days=1)
    return out


def _simulate_signal(sig, max_horizon_m=1440):
    """Return dict with per-tick simulation results for all hypotheses."""
    symbol   = sig["symbol"]
    side     = sig["side"]                 # buy/sell
    entry    = float(sig["close"])
    sl       = float(sig["sl"])
    tp       = float(sig["tp"])
    ts_entry = int(sig["ts_ms"])
    hold_m   = int(sig.get("max_hold_min") or 300)

    sl_dist  = abs(entry - sl)
    tp_dist  = abs(tp - entry)
    if sl_dist == 0:
        return None

    sym_swap = f"{symbol}-SWAP"
    ts_end   = ts_entry + max_horizon_m * 60 * 1000

    date_keys = _date_keys_for_range(ts_entry, ts_end)

    # streaming over tape files
    touched_tp_ms = None
    touched_sl_ms = None
    mfe_r = 0.0
    mae_r = 0.0
    # per-horizon tracking
    exit_at_horizon = {h: None for h in HORIZONS_M}
    # BE hypothesis: after MFE crosses 0.5R → SL = entry (0R)
    be05_triggered = False
    be10_triggered = False
    be05_exit_r    = None
    be10_exit_r    = None

    # New variants (Stage 2):
    # - single_tp15: use fixed TP at 1.5R (ignore original TP1 @ 0.4-0.8R), same SL.
    # - trail_1r  : once mfe >= 1R, SL trails at (mfe_r - 1.0). TP at 1.5R.
    # - step_trail: discrete locks. 0.5R→BE, 1R→+0.5, 2R→+1.5. TP at 1.5R.
    tp15_r          = 1.5
    single_tp15_exit_r = None      # None until resolved
    trail_1r_exit_r    = None
    step_trail_exit_r  = None
    trail_sl_r         = -1.0     # for trail_1r
    step_sl_r          = -1.0     # for step_trail

    last_price = entry
    last_ts    = ts_entry
    n_ticks = 0

    for dk in date_keys:
        for ts, base, _, price in _iter_tape_day(dk, {sym_swap}, ts_entry, ts_end):
            if base != symbol:
                continue
            n_ticks += 1
            elapsed_m = (ts - ts_entry) / 60000.0

            unit = (price - entry) if side == "buy" else (entry - price)
            r    = unit / sl_dist

            if r > mfe_r: mfe_r = r
            if r < mae_r: mae_r = r

            if touched_tp_ms is None and r >= (tp_dist / sl_dist):
                touched_tp_ms = ts
            if touched_sl_ms is None and r <= -1.0:
                touched_sl_ms = ts

            # BE tracking
            # Arm only if SL wasn't already touched — otherwise position
            # would have been closed at -1R before BE could activate.
            _sl_already = touched_sl_ms is not None and touched_sl_ms <= ts
            if not be05_triggered and mfe_r >= 0.5 and not _sl_already:
                be05_triggered = True
            if be05_triggered and be05_exit_r is None:
                # SL is at entry (0R). Check if price came back and hit tp first or entry.
                if r >= (tp_dist / sl_dist):
                    be05_exit_r = tp_dist / sl_dist
                elif r <= 0.0:
                    # price returned to entry — close at 0
                    be05_exit_r = 0.0

            if not be10_triggered and mfe_r >= 1.0 and not _sl_already:
                be10_triggered = True
            if be10_triggered and be10_exit_r is None:
                if r >= (tp_dist / sl_dist):
                    be10_exit_r = tp_dist / sl_dist
                elif r <= 0.0:
                    be10_exit_r = 0.0

            # single_tp15: fixed 1.5R target, original SL (-1R).
            if single_tp15_exit_r is None:
                if r >= tp15_r:
                    single_tp15_exit_r = tp15_r
                elif r <= -1.0:
                    single_tp15_exit_r = -1.0

            # trail_1r: until mfe >= 1, SL at -1R; then SL = mfe_r - 1.0 (locks +0, +1, +2...).
            if trail_1r_exit_r is None:
                if mfe_r >= 1.0:
                    # trail SL = mfe_r - 1.0
                    trail_sl_r = max(trail_sl_r, mfe_r - 1.0)
                if r >= tp15_r:
                    trail_1r_exit_r = tp15_r
                elif r <= trail_sl_r:
                    trail_1r_exit_r = trail_sl_r

            # step_trail: discrete locks.
            if step_trail_exit_r is None:
                if mfe_r >= 2.0:
                    step_sl_r = max(step_sl_r, 1.5)
                elif mfe_r >= 1.0:
                    step_sl_r = max(step_sl_r, 0.5)
                elif mfe_r >= 0.5:
                    step_sl_r = max(step_sl_r, 0.0)
                if r >= tp15_r:
                    step_trail_exit_r = tp15_r
                elif r <= step_sl_r:
                    step_trail_exit_r = step_sl_r

            # per-horizon fill
            for h in HORIZONS_M:
                if exit_at_horizon[h] is not None: continue
                if elapsed_m > h:
                    exit_at_horizon[h] = ("TIME_EXIT", r)
                    continue
                # check TP/SL within this horizon
                if r >= (tp_dist / sl_dist):
                    exit_at_horizon[h] = ("TP", tp_dist / sl_dist)
                elif r <= -1.0:
                    exit_at_horizon[h] = ("STOP", -1.0)

            last_price = price
            last_ts    = ts

    # close any open horizon
    for h in HORIZONS_M:
        if exit_at_horizon[h] is None:
            # no data beyond — use last tick
            if side == "buy":
                r = (last_price - entry)/sl_dist
            else:
                r = (entry - last_price)/sl_dist
            exit_at_horizon[h] = ("INCOMPLETE", r)

    # original (with actual hold_m)
    # find first of: TP touch, SL touch, or TIME_EXIT at hold_m
    original_outcome = None
    original_r       = None
    # compare touch times
    tp_ms = touched_tp_ms
    sl_ms = touched_sl_ms
    horizon_ms = ts_entry + hold_m*60*1000
    # pick earliest
    cands = []
    if tp_ms is not None and tp_ms <= horizon_ms: cands.append((tp_ms, "TP", tp_dist/sl_dist))
    if sl_ms is not None and sl_ms <= horizon_ms: cands.append((sl_ms, "STOP", -1.0))
    if cands:
        cands.sort()
        _, original_outcome, original_r = cands[0]
    else:
        # TIME_EXIT at horizon
        # r at horizon — find last tick before horizon
        # for simplicity use exit_at_horizon lookup: closest horizon >= hold_m
        closest = min((h for h in HORIZONS_M if h >= hold_m), default=HORIZONS_M[-1])
        eo, er = exit_at_horizon[closest]
        original_outcome = "TIME_EXIT" if eo == "TIME_EXIT" else eo
        original_r = er

    # BE hypotheses: if not triggered, use original
    if be05_exit_r is None:
        be05_final = original_r
    else:
        # BE triggered + exit: take that exit
        be05_final = be05_exit_r
    if be10_exit_r is None:
        be10_final = original_r
    else:
        be10_final = be10_exit_r

    # New-variant resolution: if not exited in-walk, close at hold_m with last r.
    # last_r at horizon
    last_r = (last_price - entry)/sl_dist if side == "buy" else (entry - last_price)/sl_dist
    if single_tp15_exit_r is None:
        # held to end — take last_r (capped at -1/+1.5 floor/ceiling isn't needed, walk guarantees)
        single_tp15_exit_r = last_r
    if trail_1r_exit_r is None:
        trail_1r_exit_r = last_r
    if step_trail_exit_r is None:
        step_trail_exit_r = last_r

    return {
        "signal_id":    sig["signal_id"],
        "symbol":       symbol,
        "side":         side,
        "style":        sig.get("style"),
        "regime":       sig.get("regime"),
        "source":       sig.get("source"),
        "ts_entry":     ts_entry,
        "hold_m":       hold_m,
        "sl_dist_pct":  sl_dist/entry*100,
        "tp_over_sl":   tp_dist/sl_dist,
        "real_mfe_r":   round(mfe_r, 3),
        "real_mae_r":   round(mae_r, 3),
        "tp_hit_m":     round((tp_ms - ts_entry)/60000, 1) if tp_ms else None,
        "sl_hit_m":     round((sl_ms - ts_entry)/60000, 1) if sl_ms else None,
        "original":     (original_outcome, round(original_r, 3)),
        "be05":         round(be05_final, 3),
        "be10":         round(be10_final, 3),
        "single_tp15":  round(single_tp15_exit_r, 3),
        "trail_1r":     round(trail_1r_exit_r, 3),
        "step_trail":   round(step_trail_exit_r, 3),
        "h150":         (exit_at_horizon[150][0], round(exit_at_horizon[150][1], 3)),
        "h300":         (exit_at_horizon[300][0], round(exit_at_horizon[300][1], 3)),
        "h480":         (exit_at_horizon[480][0], round(exit_at_horizon[480][1], 3)),
        "h720":         (exit_at_horizon[720][0], round(exit_at_horizon[720][1], 3)),
        "h1440":        (exit_at_horizon[1440][0], round(exit_at_horizon[1440][1], 3)),
        "n_ticks":      n_ticks,
    }


def main(limit=None):
    sigs = _load_signals()
    labs = _load_labels()

    # Earliest tape day
    tape_days = sorted([p.stem.replace(".csv","") for p in TAPE_DIR.glob("*.csv*")])
    tape_days = [d for d in tape_days if len(d) == 10]  # YYYY-MM-DD
    if not tape_days:
        print("No tape files!")
        return
    tape_start = datetime.strptime(tape_days[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    tape_start_ms = int(tape_start.timestamp()*1000)
    print(f"Tape covers {tape_days[0]} .. {tape_days[-1]} ({len(tape_days)} days)")
    print(f"Total signals: {len(sigs)}")
    filtered = [s for s in sigs if s["ts_ms"] >= tape_start_ms]
    print(f"Signals within tape window: {len(filtered)}\n")

    if limit:
        filtered = filtered[:limit]

    results = []
    for i, s in enumerate(filtered, 1):
        r = _simulate_signal(s)
        if r is None:
            print(f"[{i}/{len(filtered)}] {s['signal_id']}: SKIP (zero SL distance)")
            continue
        lab = labs.get(s["signal_id"], {})
        r["label_outcome"] = lab.get("outcome")
        r["label_exit_r"]  = lab.get("exit_r")
        r["label_mfe_r"]   = lab.get("mfe_r")
        r["label_mae_r"]   = lab.get("mae_r")
        r["label_elapsed"] = lab.get("elapsed_m")
        results.append(r)
        if i % 10 == 0:
            print(f"  processed {i}/{len(filtered)}")

    print(f"\nProcessed {len(results)} signals.\n")

    # Save raw JSON for follow-up analysis
    out_path = ROOT / "tape_verify_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Saved raw results → {out_path}\n")

    # Aggregate
    def _sum_r(rows, key):
        return sum(x[key] if isinstance(x[key], (int, float)) else x[key][1] for x in rows)

    print("=" * 72)
    print("AGGREGATE COMPARISON (sum R, all signals in window)")
    print("=" * 72)
    print(f"{'Scenario':<20} {'sumR':>10} {'avgR':>8} {'WR%':>8} {'n':>6}")
    def _report(name, rows, getter):
        vals = [getter(x) for x in rows]
        if not vals: return
        sr = sum(vals)
        n  = len(vals)
        wr = sum(1 for v in vals if v > 0)/n*100
        print(f"{name:<20} {sr:+10.2f} {sr/n:+8.3f} {wr:>7.1f}% {n:>6}")

    _report("label_exit_r",  [r for r in results if r["label_exit_r"] is not None],
            lambda r: r["label_exit_r"])
    _report("tape_original", results, lambda r: r["original"][1])
    _report("tape_be05",     results, lambda r: r["be05"])
    _report("tape_be10",     results, lambda r: r["be10"])
    _report("tape_single_tp15", results, lambda r: r["single_tp15"])
    _report("tape_trail_1r",    results, lambda r: r["trail_1r"])
    _report("tape_step_trail",  results, lambda r: r["step_trail"])
    for h in HORIZONS_M:
        _report(f"tape_h{h}",  results, lambda r, _h=h: r[f"h{_h}"][1])

    # Per-side breakdown for top variants
    print("\n" + "=" * 72)
    print("Per-side breakdown")
    print("=" * 72)
    for side in ("buy", "sell"):
        side_rows = [r for r in results if r["side"] == side]
        if not side_rows: continue
        print(f"\n-- {side.upper()} (n={len(side_rows)}) --")
        print(f"{'Scenario':<20} {'sumR':>10} {'avgR':>8} {'WR%':>8}")
        _report("original", side_rows, lambda r: r["original"][1])
        _report("be05", side_rows, lambda r: r["be05"])
        _report("be10", side_rows, lambda r: r["be10"])
        _report("single_tp15", side_rows, lambda r: r["single_tp15"])
        _report("trail_1r", side_rows, lambda r: r["trail_1r"])
        _report("step_trail", side_rows, lambda r: r["step_trail"])

    # How many TIME_EXIT would improve with longer hold?
    print("\n" + "=" * 72)
    print("TIME_EXIT cohort: what if held longer?")
    print("=" * 72)
    te_rows = [r for r in results if r["label_outcome"] == "TIME_EXIT"]
    print(f"n={len(te_rows)}")
    for h in HORIZONS_M:
        _report(f"  h{h}", te_rows, lambda r, _h=h: r[f"h{_h}"][1])

    # How many STOP trades had MFE>=0.5R and BE would save them?
    print("\n" + "=" * 72)
    print("STOP cohort with MFE>=0.5R (our 'swept' trades):")
    print("=" * 72)
    sw_rows = [r for r in results if r["label_outcome"] == "STOP" and (r.get("label_mfe_r") or 0) >= 0.5]
    print(f"n={len(sw_rows)}")
    _report("  original (-1R)", sw_rows, lambda r: -1.0)
    _report("  be05",            sw_rows, lambda r: r["be05"])
    _report("  be10",            sw_rows, lambda r: r["be10"])

    # Discrepancy vs label
    print("\n" + "=" * 72)
    print("DISCREPANCY: tape_original vs label")
    print("=" * 72)
    diffs = []
    for r in results:
        if r["label_exit_r"] is None: continue
        diff = r["original"][1] - r["label_exit_r"]
        diffs.append(diff)
    if diffs:
        import statistics
        print(f"n={len(diffs)} | mean={statistics.mean(diffs):+.3f} | median={statistics.median(diffs):+.3f}")
        big = [d for d in diffs if abs(d) >= 0.3]
        print(f"abs(diff) >= 0.3R: {len(big)}")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    main(limit=limit)
