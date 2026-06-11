# -*- coding: utf-8 -*-
"""
nogo_audit.py — offline-аудит NO_GO карточек: что сканер отфильтровал и что из этого
реально двинулось. Диагностика, ничего в проде не меняет.

Соединяет scanner_journal + scanner_outcomes по card_id, раскладывает каждый NO_GO в
audit_bucket (метрики = calibration_report.classify_miss, порог един):

  MISSED_IDIO_MOVE          |excess| >= th   — событие, не бета (реальный промах)
  MISSED_DIRECTIONAL_MOVE   |ret| >= th, excess ниже порога — в основном бета/рынок
  BETA_BLIND_MOVE           |ret| >= th при активе=своему baseline (BTC/CL/XAU) —
                            idio не определён by construction, НЕ записывать в idio-промахи
  VOLATILE_BUT_NO_DIRECTION max(|MFE|,|MAE|) >= th — фитили без финального хода
  CORRECT_NO_GO             тихо по всем осям
  CHIEF_ERROR_UNRESOLVED    chief был недоступен (гейт CHIEF_ERROR_*/CHIEF_UNAVAILABLE) —
                            это не качество суждения, отдельно от обычных NO_GO
  MANUAL_OR_UNSCORED / NOT_MATURE / MISSING_PRICE — нескоренные состояния

Выход: reports/scanner_no_go_audit/{no_go_audit.json,no_go_audit.csv,summary.md,samples/}.
Чарты — matplotlib по публичным свечам OKX (1H), только полезные подмножества.

Запуск:  python src/scout/nogo_audit.py [--threshold 3] [--no-charts] [--per-set 20]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.calibration_report import JOURNAL, OUTCOMES, _latest_by_card, _load_jsonl, classify_miss  # noqa: E402

OUT_BASE = _ROOT / "reports" / "scanner_no_go_audit"
UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 nogo-audit; keyless)"}

FIELDS = ["card_id", "ts_utc", "source", "layer", "asset", "okx_inst", "headline", "source_url",
          "low_confidence", "chief_called", "escalation_gate", "in_price", "surprise",
          "event_phase", "lead_class",
          "materiality_score", "price_at_decision", "horizon_hours",
          "outcome_long_pct", "outcome_short_pct", "ret_pct", "mfe_long_pct", "mae_long_pct",
          "excess_pct", "beta_blind", "missed_move", "verdict_correct", "outcome_source",
          "chart_path", "audit_bucket"]

CHIEF_ERROR_GATES = ("CHIEF_ERROR_FALLBACK", "CHIEF_ERROR_PENDING", "CHIEF_UNAVAILABLE")
UNSCORED_BUCKETS = ("NOT_MATURE", "MANUAL_OR_UNSCORED", "MISSING_PRICE", "CHIEF_ERROR_UNRESOLVED")


def _parse_ts(ts: str) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def _is_beta_blind(jrow: dict, outcome: dict | None) -> bool:
    """Актив сам себе baseline (новые outcomes несут флаг; старые — по журналу)."""
    if outcome and outcome.get("beta_blind") is not None:
        return bool(outcome.get("beta_blind"))
    inst = jrow.get("okx_inst")
    return bool(inst) and inst == jrow.get("baseline_symbol")


def classify_bucket(jrow: dict, outcome: dict | None, now: dt.datetime, threshold: float = 3.0) -> str:
    """Разложить NO_GO карточку в audit_bucket (см. шапку модуля)."""
    if str(jrow.get("escalation_gate") or "") in CHIEF_ERROR_GATES:
        return "CHIEF_ERROR_UNRESOLVED"   # chief упал — не качество суждения фильтра
    if outcome and outcome.get("scored"):
        if outcome.get("ret_pct") is None and outcome.get("mfe_long_pct") is None:
            return "MISSING_PRICE"
        miss = classify_miss(outcome, threshold)
        beta_blind = _is_beta_blind(jrow, outcome)
        if miss["idio"] and not beta_blind:
            return "MISSED_IDIO_MOVE"
        if miss["dir"]:
            return "BETA_BLIND_MOVE" if beta_blind else "MISSED_DIRECTIONAL_MOVE"
        if miss["vol"]:
            return "VOLATILE_BUT_NO_DIRECTION"
        return "CORRECT_NO_GO"
    if outcome:
        return "MANUAL_OR_UNSCORED"
    t0 = _parse_ts(jrow.get("ts_utc") or "")
    try:
        horizon = float(jrow.get("horizon_hours") or 0)
    except (TypeError, ValueError):
        horizon = 0
    if t0 is None:
        return "MISSING_PRICE"
    if now < t0 + dt.timedelta(hours=horizon):
        return "NOT_MATURE"
    if not jrow.get("okx_inst"):
        return "MISSING_PRICE"
    return "MANUAL_OR_UNSCORED"   # зрелая, инструмент есть, но исход не посчитан


def build_dataset(journal_rows: list[dict], outcome_rows: list[dict],
                  now: dt.datetime, threshold: float = 3.0) -> list[dict]:
    """NO_GO журнала + исходы → плоские записи аудита (поля FIELDS, chart_path пустой)."""
    latest = _latest_by_card(outcome_rows)
    out = []
    for r in journal_rows:
        if r.get("verdict") != "NO_GO":
            continue
        o = latest.get(str(r.get("card_id"))) or {}
        rec = {k: r.get(k) for k in FIELDS if k in r}
        for k in ("outcome_long_pct", "outcome_short_pct", "ret_pct", "mfe_long_pct",
                  "mae_long_pct", "excess_pct", "beta_blind", "missed_move", "verdict_correct"):
            rec[k] = o.get(k)
        rec["chart_path"] = ""
        rec["audit_bucket"] = classify_bucket(r, o or None, now, threshold)
        out.append({k: rec.get(k) for k in FIELDS})
    return out


def _abs(v) -> float:
    try:
        return abs(float(v))
    except (TypeError, ValueError):
        return 0.0


def select_chart_rows(rows: list[dict], per_set: int = 20, correct_n: int = 10) -> list[dict]:
    """Полезные подмножества для чартов (дедуп по card_id, детерминированно)."""
    by_bucket: dict[str, list[dict]] = {}
    for r in rows:
        by_bucket.setdefault(r["audit_bucket"], []).append(r)
    sel: dict[str, dict] = {}

    def take(bucket: str, key, n: int):
        for r in sorted(by_bucket.get(bucket, []), key=key)[:n]:
            sel.setdefault(r["card_id"], r)

    take("MISSED_IDIO_MOVE", lambda r: -_abs(r["excess_pct"]), per_set)
    take("MISSED_DIRECTIONAL_MOVE", lambda r: -_abs(r["ret_pct"]), per_set)
    take("BETA_BLIND_MOVE", lambda r: -_abs(r["ret_pct"]), correct_n)
    take("VOLATILE_BUT_NO_DIRECTION",
         lambda r: -max(_abs(r["mfe_long_pct"]), _abs(r["mae_long_pct"])), per_set)
    take("CORRECT_NO_GO", lambda r: r["card_id"], correct_n)   # стабильная «случайная» выборка
    return list(sel.values())


# ── чарты (OKX public, read-only) ────────────────────────────────────────────
def fetch_window(inst: str, t0_ms: int, t_end_ms: int) -> list:
    """1H свечи в [t0-6ч, t_end+6ч]: /candles, для старых окон — /history-candles."""
    lo, hi = t0_ms - 6 * 3600_000, t_end_ms + 6 * 3600_000
    for ep, lim in (("candles", "300"), ("history-candles", "100")):
        try:
            r = requests.get(f"https://www.okx.com/api/v5/market/{ep}",
                             params={"instId": inst, "bar": "1H", "limit": lim,
                                     **({"after": str(hi)} if ep == "history-candles" else {})},
                             headers=UA, timeout=20)
            d = r.json()
            rows = [(int(c[0]), float(c[2]), float(c[3]), float(c[4]))
                    for c in (d.get("data") or []) if lo <= int(c[0]) <= hi]
            if rows:
                rows.sort()
                return rows
        except Exception:
            continue
    return []


def render_chart(row: dict, candles: list, out_path: Path, threshold: float) -> bool:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    t0 = _parse_ts(row["ts_utc"])
    t_end = t0 + dt.timedelta(hours=float(row["horizon_hours"] or 0))
    xs = [dt.datetime.fromtimestamp(c[0] / 1000, dt.timezone.utc) for c in candles]
    closes = [c[3] for c in candles]
    p0 = row.get("price_at_decision") or closes[0]

    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.plot(xs, closes, lw=1.2, color="#2962ff")
    ax.axvline(t0, color="#555", ls="--", lw=1)
    ax.axvline(t_end, color="#999", ls=":", lw=1)
    ax.axhline(p0, color="#555", lw=0.8)
    ax.axhspan(p0 * (1 - threshold / 100), p0 * (1 + threshold / 100), color="#888", alpha=0.12)
    horizon = [c for c in candles if t0.timestamp() * 1000 <= c[0] <= t_end.timestamp() * 1000]
    if horizon:
        hi = max(horizon, key=lambda c: c[1])
        lo = min(horizon, key=lambda c: c[2])
        ax.plot(dt.datetime.fromtimestamp(hi[0] / 1000, dt.timezone.utc), hi[1], "^", color="green", ms=7)
        ax.plot(dt.datetime.fromtimestamp(lo[0] / 1000, dt.timezone.utc), lo[2], "v", color="red", ms=7)
    bits = [f"ret={row.get('ret_pct')}%" if row.get("ret_pct") is not None else "",
            f"excess={row.get('excess_pct')}%" if row.get("excess_pct") is not None else ""]
    ax.set_title(f"{row['asset']} · NO_GO · {row['audit_bucket']} · {' '.join(b for b in bits if b)}\n"
                 f"{str(row.get('headline'))[:95]}", fontsize=9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return True


def write_outputs(rows: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "no_go_audit.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(out_dir / "no_go_audit.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def _rate_table(rows: list[dict], key: str, top: int = 12) -> list[str]:
    agg: dict[str, list[int]] = {}
    for r in rows:
        if r["audit_bucket"] in UNSCORED_BUCKETS:
            continue
        k = str(r.get(key))
        cell = agg.setdefault(k, [0, 0])
        cell[0] += 1
        cell[1] += r["audit_bucket"] == "MISSED_IDIO_MOVE"
    ranked = sorted(agg.items(), key=lambda kv: -kv[1][1])[:top]
    return [f"| {k} | {v[1]}/{v[0]} |" for k, v in ranked if v[0]]


def build_summary(rows: list[dict], journal_rows: list[dict], threshold: float) -> str:
    buckets: dict[str, int] = {}
    for r in rows:
        buckets[r["audit_bucket"]] = buckets.get(r["audit_bucket"], 0) + 1
    scored = [r for r in rows if r["audit_bucket"] not in UNSCORED_BUCKETS]
    idio = [r for r in rows if r["audit_bucket"] == "MISSED_IDIO_MOVE"]
    verd: dict[str, int] = {}
    for r in journal_rows:
        verd[str(r.get("verdict"))] = verd.get(str(r.get("verdict")), 0) + 1
    chief_ng = [r for r in journal_rows if r.get("chief_called") and r.get("verdict") == "NO_GO"]
    ip_yes = sum(1 for r in chief_ng if str(r.get("in_price")).lower() == "yes")
    lc = [r for r in scored if r.get("low_confidence")]
    lc_idio = sum(1 for r in lc if r["audit_bucket"] == "MISSED_IDIO_MOVE")
    gw = [r for r in scored if "news.google.com" in str(r.get("source_url"))]
    gw_idio = sum(1 for r in gw if r["audit_bucket"] == "MISSED_IDIO_MOVE")
    direct = [r for r in scored if str(r.get("source")) in ("cointelegraph", "decrypt", "oilprice")]
    d_idio = sum(1 for r in direct if r["audit_bucket"] == "MISSED_IDIO_MOVE")

    L = [f"# NO_GO аудит · порог {threshold:g}% · {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M}Z", "",
         f"Вердикты журнала: {verd}. NO_GO в аудите: **{len(rows)}**, со скоренным исходом: **{len(scored)}**.",
         f"Бакеты: {json.dumps(buckets, ensure_ascii=False, sort_keys=True)}", "",
         f"- Вероятно ПРАВИЛЬНЫЙ фильтр (CORRECT + VOLATILE-фитили): "
         f"{buckets.get('CORRECT_NO_GO', 0) + buckets.get('VOLATILE_BUT_NO_DIRECTION', 0)}/{len(scored)}",
         f"- РЕАЛЬНЫЕ идиосинкратические промахи: **{len(idio)}/{len(scored)}**",
         f"- Бета/рынок (directional без excess): {buckets.get('MISSED_DIRECTIONAL_MOVE', 0)}/{len(scored)}",
         f"- beta_blind ход (актив=своему baseline, idio не определён): "
         f"{buckets.get('BETA_BLIND_MOVE', 0)}/{len(scored)}",
         f"- chief был недоступен (вне оценки суждения): {buckets.get('CHIEF_ERROR_UNRESOLVED', 0)}", "",
         "## Идио-промахи по источникам (idio/n)", "| источник | idio/n |", "|---|---|",
         *_rate_table(rows, "source"), "",
         "## По слоям", "| слой | idio/n |", "|---|---|", *_rate_table(rows, "layer"), "",
         "## По активам", "| актив | idio/n |", "|---|---|", *_rate_table(rows, "asset"), "",
         "## Концентрации",
         f"- low_confidence (без тела): idio {lc_idio}/{len(lc)} против {len(idio) - lc_idio}/{len(scored) - len(lc)} с телом.",
         f"- Google-обёртки: idio {gw_idio}/{len(gw)}; прямые ленты (cointelegraph/decrypt/oilprice): {d_idio}/{len(direct)}.",
         f"- chief `in_price=yes` на NO_GO: {ip_yes}/{len(chief_ng)} ({100 * ip_yes / max(1, len(chief_ng)):.0f}%).", "",
         "## Топ идио-промахов", "| актив | excess% | источник | заголовок |", "|---|---|---|---|"]
    for r in sorted(idio, key=lambda r: -_abs(r["excess_pct"]))[:15]:
        L.append(f"| {r['asset']} | {r['excess_pct']:+.1f} | {r['source']} | {str(r['headline'])[:70]} |")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=3.0)
    ap.add_argument("--no-charts", action="store_true")
    ap.add_argument("--per-set", type=int, default=20, help="чартов на бакет")
    ap.add_argument("--out", default=str(OUT_BASE))
    args = ap.parse_args()
    out_dir = Path(args.out)
    now = dt.datetime.now(dt.timezone.utc)

    journal_rows = _load_jsonl(JOURNAL)
    rows = build_dataset(journal_rows, _load_jsonl(OUTCOMES), now, args.threshold)
    extra = [r for r in journal_rows if r.get("verdict") in ("WATCH", "GO")]

    if not args.no_charts:
        samples = out_dir / "samples"
        samples.mkdir(parents=True, exist_ok=True)
        todo = select_chart_rows(rows, args.per_set)
        for jr in extra:                              # все WATCH/GO для сравнения
            todo.append({**{k: jr.get(k) for k in FIELDS}, "audit_bucket": jr.get("verdict"),
                         "ret_pct": None, "excess_pct": None,
                         "mfe_long_pct": None, "mae_long_pct": None})
        done = 0
        for r in todo:
            inst, t0 = r.get("okx_inst"), _parse_ts(r.get("ts_utc") or "")
            if not inst or not t0:
                continue
            t_end = t0 + dt.timedelta(hours=float(r.get("horizon_hours") or 24))
            candles = fetch_window(inst, int(t0.timestamp() * 1000), int(min(t_end, now).timestamp() * 1000))
            if len(candles) < 5:
                continue
            name = f"{r['audit_bucket']}_{r['asset']}_{r['card_id']}.png"
            try:
                if render_chart(r, candles, samples / name, args.threshold):
                    r["chart_path"] = f"samples/{name}"
                    done += 1
            except Exception as e:
                print(f"  chart {r['card_id']}: {e}")
        print(f"чартов отрисовано: {done} → {samples}")

    write_outputs(rows, out_dir)
    (out_dir / "summary.md").write_text(build_summary(rows, journal_rows, args.threshold), encoding="utf-8")
    buckets: dict[str, int] = {}
    for r in rows:
        buckets[r["audit_bucket"]] = buckets.get(r["audit_bucket"], 0) + 1
    print(f"NO_GO в аудите: {len(rows)} · бакеты: {json.dumps(buckets, ensure_ascii=False, sort_keys=True)}")
    print(f"отчёт: {out_dir}")


if __name__ == "__main__":
    main()
