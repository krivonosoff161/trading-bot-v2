# -*- coding: utf-8 -*-
"""
source_onboarding_report.py — замер онбординга источников (эксперимент «один на слой», 11.06.2026).

Офлайн/read-only: source_registry.yaml + news_buffer.sqlite + scanner_journal/outcomes/
reasoning. Детерминирован на фиксированных входах. Для каждого источника: объёмы,
качество тел, карточки/chief/телега, исходы/idio, стоимость, рекомендация.

Запуск:  python scripts/analysis/source_onboarding_report.py [--all] [--json]
         (--all = все источники реестра; дефолт — только участники онбординга)
Выход:   stdout + reports/source_onboarding/<date>/summary.md (+ raw_metrics.json)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs" / "scout"
DB = ROOT / "data" / "scout" / "news_buffer.sqlite"
REGISTRY = ROOT / "src" / "scout" / "config" / "source_registry.yaml"
OUT_BASE = ROOT / "reports" / "source_onboarding"

IDIO_THRESHOLD_PCT = 3.0

CHECKLIST = """## Чеклист оценки 24–48ч (на источник)

| Критерий | Порог | Действие при провале |
|---|---|---|
| raw_items за 48ч | ≥ 5 (иначе лента мертва/слишком узкая) | needs replacement / observe ещё 48ч |
| full_body rate | ≥ 50% для direct_body | needs parser (extract не берёт страницы) |
| title_only rate | ≤ 40% для direct_body | needs parser / disable |
| chief-вызовы с 100% NO_GO | < 15 вызовов впустую (≈10₽) | поднять пороги слоя / disable |
| полезность | ≥ 1 осмысленная карточка (не пересказ цены) | observe → disable на 2-м окне |
| идемпотентность | нет дублей карточек по одному событию | баг-фикс dedup |

**Роллбэк источника** (1 строка, без кода): в `src/scout/config/source_registry.yaml`
поставить `enabled: false` у источника — следующий проход сканера его не читает.
Проверка: `python -c "from src.scout.router import enabled_sources; print(list(enabled_sources()))"`.
"""


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def load_registry(path: Path = REGISTRY) -> dict[str, dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("sources", {}) or {}


def sqlite_stats(db_path: Path = DB) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not Path(db_path).exists():
        return out
    try:
        con = sqlite3.connect(str(db_path))
    except sqlite3.Error:
        return out
    try:
        q = """
            SELECT r.source_id, COUNT(*),
                   SUM(CASE WHEN r.url NOT LIKE '%news.google.com%' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN m.doc_id IS NOT NULL THEN 1 ELSE 0 END),
                   SUM(CASE WHEN m.extraction_status='ok' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN m.extraction_method='title_only' THEN 1 ELSE 0 END),
                   CAST(AVG(m.text_len) AS INT)
            FROM raw_items r LEFT JOIN machine_docs m ON m.doc_id = r.doc_id
            GROUP BY r.source_id
        """
        try:
            rows = con.execute(q)
        except sqlite3.Error:
            return out
        for s, raw, resolved, docs, full, to, alen in rows:
            out[str(s)] = {"raw_items": int(raw), "resolved_urls": int(resolved or 0),
                           "machine_docs": int(docs or 0), "full_body": int(full or 0),
                           "title_only": int(to or 0), "avg_text_len": alen}
    finally:
        con.close()
    return out


def journal_stats(journal: list[dict], outcomes: list[dict],
                  reasoning: list[dict]) -> dict[str, dict]:
    oc = {str(o.get("card_id")): o for o in outcomes}
    cost_by_card: dict[str, float] = {}
    for r in reasoning:
        cost_by_card[str(r.get("card_id"))] = round(sum(
            float(u.get("cost_rub") or 0.0) for u in (r.get("usage") or [])), 4)
    out: dict[str, dict] = {}
    for j in journal:
        s = str(j.get("source") or "?")
        e = out.setdefault(s, {"cards": 0, "chief_calls": 0, "tg_watch_go": 0, "no_go": 0,
                               "matured_outcomes": 0, "idio_miss": 0, "cost_rub": 0.0})
        e["cards"] += 1
        if j.get("chief_called"):
            e["chief_calls"] += 1
        v = str(j.get("verdict"))
        if v in ("GO", "WATCH"):
            e["tg_watch_go"] += 1
        elif v == "NO_GO":
            e["no_go"] += 1
        e["cost_rub"] = round(e["cost_rub"] + cost_by_card.get(str(j.get("card_id")), 0.0), 2)
        o = oc.get(str(j.get("card_id")))
        if o and o.get("scored"):
            e["matured_outcomes"] += 1
            ex = o.get("excess_pct")
            if v == "NO_GO" and ex is not None and abs(float(ex)) >= IDIO_THRESHOLD_PCT:
                e["idio_miss"] += 1
    return out


def recommend(meta: dict, row: dict) -> str:
    status = str(meta.get("onboarding_status") or "")
    if status in ("needs_key", "needs_provider"):
        return f"needs key/provider ({status})"
    if not meta.get("enabled"):
        return "disabled (роллбэк выполнен или не включался)"
    docs = row.get("machine_docs") or 0
    if docs == 0:
        return "observe (данных ещё нет — рано судить)"
    to_rate = (row.get("title_only") or 0) / docs
    full_rate = (row.get("full_body") or 0) / docs
    if str(meta.get("expected_body")) == "direct_body" and to_rate > 0.4:
        return f"needs parser (title_only {to_rate:.0%} > 40%)"
    chief = row.get("chief_calls") or 0
    useful = (row.get("tg_watch_go") or 0) + (row.get("idio_miss") or 0)
    if chief >= 15 and useful == 0:
        return "disable-кандидат (chief впустую: все вызовы → NO_GO)"
    if full_rate >= 0.5 and docs >= 5:
        return "keep (тела есть, наблюдать сигнальность)"
    return "observe (мало данных для вердикта)"


def build(only_onboarding: bool = True) -> dict:
    registry = load_registry()
    sql = sqlite_stats()
    journal = read_jsonl(LOGS / "scanner_journal.jsonl")
    jstats = journal_stats(journal,
                           read_jsonl(LOGS / "scanner_outcomes.jsonl"),
                           read_jsonl(LOGS / "scanner_reasoning.jsonl"))
    as_of = max([j.get("ts_utc") or "" for j in journal] + [""])
    rows = []
    for name, meta in registry.items():
        if only_onboarding and not meta.get("onboarding_status"):
            continue
        row = {"source": name,
               "layer": ",".join(map(str, meta.get("layers") or [])),
               "enabled": bool(meta.get("enabled")),
               "onboarding_status": meta.get("onboarding_status") or "—",
               "expected_body": meta.get("expected_body") or "—",
               **(sql.get(name) or {"raw_items": 0, "resolved_urls": 0, "machine_docs": 0,
                                    "full_body": 0, "title_only": 0, "avg_text_len": None}),
               **(jstats.get(name) or {"cards": 0, "chief_calls": 0, "tg_watch_go": 0,
                                       "no_go": 0, "matured_outcomes": 0, "idio_miss": 0,
                                       "cost_rub": 0.0})}
        row["idio_miss_rate"] = (round(row["idio_miss"] / row["matured_outcomes"], 3)
                                 if row["matured_outcomes"] else None)
        row["recommendation"] = recommend(meta, row)
        rows.append(row)
    rows.sort(key=lambda r: (r["layer"], r["source"]))
    return {"as_of": as_of, "rows": rows}


def render_md(data: dict) -> str:
    L = [f"# Source onboarding — замер · as_of {data['as_of']}", ""]
    L.append("| source | L | on | статус | body | raw | docs | full | title_only | avg_len "
             "| cards | chief | tg | NO_GO | matured | idio | ₽ | рекомендация |")
    L.append("|" + "---|" * 18)
    for r in data["rows"]:
        L.append(f"| {r['source']} | {r['layer']} | {'✅' if r['enabled'] else '⛔'} "
                 f"| {r['onboarding_status']} | {r['expected_body']} | {r['raw_items']} "
                 f"| {r['machine_docs']} | {r['full_body']} | {r['title_only']} "
                 f"| {r['avg_text_len'] if r['avg_text_len'] is not None else '—'} "
                 f"| {r['cards']} | {r['chief_calls']} | {r['tg_watch_go']} | {r['no_go']} "
                 f"| {r['matured_outcomes']} | {r['idio_miss']} | {r['cost_rub']} "
                 f"| {r['recommendation']} |")
    L += ["", CHECKLIST]
    return "\n".join(L) + "\n"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="все источники реестра, не только онбординг")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--date", default=None, help="имя папки отчёта (дефолт = дата as_of)")
    args = ap.parse_args()
    data = build(only_onboarding=not args.all)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True))
    else:
        print(render_md(data))
    out = OUT_BASE / (args.date or (data["as_of"][:10] or "unknown"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.md").write_text(render_md(data), encoding="utf-8")
    (out / "raw_metrics.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    print(f"report → {out}")


if __name__ == "__main__":
    main()
