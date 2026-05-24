"""
geometry_model.py - post-entry geometry / entry-quality audit across channels.

Read-only. Reconstructs the post-entry price path from local tick tape
(E:\\trading-data\\ticks) for each closed trade and measures:
  post_mfe  - max favourable signed return AFTER entry (was there a move to catch?)
  post_mae  - max adverse signed return AFTER entry
  net       - realised result (from record, or computed from exit)

Answers: is the loss an ENTRY problem (we enter at the spike top -> post_mfe<=0)
or an EXIT problem (move existed -> post_mfe>0 -> but we gave it back)?

Channels: impulse (dead, reference) | main_ws (live) | bb_fade (live).
Charts -> docs/geometry/.  Usage: python scripts/analysis/research/geometry_model.py
"""
from __future__ import annotations

import io
import sys
import os
import gzip
import json
import datetime as dt
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[3]
TAPE = os.environ.get("TAPE_DATA_DIR", r"E:\trading-data\ticks")
OUT = ROOT / "docs" / "geometry"
OUT.mkdir(parents=True, exist_ok=True)
TOP_THR = 0.3   # post_mfe below this = "entered at the top, no move after"


def iso_ms(s: str) -> int:
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def load_ticks(symbol: str, t0: int, t1: int) -> list[tuple[int, float]]:
    d = os.path.join(TAPE, symbol)
    if not os.path.isdir(d):
        return []
    days = {dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d") for ms in (t0, t1)}
    out = []
    for day in sorted(days):
        for fn in (f"{day}.csv", f"{day}.csv.gz"):
            p = os.path.join(d, fn)
            if not os.path.exists(p):
                continue
            op = gzip.open if fn.endswith(".gz") else open
            with op(p, "rt", encoding="utf-8", errors="ignore") as fh:
                next(fh, None)
                for line in fh:
                    c = line.split(",")
                    if len(c) < 5 or c[3] == "GAP" or not c[4]:
                        continue
                    ts = int(c[0])
                    if t0 <= ts <= t1:
                        try:
                            out.append((ts, float(c[4])))
                        except ValueError:
                            pass
    return sorted(out)


def _jsonl(path: Path):
    if not path.exists():
        return []
    return [json.loads(x) for x in path.open(encoding="utf-8") if x.strip()]


def load_trades() -> list[dict]:
    """Unified trade list: {ch, sym, side, entry, t0_ms, t1_ms, net, regime, style}."""
    T = []
    # impulse (signals + outcomes)
    for ch, sp, op in [
        ("impulse", "logs/impulse_pump/impulse_pump_signals.jsonl", "logs/impulse_pump/impulse_pump_outcomes.jsonl"),
        ("impulse", "logs/main_impulse/main_impulse_signals.jsonl", "logs/main_impulse/main_impulse_outcomes.jsonl"),
    ]:
        sg = {s["signal_id"]: s for s in _jsonl(ROOT / sp)}
        for o in _jsonl(ROOT / op):
            s = sg.get(o["signal_id"])
            if not s:
                continue
            entry = s.get("entry_price") or s.get("entry") or s.get("raw_entry_price")
            if not entry:
                continue
            T.append({"ch": ch, "sym": o["symbol"], "side": o["side"], "entry": entry,
                      "t0": iso_ms(o["ts"]), "t1": iso_ms(o["recorded_at"]),
                      "net": o.get("net_pct"), "regime": None, "style": None})
    # main_ws (signals + labels)
    sg = {s["id"]: s for s in _jsonl(ROOT / "logs/signals/main_signals.jsonl")}
    for lb in _jsonl(ROOT / "logs/signals/main_signals_labels.jsonl"):
        s = sg.get(lb["signal_id"])
        if not s:
            continue
        entry = s.get("entry")
        if not entry:
            continue
        hold = lb.get("hold_min") or s.get("hold_min") or 120
        t0 = iso_ms(s["ts"])
        T.append({"ch": "main_ws", "sym": s["symbol"], "side": s["side"], "entry": entry,
                  "t0": t0, "t1": t0 + int(hold) * 60000, "net": None,
                  "outcome": lb.get("outcome"), "exit_price": lb.get("exit_price"),
                  "regime": s.get("regime"), "style": s.get("trade_style")})
    # bb_fade
    for s in _jsonl(ROOT / "logs/bb_fade/bb_fade_signals.jsonl"):
        entry = s.get("entry")
        if not entry or s.get("outcome") in (None, ""):
            continue
        t0 = iso_ms(s["ts"]); hold = s.get("hold_min") or 80
        T.append({"ch": "bb_fade", "sym": s["symbol"], "side": s["side"], "entry": entry,
                  "t0": t0, "t1": t0 + int(hold) * 60000, "net": s.get("net_pct"),
                  "regime": None, "style": "FADE"})
    return T


def enrich(t: dict) -> dict | None:
    tk = load_ticks(t["sym"], t["t0"], t["t1"])
    if len(tk) < 3:
        return None
    sgn = 1 if t["side"] in ("long", "buy") else -1
    rs = [sgn * (p - t["entry"]) / t["entry"] * 100 for _, p in tk]
    t = dict(t)
    t["mfe"] = max(rs); t["mae"] = min(rs)
    t["r"] = rs; t["tmin"] = [(ts - tk[0][0]) / 60000 for ts, _ in tk]
    if t["net"] is None:
        ep = t.get("exit_price")
        t["net"] = (sgn * (ep - t["entry"]) / t["entry"] * 100 - 0.1) if ep else (rs[-1] - 0.1)
    t["sym_s"] = t["sym"].replace("-USDT-SWAP", "")
    return t


def summary(rows: list[dict]) -> None:
    print(f"\n{'channel':9} {'n':>3} {'avgMFE':>7} {'avgMAE':>7} {'avgNet':>7} {'top%':>6} {'WR':>5}")
    for ch in ("impulse", "main_ws", "bb_fade"):
        g = [r for r in rows if r["ch"] == ch]
        if not g:
            continue
        n = len(g)
        top = sum(1 for r in g if r["mfe"] < TOP_THR)
        dec = [r for r in g if r["net"] is not None]
        wr = 100 * sum(1 for r in dec if r["net"] > 0) / len(dec) if dec else 0
        print(f"{ch:9} {n:3} {sum(r['mfe'] for r in g)/n:+7.2f} {sum(r['mae'] for r in g)/n:+7.2f} "
              f"{sum(r['net'] for r in g)/n:+7.2f} {100*top//n:5}% {wr:4.0f}%")


def chart_scatter(rows):
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6), sharey=True)
    for ax, ch in zip(axes, ("impulse", "main_ws", "bb_fade")):
        g = [r for r in rows if r["ch"] == ch]
        ax.axvspan(-8, TOP_THR, color="red", alpha=0.07)
        if g:
            ax.scatter([r["mfe"] for r in g], [r["net"] for r in g],
                       c=["#2ca02c" if r["net"] > 0 else "#d62728" for r in g],
                       s=45, edgecolor="k", lw=0.4, zorder=3)
        ax.axvline(TOP_THR, ls="--", c="gray"); ax.axhline(0, c="k", lw=0.5)
        top = sum(1 for r in g if r["mfe"] < TOP_THR)
        ax.set_title(f"{ch}  (n={len(g)}, вход-в-топ {100*top//max(len(g),1)}%)")
        ax.set_xlabel("post-entry MFE %")
    axes[0].set_ylabel("net %")
    fig.suptitle("Качество входа: post-entry MFE vs net (красная зона = после входа хода не было)")
    fig.tight_layout(); fig.savefig(OUT / "10_scatter_by_channel.png", dpi=110); plt.close(fig)


def chart_hist(rows):
    fig, ax = plt.subplots(figsize=(9, 5))
    bins = [-8, -3, -1, -0.3, 0.3, 1, 2, 3, 8]
    for ch, c in [("impulse", "#8c564b"), ("main_ws", "#1f77b4"), ("bb_fade", "#ff7f0e")]:
        g = [r["mfe"] for r in rows if r["ch"] == ch]
        if g:
            ax.hist(g, bins=bins, alpha=0.55, label=f"{ch} (n={len(g)})", color=c)
    ax.axvline(TOP_THR, ls="--", c="red", label=f"порог входа-в-топ ({TOP_THR}%)")
    ax.set_xlabel("post-entry MFE %"); ax.set_ylabel("сделок"); ax.legend()
    ax.set_title("Распределение post-entry MFE по каналам")
    fig.tight_layout(); fig.savefig(OUT / "11_hist_postmfe.png", dpi=110); plt.close(fig)


def chart_mfe_mae(rows):
    fig, ax = plt.subplots(figsize=(8, 6))
    cmap = {"impulse": "#8c564b", "main_ws": "#1f77b4", "bb_fade": "#ff7f0e"}
    for ch, c in cmap.items():
        g = [r for r in rows if r["ch"] == ch]
        if g:
            ax.scatter([r["mfe"] for r in g], [-r["mae"] for r in g], c=c, s=45,
                       edgecolor="k", lw=0.4, label=f"{ch} (n={len(g)})", alpha=0.8)
    lim = 6
    ax.plot([0, lim], [0, lim], ls=":", c="k", label="MFE = |MAE| (R:R 1:1)")
    ax.set_xlabel("post-entry MFE % (потенциал в нашу сторону)")
    ax.set_ylabel("|post-entry MAE| % (риск против)")
    ax.set_title("Геометрия: ход в нашу сторону vs против.\nНиже линии = рискуем больше чем ловим")
    ax.legend(); ax.set_xlim(-0.5, lim); ax.set_ylim(-0.5, lim)
    fig.tight_layout(); fig.savefig(OUT / "12_mfe_vs_mae.png", dpi=110); plt.close(fig)


def chart_main_by_regime(rows):
    g = [r for r in rows if r["ch"] == "main_ws" and r["regime"]]
    if not g:
        return
    keys = {}
    for r in g:
        k = f"{r['regime']}\n{r['style']}"
        keys.setdefault(k, []).append(r)
    labels = sorted(keys, key=lambda k: -len(keys[k]))
    avg_mfe = [sum(x["mfe"] for x in keys[k]) / len(keys[k]) for k in labels]
    ns = [len(keys[k]) for k in labels]
    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(range(len(labels)), avg_mfe,
                  color=["#2ca02c" if v >= TOP_THR else "#d62728" for v in avg_mfe])
    ax.axhline(TOP_THR, ls="--", c="gray")
    ax.set_xticks(range(len(labels))); ax.set_xticklabels(labels, fontsize=8)
    for b, n in zip(bars, ns):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height(), f"n={n}", ha="center", va="bottom", fontsize=8)
    ax.set_ylabel("avg post-entry MFE %")
    ax.set_title("Main WS: качество входа по режиму×стилю (зелёный = вход ловит ход)")
    fig.tight_layout(); fig.savefig(OUT / "13_mainws_by_regime.png", dpi=110); plt.close(fig)


def chart_examples(rows):
    live = [r for r in rows if r["ch"] in ("main_ws", "bb_fade")]
    if not live:
        return
    good = sorted(live, key=lambda r: -r["mfe"])[:2]
    bad = sorted(live, key=lambda r: r["mfe"])[:2]
    ex = good + bad
    fig, axes = plt.subplots(2, 2, figsize=(12, 7))
    for ax, r in zip(axes.flat, ex):
        ax.plot(r["tmin"], r["r"], lw=1.1, c="#1f77b4")
        ax.axhline(0, c="k", lw=1)
        ax.fill_between(r["tmin"], r["r"], 0, where=[x < 0 for x in r["r"]], color="red", alpha=0.15)
        ax.fill_between(r["tmin"], r["r"], 0, where=[x >= 0 for x in r["r"]], color="green", alpha=0.15)
        ax.set_title(f"{r['ch']} {r['sym_s']} {r['side']}  MFE={r['mfe']:.2f}% MAE={r['mae']:.2f}% net={r['net']:.2f}%", fontsize=9)
        ax.set_xlabel("мин после входа")
    fig.suptitle("Примеры: хорошие входы (верх) vs входы-в-топ (низ)")
    fig.tight_layout(); fig.savefig(OUT / "14_examples.png", dpi=110); plt.close(fig)


def main():
    raw = load_trades()
    rows = [e for e in (enrich(t) for t in raw) if e]
    print(f"загружено сделок: {len(raw)} | с тейп-покрытием: {len(rows)}")
    by = {}
    for t in raw:
        by[t["ch"]] = by.get(t["ch"], 0) + 1
    cov = {}
    for r in rows:
        cov[r["ch"]] = cov.get(r["ch"], 0) + 1
    for ch in by:
        print(f"  {ch:9}: {cov.get(ch,0)}/{by[ch]} покрыто тейпом")
    summary(rows)
    chart_scatter(rows); chart_hist(rows); chart_mfe_mae(rows)
    chart_main_by_regime(rows); chart_examples(rows)
    print("\nкартинки ->", [p.name for p in sorted(OUT.glob("*.png"))])


if __name__ == "__main__":
    main()
