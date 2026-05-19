"""Analyze main_signals.jsonl + main_signals_labels.jsonl — WS screener signals."""
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_ROOT   = Path(__file__).resolve().parents[2]
LOG     = _ROOT / "logs" / "signals" / "main_signals.jsonl"
LABELS  = _ROOT / "logs" / "signals" / "main_signals_labels.jsonl"
OUT_DIR = Path(__file__).parent / "backtest_runs"


_OUTCOME_MAP = {"TP1": "TP", "TP2": "TP", "SL": "STOP", "TIME": "TIME_EXIT"}


def load() -> list[dict]:
    signals = {}
    with open(LOG, encoding="utf-8") as f:
        for line in f:
            try:
                s = json.loads(line)
                sid = s.get("id") or s.get("signal_id")
                if sid:
                    s["signal_id"] = sid
                    signals[sid] = s
            except Exception:
                pass
    with open(LABELS, encoding="utf-8") as f:
        for line in f:
            try:
                lb = json.loads(line)
                sid = lb["signal_id"]
                if sid in signals:
                    lb["outcome"] = _OUTCOME_MAP.get(lb.get("outcome", ""), lb.get("outcome", ""))
                    signals[sid].update(lb)
            except Exception:
                pass
    merged = [s for s in signals.values() if "outcome" in s]
    for s in merged:
        # derive ts_ms from ts string if missing
        if not s.get("ts_ms") and s.get("ts"):
            try:
                from datetime import timezone
                s["ts_ms"] = int(datetime.fromisoformat(
                    s["ts"].replace("Z", "+00:00")).timestamp() * 1000)
            except Exception:
                pass
        # derive exit_r from exit_price + entry + sl
        if not s.get("exit_r") and s.get("exit_price") and s.get("entry") and s.get("sl"):
            try:
                entry = float(s["entry"])
                sl    = float(s["sl"])
                ep    = float(s["exit_price"])
                sl_dist = abs(entry - sl)
                if sl_dist > 0:
                    if s.get("side") == "sell":
                        s["exit_r"] = (entry - ep) / sl_dist
                    else:
                        s["exit_r"] = (ep - entry) / sl_dist
            except Exception:
                pass
    merged.sort(key=lambda s: s.get("ts_ms") or 0)
    return merged


def fv(s, key, default=None):
    v = s.get(key)
    if v in ("", None):
        return default
    try:
        return float(v)
    except Exception:
        return v


def metrics(rows: list[dict]) -> dict:
    if not rows:
        return {}
    tp   = [r for r in rows if r["outcome"] == "TP"]
    sl   = [r for r in rows if r["outcome"] == "STOP"]
    te   = [r for r in rows if r["outcome"] == "TIME_EXIT"]
    n    = len(rows)
    denom = len(tp) + len(sl)
    wr        = round(len(tp) / denom * 100, 1) if denom else 0.0
    honest_wr = round(len(tp) / n * 100, 1) if n else 0.0

    def avg(lst, key):
        vals = [fv(r, key) for r in lst if fv(r, key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    exit_r_tp = [fv(r, "exit_r") for r in tp if fv(r, "exit_r") is not None]
    exit_r_sl = [fv(r, "exit_r") for r in sl if fv(r, "exit_r") is not None]
    gw = sum(exit_r_tp) if exit_r_tp else 0
    gl = abs(sum(exit_r_sl)) if exit_r_sl else 0
    pf = round(gw / gl, 2) if gl > 0 else 99.0

    return {
        "n": n, "n_tp": len(tp), "n_sl": len(sl), "n_te": len(te),
        "wr": wr, "honest_wr": honest_wr, "pf": pf,
        "avg_exit_r":     avg(rows, "exit_r"),
        "avg_mfe_tp":     avg(tp,   "mfe_r"),
        "avg_mfe_te":     avg(te,   "mfe_r"),
        "avg_elapsed_tp": avg(tp,   "elapsed_m"),
        "avg_elapsed_te": avg(te,   "elapsed_m"),
    }


def sep(char="─", width=72):
    return char * width


def row_fmt(cols, widths):
    return "".join(str(c).rjust(w) for c, w in zip(cols, widths))


def main():
    rows = load()
    n = len(rows)
    if n == 0:
        print("No labeled signals found.")
        return

    fade    = [r for r in rows if "FADE" in r.get("signal_id", "")]
    scanner = [r for r in rows if "FADE" not in r.get("signal_id", "")]

    lines = []
    lines += ["", "=" * 72,
              f"  SIGNAL LOG ANALYSIS  —  {datetime.now().strftime('%Y-%m-%d %H:%M')}",
              f"  Total: {n} signals  |  Scanner: {len(scanner)}  |  FADE: {len(fade)}",
              "=" * 72]

    # ── SECTION 1: Overall ───────────────────────────────────────────────────
    lines += ["", sep(), "  1. OVERALL", sep()]
    for label, subset in [("ALL", rows), ("SCANNER", scanner), ("FADE", fade)]:
        m = metrics(subset)
        if not m:
            continue
        lines.append(
            f"  {label:8s}  n={m['n']:3d}"
            f"  WR(TP+SL)={m['wr']:5.1f}%  honest_WR={m['honest_wr']:5.1f}%"
            f"  PF={m['pf']:5.2f}  avg_R={m['avg_exit_r'] or 0:+.3f}"
            f"  TP={m['n_tp']}  SL={m['n_sl']}  TIME={m['n_te']}"
        )

    # ── SECTION 2: By pair ───────────────────────────────────────────────────
    lines += ["", sep(), "  2. BY PAIR", sep()]
    widths2 = [12, 5, 7, 7, 8, 9, 9]
    lines.append(row_fmt(["pair", "n", "WR%", "PF", "avg_R", "mfe_TP", "elap_TP"], widths2))
    lines.append("  " + "─" * 58)
    by_pair = defaultdict(list)
    for r in rows:
        by_pair[r.get("symbol", "?")].append(r)
    for sym in sorted(by_pair):
        m = metrics(by_pair[sym])
        lines.append("  " + row_fmt([
            sym, m["n"], m["wr"], m["pf"],
            f"{m['avg_exit_r'] or 0:+.3f}",
            m["avg_mfe_tp"] or "—",
            m["avg_elapsed_tp"] or "—",
        ], widths2))

    # ── SECTION 3: By regime × style ────────────────────────────────────────
    lines += ["", sep(), "  3. BY REGIME × STYLE", sep()]
    widths3 = [10, 7, 5, 7, 7, 8]
    lines.append(row_fmt(["regime", "style", "n", "WR%", "PF", "avg_R"], widths3))
    lines.append("  " + "─" * 48)
    by_rs = defaultdict(list)
    for r in rows:
        key = (r.get("regime", "?"), r.get("style", r.get("trade_style", "?")))
        by_rs[key].append(r)
    for (regime, style) in sorted(by_rs):
        m = metrics(by_rs[(regime, style)])
        lines.append("  " + row_fmt([
            regime, style, m["n"], m["wr"], m["pf"],
            f"{m['avg_exit_r'] or 0:+.3f}",
        ], widths3))

    # ── SECTION 4: By hour UTC ───────────────────────────────────────────────
    lines += ["", sep(), "  4. BY HOUR UTC  (no filtering conclusions — research only)", sep()]
    widths4 = [6, 5, 7, 5, 5, 5]
    lines.append(row_fmt(["hour", "n", "WR%", "TP", "SL", "TIME"], widths4))
    lines.append("  " + "─" * 35)
    by_hour = defaultdict(list)
    for r in rows:
        ts = r.get("ts_ms")
        if ts:
            hour = int(ts) // 3_600_000 % 24
            by_hour[hour].append(r)
    for h in range(24):
        if h not in by_hour:
            continue
        m = metrics(by_hour[h])
        lines.append("  " + row_fmt([
            f"{h:02d}:00", m["n"], m["wr"], m["n_tp"], m["n_sl"], m["n_te"]
        ], widths4))

    # ── SECTION 5: Microstructure × outcome ─────────────────────────────────
    lines += ["", sep(), "  5. MICROSTRUCTURE × OUTCOME  (scanner only, n with data)", sep()]
    ms_rows = [r for r in scanner if fv(r, "trade_delta_100") is not None]

    def ms_avg(subset, key):
        vals = [fv(r, key) for r in subset if fv(r, key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    tp_ms  = [r for r in ms_rows if r["outcome"] == "TP"]
    sl_ms  = [r for r in ms_rows if r["outcome"] == "STOP"]
    te_ms  = [r for r in ms_rows if r["outcome"] == "TIME_EXIT"]
    lines.append(f"  {'outcome':10s}  {'n':>4s}  {'delta100':>9s}  {'obi5':>8s}  {'spread_bps':>11s}")
    lines.append("  " + "─" * 50)
    for label, subset in [("TP", tp_ms), ("STOP", sl_ms), ("TIME_EXIT", te_ms)]:
        if not subset:
            continue
        lines.append(
            f"  {label:10s}  {len(subset):4d}"
            f"  {str(ms_avg(subset, 'trade_delta_100')):>9s}"
            f"  {str(ms_avg(subset, 'obi5')):>8s}"
            f"  {str(ms_avg(subset, 'spread_bps')):>11s}"
        )

    # Delta direction match (signal side vs delta)
    matches = 0
    total_dm = 0
    for r in ms_rows:
        delta = fv(r, "trade_delta_100")
        side  = r.get("side")
        if delta is None or not side:
            continue
        total_dm += 1
        if (side == "buy" and delta > 0) or (side == "sell" and delta < 0):
            matches += 1
    if total_dm:
        lines.append(f"\n  Delta direction matches signal side: {matches}/{total_dm} ({matches/total_dm*100:.0f}%)")
        # WR when aligned vs not
        aligned     = [r for r in ms_rows if fv(r,"trade_delta_100") is not None
                       and ((r.get("side")=="buy" and fv(r,"trade_delta_100")>0)
                            or (r.get("side")=="sell" and fv(r,"trade_delta_100")<0))]
        not_aligned = [r for r in ms_rows if fv(r,"trade_delta_100") is not None
                       and r not in aligned]
        ma = metrics(aligned)
        mn = metrics(not_aligned)
        if ma:
            lines.append(f"  WR when delta ALIGNED:     {ma['wr']:5.1f}%  n={ma['n']}  PF={ma['pf']}")
        if mn:
            lines.append(f"  WR when delta NOT aligned: {mn['wr']:5.1f}%  n={mn['n']}  PF={mn['pf']}")

    # ── SECTION 6: TIME_EXIT deep dive ───────────────────────────────────────
    lines += ["", sep(), "  6. TIME_EXIT DEEP DIVE", sep()]
    te_all = [r for r in rows if r["outcome"] == "TIME_EXIT"]
    if te_all:
        mfe_vals = [fv(r, "mfe_r") for r in te_all if fv(r, "mfe_r") is not None]
        near_tp  = [v for v in mfe_vals if v >= 0.5]
        elapsed  = [fv(r, "elapsed_m") for r in te_all if fv(r, "elapsed_m") is not None]
        lines.append(f"  Total TIME_EXIT: {len(te_all)}")
        if mfe_vals:
            lines.append(f"  avg mfe_r:  {sum(mfe_vals)/len(mfe_vals):+.3f}R  (peak inside hold window)")
            lines.append(f"  mfe >= 0.5R: {len(near_tp)}/{len(mfe_vals)} ({len(near_tp)/len(mfe_vals)*100:.0f}%)  — were close to TP")
            lines.append(f"  mfe >= 1.0R: {sum(1 for v in mfe_vals if v>=1.0)}/{len(mfe_vals)}")
        if elapsed:
            lines.append(f"  avg elapsed: {sum(elapsed)/len(elapsed):.0f}m")
        lines += ["", "  TIME_EXIT by pair:"]
        te_pair = defaultdict(list)
        for r in te_all:
            te_pair[r.get("symbol","?")].append(r)
        for sym in sorted(te_pair):
            subset = te_pair[sym]
            mfe_v  = [fv(r,"mfe_r") for r in subset if fv(r,"mfe_r") is not None]
            avg_mfe = round(sum(mfe_v)/len(mfe_v), 3) if mfe_v else None
            lines.append(f"    {sym:12s}  n={len(subset):2d}  avg_mfe={avg_mfe}")

    # ── SECTION 7: MFE/MAE winners vs losers ────────────────────────────────
    lines += ["", sep(), "  7. MFE / MAE  —  WINNERS VS LOSERS", sep()]
    tp_all = [r for r in rows if r["outcome"] == "TP"]
    sl_all = [r for r in rows if r["outcome"] == "STOP"]

    def dist(lst, key, thresholds):
        vals = [fv(r, key) for r in lst if fv(r, key) is not None]
        if not vals:
            return "—"
        parts = []
        for t in thresholds:
            cnt = sum(1 for v in vals if v >= t)
            parts.append(f">={t}R:{cnt}")
        return f"avg={sum(vals)/len(vals):+.3f}  " + "  ".join(parts)

    lines.append(f"  TP  mfe_r : {dist(tp_all, 'mfe_r', [0.5, 1.0, 1.5])}")
    lines.append(f"  SL  mfe_r : {dist(sl_all, 'mfe_r', [0.5, 1.0])}")
    lines.append(f"  SL  mae_r : {dist(sl_all, 'mae_r', [-0.5, -1.0])}")

    lines.append("")
    output = "\n".join(lines)
    sys.stdout.buffer.write(output.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp   = datetime.now().strftime("%Y-%m-%d_%H-%M")
    outpath = OUT_DIR / f"signal_analysis_{stamp}.md"
    outpath.write_text(output, encoding="utf-8")
    sys.stdout.buffer.write(f"Saved -> {outpath.name}\n".encode("utf-8"))


if __name__ == "__main__":
    main()
