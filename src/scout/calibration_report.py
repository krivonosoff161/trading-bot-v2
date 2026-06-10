# -*- coding: utf-8 -*-
"""
calibration_report.py — offline-калибровка вердиктов сканера (read-only, без сети).

Главный вопрос: ГДЕ NO_GO упускает реальное движение и какой гейт это создаёт.
Соединяет scanner_journal.jsonl + scanner_outcomes.jsonl по card_id и считает
по разрезам (source/layer/asset/event_phase/lead_class/chief_called/low_confidence/
materiality-bucket) три вида промаха NO_GO на пороге th:

  vol  — max(|MFE|,|MAE|) >= th   (ход был, любой природы: фитили/бета считаются)
  dir  — |финальный ret| >= th    (направленный ход)
  idio — |excess vs baseline| >= th (идиосинкратика: событие, НЕ бета рынка)

idio — честная метрика реального промаха (Stage 0: «94% missed» оказались бетой).
Дропы ДО карточки (роутер/материальность) — в source_quality_report.py, не здесь.

Запуск:  python src/scout/calibration_report.py [--threshold 3] [--json]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = _ROOT / "logs" / "scout"
JOURNAL = LOG_DIR / "scanner_journal.jsonl"
OUTCOMES = LOG_DIR / "scanner_outcomes.jsonl"

DIMENSIONS = ("source", "layer", "asset", "event_phase", "lead_class",
              "chief_called", "low_confidence", "materiality")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _latest_by_card(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = row.get("card_id")
        if cid:
            out[str(cid)] = row
    return out


def _abs(v) -> float:
    try:
        return abs(float(v))
    except (TypeError, ValueError):
        return 0.0


def classify_miss(outcome: dict[str, Any], threshold: float) -> dict[str, bool | None]:
    """Промахи NO_GO-карточки на пороге threshold (idio=None, если excess не посчитан)."""
    vol = max(_abs(outcome.get("mfe_long_pct")), _abs(outcome.get("mae_long_pct"))) >= threshold
    direct = _abs(outcome.get("ret_pct")) >= threshold
    idio = (None if outcome.get("excess_pct") is None
            else _abs(outcome.get("excess_pct")) >= threshold)
    return {"vol": vol, "dir": direct, "idio": idio}


def _dim_key(dim: str, jrow: dict[str, Any]) -> str:
    if dim == "materiality":
        s = jrow.get("materiality_score")
        if s is None:
            return "none"
        try:
            return f"{min(int(float(s) * 5), 4) * 0.2:.1f}-{min(int(float(s) * 5), 4) * 0.2 + 0.2:.1f}"
        except (TypeError, ValueError):
            return "none"
    if dim == "event_phase":
        return str(jrow.get("event_phase") or "unknown").upper()   # realized/REALIZED → один ключ
    v = jrow.get(dim)
    return "none" if v is None else str(v)


def summarize(*, journal_rows: list[dict] | None = None,
              outcome_rows: list[dict] | None = None,
              threshold: float = 3.0) -> dict[str, Any]:
    journal_rows = journal_rows if journal_rows is not None else _load_jsonl(JOURNAL)
    outcome_rows = outcome_rows if outcome_rows is not None else _load_jsonl(OUTCOMES)
    jix = {str(r.get("card_id")): r for r in journal_rows if r.get("card_id")}
    scored = [o for o in _latest_by_card(outcome_rows).values() if o.get("scored")]

    verdicts = Counter(str(r.get("verdict") or "?") for r in journal_rows)
    chief_no_go = [r for r in journal_rows if r.get("chief_called") and r.get("verdict") == "NO_GO"]
    gate = {
        "verdicts": dict(verdicts),
        "chief_called": dict(Counter(str(r.get("chief_called")) for r in journal_rows)),
        "chief_no_go_in_price": dict(Counter(str(r.get("in_price") or "?") for r in chief_no_go)),
        "chief_no_go_surprise": dict(Counter(str(r.get("surprise") or "?") for r in chief_no_go)),
    }

    no_go = [o for o in scored if o.get("verdict") == "NO_GO"]
    agg = {"n": len(no_go), "vol": 0, "dir": 0, "idio": 0, "idio_unknown": 0}
    by: dict[str, dict[str, dict[str, int]]] = {d: defaultdict(lambda: {"n": 0, "vol": 0, "dir": 0, "idio": 0})
                                                for d in DIMENSIONS}
    for o in no_go:
        miss = classify_miss(o, threshold)
        agg["vol"] += miss["vol"]
        agg["dir"] += miss["dir"]
        agg["idio"] += bool(miss["idio"])
        agg["idio_unknown"] += miss["idio"] is None
        jrow = jix.get(str(o.get("card_id")), {})
        for dim in DIMENSIONS:
            cell = by[dim][_dim_key(dim, jrow)]
            cell["n"] += 1
            cell["vol"] += miss["vol"]
            cell["dir"] += miss["dir"]
            cell["idio"] += bool(miss["idio"])

    return {
        "threshold_pct": threshold,
        "totals": {"journal_cards": len(journal_rows), "scored_outcomes": len(scored)},
        "gate_attribution": gate,
        "no_go": agg,
        "by": {d: {k: dict(v) for k, v in sorted(cells.items())} for d, cells in by.items()},
    }


def render_text(report: dict[str, Any]) -> str:
    th = report["threshold_pct"]
    t, g, n = report["totals"], report["gate_attribution"], report["no_go"]
    lines = [
        f"Карточек в журнале: {t['journal_cards']} · посчитанных исходов: {t['scored_outcomes']} · порог {th:g}%",
        f"Вердикты журнала: {json.dumps(g['verdicts'], ensure_ascii=False, sort_keys=True)}",
        f"chief_called: {json.dumps(g['chief_called'], ensure_ascii=False, sort_keys=True)}",
        f"chief NO_GO in_price: {json.dumps(g['chief_no_go_in_price'], ensure_ascii=False, sort_keys=True)}",
        f"chief NO_GO surprise: {json.dumps(g['chief_no_go_surprise'], ensure_ascii=False, sort_keys=True)}",
        "",
        f"NO_GO scored: n={n['n']} · vol-missed {n['vol']} · dir-missed {n['dir']} · "
        f"IDIO-missed {n['idio']} (без excess: {n['idio_unknown']})",
    ]
    for dim in DIMENSIONS:
        cells = report["by"].get(dim) or {}
        if not cells:
            continue
        lines.append(f"\n— missed NO_GO по {dim} (idio/dir/vol из n):")
        for key, c in sorted(cells.items(), key=lambda kv: (-kv[1]["idio"], -kv[1]["vol"])):
            lines.append(f"  {key:<22} n={c['n']:<3} idio={c['idio']:<3} dir={c['dir']:<3} vol={c['vol']}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=3.0, help="порог промаха в %% (default 3)")
    ap.add_argument("--json", action="store_true", help="JSON вместо текста")
    args = ap.parse_args()
    report = summarize(threshold=args.threshold)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report))


if __name__ == "__main__":
    main()
