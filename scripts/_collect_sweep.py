"""Collect sweep results from saved JSONs and print comparison table."""
import json
import sys
from pathlib import Path
from datetime import datetime

RUNS    = Path(__file__).parent / "backtest_runs"
CONFIGS = ["baseline","slope_35","slope_40","hold_210","hold_240","s35_h210"]
DAYS    = 63


def metrics(trades):
    if not trades:
        return {}
    tp = [t for t in trades if t["outcome"] == "TP"]
    sl = [t for t in trades if t["outcome"] == "STOP"]
    te = [t for t in trades if t["outcome"] == "TIME_EXIT"]
    n  = len(trades)
    denom = len(tp) + len(sl)
    wr  = round(len(tp) / denom * 100, 1) if denom else 0
    gw  = sum(t["exit_r"] for t in tp)
    gl  = abs(sum(t["exit_r"] for t in sl))
    pf  = round(gw / gl, 2) if gl else 99.0
    spd = round(n / DAYS, 1)
    te_pct = round(len(te) / n * 100) if n else 0

    ex  = [t for t in trades if t.get("pnl") is not None]
    bal = 1000.0; peak = 1000.0; dd = 0.0
    for t in ex:
        bal += t["pnl"]
        if bal > peak: peak = bal
        d = (peak - bal) / peak * 100
        if d > dd: dd = d
    sim = round((bal - 1000) / 1000 * 100, 1)

    def avg(lst, k):
        v = [t.get(k) for t in lst if t.get(k) is not None]
        return round(sum(v)/len(v), 1) if v else None

    reg = {}
    for r in ("TRENDING", "DRIFT", "RANGING"):
        rt  = [t for t in trades if t.get("regime") == r]
        rtp = [t for t in rt if t["outcome"] == "TP"]
        rsl = [t for t in rt if t["outcome"] == "STOP"]
        rte = [t for t in rt if t["outcome"] == "TIME_EXIT"]
        d2  = len(rtp) + len(rsl)
        reg[r] = {"n": len(rt), "wr": round(len(rtp)/d2*100) if d2 else 0, "time": len(rte)}

    pair = {}
    for sym in ("BTC-USDT","ETH-USDT","SOL-USDT","XRP-USDT","DOGE-USDT"):
        pt  = [t for t in trades if t.get("symbol") == sym]
        ptp = [t for t in pt if t["outcome"] == "TP"]
        psl = [t for t in pt if t["outcome"] == "STOP"]
        pte = [t for t in pt if t["outcome"] == "TIME_EXIT"]
        d2  = len(ptp) + len(psl)
        if pt:
            pair[sym] = {"n": len(pt), "wr": round(len(ptp)/d2*100) if d2 else 0, "time": len(pte)}

    return {
        "n": n, "spd": spd, "wr": wr, "pf": pf, "sim": sim, "dd": round(dd, 1),
        "te_pct": te_pct, "n_tp": len(tp), "n_sl": len(sl), "n_te": len(te),
        "slope_tp":   avg(tp, "slope_15m"), "slope_sl": avg(sl, "slope_15m"),
        "elapsed_tp": avg(tp, "elapsed_m"), "elapsed_te": avg(te, "elapsed_m"),
        "reg": reg, "pair": pair,
    }


def main():
    all_m = {}
    for tag in CONFIGS:
        p = RUNS / f"backtest_results_{tag}.json"
        if not p.exists():
            print("MISSING: " + tag)
            continue
        with open(p, encoding="utf-8") as f:
            trades = json.load(f)
        all_m[tag] = metrics(trades)

    lines = []

    def row(*cols, widths=None):
        if widths is None:
            widths = [14] * len(cols)
        return "".join(str(c).rjust(w) for c, w in zip(cols, widths))

    lines.append("")
    lines.append("=" * 110)
    lines.append("  PARAM SWEEP RESULTS  --  " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    lines.append("=" * 110)

    lines.append("")
    lines.append("-- MAIN TABLE -------------------------------------------------------")
    lines.append(row("config","n/day","WR%","PF","sim%","DD%","TIME%","TP","SL","TIME",
                     widths=[13,7,7,7,7,7,7,6,6,6]))
    lines.append("-" * 76)
    for tag in CONFIGS:
        m = all_m.get(tag)
        if not m:
            continue
        mark = " <- baseline" if tag == "baseline" else ""
        lines.append(row(tag,m['spd'],m['wr'],m['pf'],m['sim'],m['dd'],m['te_pct'],
                         m['n_tp'],m['n_sl'],m['n_te'],
                         widths=[13,7,7,7,7,7,7,6,6,6]) + mark)

    lines.append("")
    lines.append("-- SLOPE AT ENTRY ---------------------------------------------------")
    lines.append(row("config","slope_TP","slope_SL","diff","elap_TP","elap_TE",
                     widths=[13,10,10,8,10,10]))
    lines.append("-" * 61)
    for tag in CONFIGS:
        m = all_m.get(tag)
        if not m:
            continue
        stp = m['slope_tp'] or 0
        ssl = m['slope_sl'] or 0
        lines.append(row(tag, stp, ssl, round(stp-ssl,1), m['elapsed_tp'], m['elapsed_te'],
                         widths=[13,10,10,8,10,10]))

    lines.append("")
    lines.append("-- BY REGIME --------------------------------------------------------")
    lines.append(f"{'config':13s}  {'TRENDING':22s}  {'DRIFT':22s}  {'RANGING':22s}")
    lines.append("-" * 85)
    for tag in CONFIGS:
        m = all_m.get(tag)
        if not m:
            continue
        def rs(r):
            d = m['reg'].get(r, {})
            return ("n=" + str(d.get('n','?')).rjust(3) +
                    " WR=" + str(d.get('wr','?')).rjust(3) + "%" +
                    " T=" + str(d.get('time','?')).rjust(2))
        lines.append(f"{tag:13s}  {rs('TRENDING'):22s}  {rs('DRIFT'):22s}  {rs('RANGING'):22s}")

    lines.append("")
    lines.append("-- BY PAIR ----------------------------------------------------------")
    lines.append(row("config","BTC","ETH","SOL","XRP","DOGE",
                     widths=[13,12,12,12,12,12]))
    lines.append("-" * 73)
    for tag in CONFIGS:
        m = all_m.get(tag)
        if not m:
            continue
        def ps(sym):
            d = m['pair'].get(sym, {})
            return (str(d['n']) + "/" + str(d['wr']) + "%") if d else "--"
        lines.append(row(tag,
                         ps("BTC-USDT"), ps("ETH-USDT"), ps("SOL-USDT"),
                         ps("XRP-USDT"), ps("DOGE-USDT"),
                         widths=[13,12,12,12,12,12]))

    lines.append("")
    output = "\n".join(lines)
    sys.stdout.buffer.write(output.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")

    # Save MD
    md = ["# Backtest Param Sweep -- " + datetime.now().strftime("%Y-%m-%d %H:%M"), ""]
    md += ["## Summary", "",
           "| Config | n/day | WR% | PF | sim% | DD% | TIME% | TP | SL | TIME | slope_TP | slope_SL |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for tag in CONFIGS:
        m = all_m.get(tag)
        if not m:
            continue
        md.append(f"| {tag} | {m['spd']} | {m['wr']} | {m['pf']} | {m['sim']} | {m['dd']}"
                  f" | {m['te_pct']} | {m['n_tp']} | {m['n_sl']} | {m['n_te']}"
                  f" | {m['slope_tp']} | {m['slope_sl']} |")
    md += ["", "## By regime", "",
           "| Config | TRENDING n/WR/T | DRIFT n/WR/T | RANGING n/WR/T |",
           "|---|---|---|---|"]
    for tag in CONFIGS:
        m = all_m.get(tag)
        if not m:
            continue
        def rmd(r):
            d = m['reg'].get(r, {})
            return str(d.get('n')) + "/" + str(d.get('wr')) + "%/T=" + str(d.get('time'))
        md.append(f"| {tag} | {rmd('TRENDING')} | {rmd('DRIFT')} | {rmd('RANGING')} |")
    md += ["", "## By pair", "",
           "| Config | BTC | ETH | SOL | XRP | DOGE |",
           "|---|---|---|---|---|---|"]
    for tag in CONFIGS:
        m = all_m.get(tag)
        if not m:
            continue
        def pmd(sym):
            d = m['pair'].get(sym, {})
            return (str(d['n']) + "/" + str(d['wr']) + "%") if d else "--"
        md.append(f"| {tag} | {pmd('BTC-USDT')} | {pmd('ETH-USDT')}"
                  f" | {pmd('SOL-USDT')} | {pmd('XRP-USDT')} | {pmd('DOGE-USDT')} |")

    stamp   = datetime.now().strftime("%Y-%m-%d_%H-%M")
    outpath = RUNS / ("sweep_" + stamp + ".md")
    outpath.write_text("\n".join(md), encoding="utf-8")
    sys.stdout.buffer.write(("Saved -> " + outpath.name + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
