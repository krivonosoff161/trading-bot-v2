# -*- coding: utf-8 -*-
"""
scanner_deep_audit.py — офлайн-аудит сканера после пайплайн-правок 10.06.2026
(google-резолвер · cheap pre-verdict гейт · телега GO/WATCH-only · новый chief-промпт).

Read-only: читает logs/scout/*.jsonl + data/scout/news_buffer.sqlite, НИЧЕГО в проде
не меняет. Детерминирован: никакого now(), "as_of" = максимальный timestamp в логах;
повторный запуск на тех же данных даёт байт-в-байт тот же результат.

Пишет в reports/scanner_deep_audit/<date>/:
  telegram_cards.md · no_go_outcomes.md · routing_audit.md · source_quality.md ·
  cost_audit.md · raw_metrics.json
(summary.md и recommendations.md — рукописные, поверх этих цифр.)

Запуск:  python scripts/analysis/scanner_deep_audit.py [--threshold 3.0] [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs" / "scout"
DB = ROOT / "data" / "scout" / "news_buffer.sqlite"
OUT_BASE = ROOT / "reports" / "scanner_deep_audit"

# Границы периодов = UTC-времена коммитов (git log --format=%cI):
#   4b8e365 калибровка промпта/телега-гейт  2026-06-10T05:09:27+03:00
#   5931c15 cheap pre-verdict гейт           2026-06-10T20:30:29+03:00
CAL_TS = "2026-06-10T02:09:27Z"
GATE_TS = "2026-06-10T17:30:29Z"
PERIODS = ("pre_calibration", "calibrated", "new_gate")

BUCKETS = ("MISSED_IDIO_MOVE", "MISSED_DIRECTIONAL_MOVE", "VOLATILE_NO_DIRECTION",
           "CORRECT_NO_GO", "UNSCORED_MANUAL")


# ── загрузка ─────────────────────────────────────────────────────────────────
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


def period_of(ts: str) -> str:
    if not ts:
        return "pre_calibration"
    if ts >= GATE_TS:
        return "new_gate"
    if ts >= CAL_TS:
        return "calibrated"
    return "pre_calibration"


def csort(counter) -> list[tuple]:
    return sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))


def pct(a: int, b: int) -> str:
    return f"{100.0 * a / b:.0f}%" if b else "—"


# ── классификация исходов (та же семантика, что nogo_audit) ──────────────────
def self_baseline(j: dict) -> bool:
    inst = j.get("okx_inst")
    return bool(inst) and inst == j.get("baseline_symbol")


def bucket_of(j: dict, o: dict | None, th: float) -> str:
    if not o or not o.get("scored"):
        return "UNSCORED_MANUAL"
    excess = o.get("excess_pct")
    ret = o.get("ret_pct") or 0.0
    wick = max(abs(o.get("mfe_long_pct") or 0.0), abs(o.get("mae_long_pct") or 0.0))
    if not self_baseline(j) and excess is not None and abs(excess) >= th:
        return "MISSED_IDIO_MOVE"
    if abs(ret) >= th:
        return "MISSED_DIRECTIONAL_MOVE"
    if wick >= th:
        return "VOLATILE_NO_DIRECTION"
    return "CORRECT_NO_GO"


def assess_watch(j: dict, o: dict | None) -> tuple[str, str]:
    """Авто-оценка GO/WATCH карточки. Порог эффекта 1.5% (excess; для self-baseline — raw ret)."""
    if not o or not o.get("scored"):
        return "NEEDS_MORE_DATA", "горизонт не созрел / не скорено"
    eff = (o.get("ret_pct") if self_baseline(j) else o.get("excess_pct")) or 0.0
    metric = "ret(self-baseline)" if self_baseline(j) else "excess"
    side = str(j.get("side") or "none")
    if side == "long":
        good, bad = eff >= 1.5, eff <= -1.5
    elif side == "short":
        good, bad = eff <= -1.5, eff >= 1.5
    else:
        wick = max(abs(o.get("mfe_long_pct") or 0.0), abs(o.get("mae_long_pct") or 0.0))
        good, bad = abs(eff) >= 3.0, wick < 1.5
    why = f"{metric}={eff:+.2f}% side={side}"
    if good:
        return "GOOD_WATCH", why
    if bad:
        return "BAD_WATCH", why
    return "NEUTRAL", why


# ── кросс-таблицы NO_GO ──────────────────────────────────────────────────────
def crosstab(items: list[tuple[dict, str]], keyfn) -> dict[str, dict[str, int]]:
    out: dict[str, Counter] = defaultdict(Counter)
    for j, b in items:
        k = str(keyfn(j))
        out[k][b] += 1
        out[k]["total"] += 1
    return {k: dict(v) for k, v in sorted(out.items(), key=lambda kv: (-kv[1]["total"], kv[0]))}


def tab_md(tab: dict[str, dict[str, int]], title: str, lines: list[str]) -> None:
    lines.append(f"\n### {title}\n")
    lines.append("| ключ | всего | idio | directional | volatile | correct | unscored | idio% |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for k, v in tab.items():
        t = v.get("total", 0)
        idio = v.get("MISSED_IDIO_MOVE", 0)
        lines.append(
            f"| {k} | {t} | {idio} | {v.get('MISSED_DIRECTIONAL_MOVE', 0)} "
            f"| {v.get('VOLATILE_NO_DIRECTION', 0)} | {v.get('CORRECT_NO_GO', 0)} "
            f"| {v.get('UNSCORED_MANUAL', 0)} | {pct(idio, t)} |")


def mat_band(j: dict) -> str:
    m = j.get("materiality_score")
    if m is None:
        return "n/a"
    if m < 0.5:
        return "<0.5"
    if m < 0.65:
        return "0.5–0.65"
    if m < 0.8:
        return "0.65–0.8"
    return ">=0.8"


# ── основной сбор ────────────────────────────────────────────────────────────
def collect(th: float) -> dict:
    journal = read_jsonl(LOGS / "scanner_journal.jsonl")
    reasoning = {r.get("card_id"): r for r in read_jsonl(LOGS / "scanner_reasoning.jsonl")}
    events = {r.get("card_id"): r for r in read_jsonl(LOGS / "scanner_events.jsonl")}
    outcomes: dict[str, dict] = {}
    for o in read_jsonl(LOGS / "scanner_outcomes.jsonl"):
        outcomes[o.get("card_id")] = o  # последняя запись побеждает
    routing = read_jsonl(LOGS / "routing_audit.jsonl")
    budget = read_jsonl(LOGS / "llm_budget.jsonl")
    drops = read_jsonl(LOGS / "drops.jsonl")

    as_of = max([j.get("ts_utc") or "" for j in journal] + [""])

    for j in journal:
        j["_period"] = period_of(j.get("ts_utc") or "")
        j["_outcome"] = outcomes.get(j.get("card_id"))
        j["_bucket"] = bucket_of(j, j["_outcome"], th)
        r = reasoning.get(j.get("card_id")) or {}
        j["_orch"] = r.get("orchestrator") or {}
        j["_agent"] = r.get("agent") or {}
        j["_chief"] = r.get("chief") or {}
        j["_usage"] = r.get("usage") or []
        e = (events.get(j.get("card_id")) or {}).get("extraction") or {}
        j["_extract_method"] = e.get("method") or "unknown"
        j["_gate"] = (j.get("escalation_gate") or j["_orch"].get("escalation_gate")
                      or "(legacy)")

    return {"journal": journal, "routing": routing, "budget": budget, "drops": drops,
            "as_of": as_of, "threshold": th}


# ── фаза 1: инвентаризация ───────────────────────────────────────────────────
def phase1(d: dict) -> dict:
    J = d["journal"]
    m = {"as_of": d["as_of"], "cards_total": len(J),
         "period_bounds": {"calibration_utc": CAL_TS, "new_gate_utc": GATE_TS}}
    m["by_period"] = dict(Counter(j["_period"] for j in J))
    m["by_verdict"] = dict(Counter(j["verdict"] for j in J))
    m["by_layer"] = {str(k): v for k, v in sorted(Counter(j["layer"] for j in J).items())}
    m["by_source"] = dict(csort(Counter(j.get("source") or "?" for j in J)))
    m["by_source_trust"] = dict(csort(Counter(str(j.get("source_trust")) for j in J)))
    m["by_source_class"] = dict(csort(Counter(str(j.get("source_class")) for j in J)))
    m["by_extraction"] = dict(csort(Counter(j["_extract_method"] for j in J)))
    m["low_confidence"] = sum(1 for j in J if j.get("low_confidence"))
    per = {}
    for p in PERIODS:
        rows = [j for j in J if j["_period"] == p]
        chief = sum(1 for j in rows if j.get("chief_called") or j["_orch"].get("chief_called"))
        per[p] = {"cards": len(rows), "chief_called": chief,
                  "chief_rate": round(chief / len(rows), 3) if rows else None,
                  "verdicts": dict(Counter(j["verdict"] for j in rows)),
                  "title_only": sum(1 for j in rows if j["_extract_method"] == "title_only")}
    m["per_period"] = per
    return m


# ── фаза 2: telegram-карточки ────────────────────────────────────────────────
def phase2(d: dict) -> list[dict]:
    cards = []
    for j in d["journal"]:
        if j["verdict"] not in ("GO", "WATCH"):
            continue
        o = j["_outcome"]
        label, why = assess_watch(j, o)
        sent = ("sent (post-gate, GO/WATCH-гейт активен)" if (j.get("ts_utc") or "") >= CAL_TS
                else "sent (pre-gate: тогда слались ВСЕ chief-карточки)")
        cards.append({
            "card_id": j["card_id"], "ts_utc": j["ts_utc"], "verdict": j["verdict"],
            "side": j.get("side"), "asset": j["asset"], "layer": j["layer"],
            "source": j.get("source"), "source_url": j.get("source_url"),
            "headline": j.get("headline"), "lead_class": j.get("lead_class"),
            "gate": j["_gate"], "pre_verdict": j["_orch"].get("pre_verdict"),
            "chief_in_price": j["_chief"].get("in_price"),
            "chief_surprise": j["_chief"].get("surprise"),
            "chief_confidence": j["_chief"].get("confidence"),
            "price_at_decision": j.get("price_at_decision"),
            "horizon_hours": j.get("horizon_hours"),
            "low_confidence": j.get("low_confidence"),
            "extract_method": j["_extract_method"], "delivery": sent,
            "outcome": ({k: o.get(k) for k in ("ret_pct", "baseline_ret_pct", "excess_pct",
                                               "mfe_long_pct", "mae_long_pct", "resolved_ts")}
                        if o else None),
            "assessment": label, "assessment_why": why,
        })
    return sorted(cards, key=lambda c: c["ts_utc"])


# ── фаза 3: NO_GO исходы ─────────────────────────────────────────────────────
def phase3(d: dict, th: float) -> dict:
    nogo = [j for j in d["journal"] if j["verdict"] == "NO_GO"]
    scored = [j for j in nogo if j["_bucket"] != "UNSCORED_MANUAL"]
    items = [(j, j["_bucket"]) for j in scored]
    m = {"nogo_total": len(nogo), "nogo_scored": len(scored),
         "nogo_unscored": len(nogo) - len(scored),
         "buckets": dict(Counter(j["_bucket"] for j in scored)),
         "self_baseline_scored": sum(1 for j in scored if self_baseline(j)),
         "threshold_pct": th}
    m["tabs"] = {
        "layer": crosstab(items, lambda j: f"L{j['layer']}"),
        "source": crosstab(items, lambda j: j.get("source") or "?"),
        "event_type": crosstab(items, lambda j: j.get("event_type") or "?"),
        "lead_class": crosstab(items, lambda j: j.get("lead_class") or "?"),
        "extraction": crosstab(items, lambda j: j["_extract_method"]),
        "low_confidence": crosstab(items, lambda j: bool(j.get("low_confidence"))),
        "chief_called": crosstab(items, lambda j: bool(j.get("chief_called")
                                                       or j["_orch"].get("chief_called"))),
        "escalation_gate": crosstab(items, lambda j: j["_gate"]),
        "in_price": crosstab(items, lambda j: j.get("in_price") or "?"),
        "surprise": crosstab(items, lambda j: j.get("surprise") or "?"),
        "materiality": crosstab(items, lambda j: mat_band(j)),
        "source_trust": crosstab(items, lambda j: str(j.get("source_trust"))),
        "period": crosstab(items, lambda j: j["_period"]),
    }

    def miss_row(j):
        o = j["_outcome"]
        return {"card_id": j["card_id"], "ts_utc": j["ts_utc"], "asset": j["asset"],
                "layer": j["layer"], "source": j.get("source"),
                "headline": (j.get("headline") or "")[:120],
                "event_type": j.get("event_type"), "lead_class": j.get("lead_class"),
                "gate": j["_gate"],
                "chief_called": bool(j.get("chief_called") or j["_orch"].get("chief_called")),
                "low_confidence": j.get("low_confidence"),
                "extract_method": j["_extract_method"],
                "in_price": j.get("in_price"), "surprise": j.get("surprise"),
                "ret_pct": o.get("ret_pct"), "excess_pct": o.get("excess_pct"),
                "mfe": o.get("mfe_long_pct"), "mae": o.get("mae_long_pct"),
                "self_baseline": self_baseline(j)}

    idio = [j for j in scored if j["_bucket"] == "MISSED_IDIO_MOVE"]
    idio.sort(key=lambda j: -abs(j["_outcome"].get("excess_pct") or 0))
    m["top_idio_misses"] = [miss_row(j) for j in idio[:15]]

    selfb = [j for j in scored if self_baseline(j)
             and j["_bucket"] == "MISSED_DIRECTIONAL_MOVE"]
    selfb.sort(key=lambda j: -abs(j["_outcome"].get("ret_pct") or 0))
    m["top_self_baseline_directional"] = [miss_row(j) for j in selfb[:10]]

    m["repeat_idio_assets"] = dict(csort(Counter(j["asset"] for j in idio)))
    m["repeat_idio_sources"] = dict(csort(Counter(j.get("source") or "?" for j in idio)))
    m["repeat_idio_event_types"] = dict(csort(Counter(j.get("event_type") or "?" for j in idio)))
    m["repeat_idio_gates"] = dict(csort(Counter(j["_gate"] for j in idio)))
    return m


# ── фаза 4: роутинг и эскалация ──────────────────────────────────────────────
def phase4(d: dict) -> dict:
    J = d["journal"]
    new = [j for j in J if j["_period"] == "new_gate"]
    m = {"new_gate_cards": len(new)}
    m["pre_verdict_dist"] = dict(csort(Counter(
        str(j["_orch"].get("pre_verdict")) for j in new)))
    m["gate_dist"] = dict(csort(Counter(j["_gate"] for j in new)))
    gate_verdict: dict[str, Counter] = defaultdict(Counter)
    gate_tokens: Counter = Counter()
    gate_buckets: dict[str, Counter] = defaultdict(Counter)
    for j in new:
        g = j["_gate"]
        gate_verdict[g][j["verdict"]] += 1
        for u in j["_usage"]:
            if u.get("role") == "chief":
                gate_tokens[g] += u.get("total_tokens") or 0
        if j["verdict"] == "NO_GO":
            gate_buckets[g][j["_bucket"]] += 1
    m["gate_to_verdict"] = {g: dict(v) for g, v in sorted(gate_verdict.items())}
    m["gate_chief_tokens"] = dict(csort(gate_tokens))
    m["gate_to_nogo_bucket"] = {g: dict(v) for g, v in sorted(gate_buckets.items())}
    m["chief_error_fallback"] = [
        {"card_id": j["card_id"], "ts_utc": j["ts_utc"], "asset": j["asset"],
         "headline": (j.get("headline") or "")[:100],
         "original_gate_reason": j["_orch"].get("escalation_reason"),
         "bucket": j["_bucket"]}
        for j in J if j["_gate"] == "CHIEF_ERROR_FALLBACK"]
    skipped = Counter(str(r.get("skipped")) for r in d["routing"] if r.get("skipped"))
    m["routing_skipped"] = dict(csort(skipped))
    m["drops_by_reason"] = dict(csort(Counter(r.get("drop_reason") or "?" for r in d["drops"])))
    # direct vs aggregator по исходам NO_GO
    trust_items = [(j, j["_bucket"]) for j in J
                   if j["verdict"] == "NO_GO" and j["_bucket"] != "UNSCORED_MANUAL"]
    m["nogo_by_trust"] = crosstab(trust_items, lambda j: str(j.get("source_trust")))
    idio = [j for j, b in trust_items if b == "MISSED_IDIO_MOVE"]
    m["idio_title_only"] = sum(1 for j in idio if j["_extract_method"] == "title_only")
    m["idio_total"] = len(idio)
    return m


# ── фаза 5: качество источников/тел ──────────────────────────────────────────
def phase5() -> dict:
    con = sqlite3.connect(str(DB))
    m: dict = {}
    m["raw_by_source_status"] = [
        list(r) for r in con.execute(
            "select source_id, status, count(*) from raw_items group by 1,2 order by 1,2")]
    m["docs_by_method"] = [
        list(r) for r in con.execute(
            "select extraction_method, extraction_status, extraction_quality, count(*) "
            "from machine_docs group by 1,2,3 order by 4 desc, 1")]
    m["google_wrapped_raw"] = con.execute(
        "select count(*) from raw_items where url like '%news.google.com%'").fetchone()[0]
    m["once_google_docs_by_method"] = [
        list(r) for r in con.execute(
            "select m.extraction_method, count(*) from machine_docs m "
            "where m.metadata_json like '%google_news_url%' group by 1 order by 2 desc, 1")]
    m["google_url_state"] = [
        list(r) for r in con.execute(
            "select case when r.url like '%news.google.com%' then 'still_wrapped' "
            "else 'resolved_real_url' end, count(*) from raw_items r "
            "join machine_docs m on m.doc_id=r.doc_id "
            "where m.metadata_json like '%google_news_url%' group by 1 order by 1")]
    m["google_resolve_errors"] = con.execute(
        "select count(*) from machine_docs where metadata_json like '%google_news_url%' "
        "and json_extract(metadata_json,'$.error') is not null").fetchone()[0]
    m["per_source_quality"] = [
        list(r) for r in con.execute(
            "select r.source_id, count(*) n, "
            "sum(case when m.extraction_method='trafilatura' and m.extraction_status='ok' "
            "then 1 else 0 end) full_body, "
            "sum(case when m.extraction_method='title_only' then 1 else 0 end) title_only, "
            "cast(avg(m.text_len) as int) avg_text_len "
            "from raw_items r join machine_docs m on m.doc_id=r.doc_id "
            "group by 1 order by n desc, 1")]
    m["sec_edgar_docs"] = [
        list(r) for r in con.execute(
            "select m.title, m.extraction_method, m.extraction_status, m.text_len "
            "from raw_items r join machine_docs m on m.doc_id=r.doc_id "
            "where r.source_id='sec_edgar' order by m.created_at")]
    con.close()
    return m


# ── фаза 6: токеномика ───────────────────────────────────────────────────────
def phase6(d: dict, tg_cards: list[dict]) -> dict:
    J = d["journal"]
    m: dict = {"per_period": {}}
    for p in PERIODS:
        rows = [j for j in J if j["_period"] == p]
        agg = {"cards": len(rows), "cheap_tokens": 0, "cheap_rub": 0.0,
               "chief_tokens": 0, "chief_rub": 0.0, "chief_calls": 0, "chief_nogo": 0}
        for j in rows:
            chief_used = False
            for u in j["_usage"]:
                role = u.get("role")
                if role == "cheap":
                    agg["cheap_tokens"] += u.get("total_tokens") or 0
                    agg["cheap_rub"] += u.get("cost_rub") or 0.0
                elif role == "chief":
                    chief_used = True
                    agg["chief_tokens"] += u.get("total_tokens") or 0
                    agg["chief_rub"] += u.get("cost_rub") or 0.0
            if chief_used or j.get("chief_called"):
                agg["chief_calls"] += 1
                if j["verdict"] == "NO_GO":
                    agg["chief_nogo"] += 1
        agg["cheap_rub"] = round(agg["cheap_rub"], 2)
        agg["chief_rub"] = round(agg["chief_rub"], 2)
        agg["chief_rate"] = round(agg["chief_calls"] / len(rows), 3) if rows else None
        agg["chief_nogo_rate"] = (round(agg["chief_nogo"] / agg["chief_calls"], 3)
                                  if agg["chief_calls"] else None)
        m["per_period"][p] = agg
    m["budget_by_day"] = {}
    for r in d["budget"]:
        day = (r.get("ts") or "")[:10]
        v = m["budget_by_day"].setdefault(day, {"tokens": 0, "rub": 0.0, "cards": 0, "scans": 0})
        v["tokens"] += r.get("total_tokens") or 0
        v["rub"] = round(v["rub"] + (r.get("cost_rub") or 0.0), 2)
        v["cards"] += r.get("n_cards") or 0
        v["scans"] += 1
    total_rub = round(sum(v["rub"] for v in m["budget_by_day"].values()), 2)
    sent_after_cal = [c for c in tg_cards if c["ts_utc"] >= CAL_TS]
    m["total_rub"] = total_rub
    m["telegram_cards_post_calibration"] = len(sent_after_cal)
    rub_cal = round(sum(v["rub"] for k, v in m["budget_by_day"].items() if k >= CAL_TS[:10]), 2)
    m["rub_since_calibration_days"] = rub_cal
    m["rub_per_telegram_card_since_cal"] = (round(rub_cal / len(sent_after_cal), 2)
                                            if sent_after_cal else None)
    return m


# ── markdown-рендеры ─────────────────────────────────────────────────────────
def render_telegram(cards: list[dict], as_of: str) -> str:
    L = [f"# Telegram-карточки (GO/WATCH) — все за историю · as_of {as_of}", ""]
    L.append(f"Всего GO/WATCH: **{len(cards)}** (GO: "
             f"{sum(1 for c in cards if c['verdict'] == 'GO')}, WATCH: "
             f"{sum(1 for c in cards if c['verdict'] == 'WATCH')})")
    for c in cards:
        L.append("\n---\n")
        L.append(f"## {c['ts_utc']} · {c['verdict']}/{c['side']} · {c['asset']} (L{c['layer']})")
        L.append(f"- headline: {c['headline']}")
        L.append(f"- card_id `{c['card_id']}` · source {c['source']} · lead {c['lead_class']} "
                 f"· gate {c['gate']} · pre_verdict {c['pre_verdict']}")
        L.append(f"- chief: in_price={c['chief_in_price']} surprise={c['chief_surprise']} "
                 f"conf={c['chief_confidence']} · extract={c['extract_method']} "
                 f"low_conf={c['low_confidence']}")
        L.append(f"- price@decision {c['price_at_decision']} · horizon {c['horizon_hours']}h "
                 f"· {c['delivery']}")
        if c["outcome"]:
            o = c["outcome"]
            L.append(f"- outcome: ret {o['ret_pct']:+.2f}% · baseline {o['baseline_ret_pct']:+.2f}% "
                     f"· excess {o['excess_pct']:+.2f}% · mfe {o['mfe_long_pct']:+.2f} "
                     f"mae {o['mae_long_pct']:+.2f} · resolved {o['resolved_ts']}")
        else:
            L.append("- outcome: ещё не созрел (unresolved)")
        L.append(f"- **оценка: {c['assessment']}** ({c['assessment_why']})")
        L.append(f"- url: {c['source_url']}")
    L.append("")
    return "\n".join(L)


def render_nogo(m: dict) -> str:
    L = [f"# NO_GO исходы · порог {m['threshold_pct']}% (excess=idio)", ""]
    L.append(f"NO_GO всего: **{m['nogo_total']}** · скорено {m['nogo_scored']} "
             f"· не скорено/manual {m['nogo_unscored']} "
             f"· self-baseline среди скоренных {m['self_baseline_scored']} "
             f"(для них idio≡0 by construction — смотреть directional)")
    L.append("\n## Бакеты\n")
    for b in BUCKETS:
        n = m["buckets"].get(b, 0)
        L.append(f"- {b}: **{n}** ({pct(n, m['nogo_scored'])})")
    for name, title in [("layer", "По слоям"), ("source", "По источникам"),
                        ("event_type", "По типу события"), ("lead_class", "По lead_class"),
                        ("extraction", "По методу извлечения"),
                        ("low_confidence", "По low_confidence"),
                        ("chief_called", "chief_called vs cheap-only"),
                        ("escalation_gate", "По гейту эскалации"),
                        ("in_price", "По in_price"), ("surprise", "По surprise"),
                        ("materiality", "По материальности"),
                        ("source_trust", "По source_trust"), ("period", "По периоду")]:
        tab_md(m["tabs"][name], title, L)
    L.append("\n## Топ idio-промахи (|excess| ≥ порога, baseline ≠ сам актив)\n")
    for r in m["top_idio_misses"]:
        L.append(f"- `{r['card_id']}` {r['ts_utc']} **{r['asset']}** L{r['layer']} "
                 f"excess {r['excess_pct']:+.2f}% (ret {r['ret_pct']:+.2f}%, mfe {r['mfe']:+.1f}/"
                 f"mae {r['mae']:+.1f}) · {r['source']} · gate {r['gate']} · chief {r['chief_called']} "
                 f"· extract {r['extract_method']} · in_price {r['in_price']} "
                 f"· {r['headline']}")
    L.append("\n## Топ directional на self-baseline (BTC/CL/XAU… — бета-слепые, справочно)\n")
    for r in m["top_self_baseline_directional"]:
        L.append(f"- `{r['card_id']}` {r['ts_utc']} **{r['asset']}** ret {r['ret_pct']:+.2f}% "
                 f"(mfe {r['mfe']:+.1f}/mae {r['mae']:+.1f}) · {r['source']} · {r['headline']}")
    L.append("\n## Повторы среди idio-промахов\n")
    L.append(f"- активы: {m['repeat_idio_assets']}")
    L.append(f"- источники: {m['repeat_idio_sources']}")
    L.append(f"- типы событий: {m['repeat_idio_event_types']}")
    L.append(f"- гейты: {m['repeat_idio_gates']}")
    L.append("")
    return "\n".join(L)


def render_routing(m: dict) -> str:
    L = ["# Роутинг и эскалация (новый гейт-период)", ""]
    L.append(f"Карточек в new_gate периоде: **{m['new_gate_cards']}**")
    L.append(f"\n## pre_verdict (cheap): {m['pre_verdict_dist']}")
    L.append(f"\n## Гейты: {m['gate_dist']}")
    L.append("\n## Гейт → финальный вердикт\n")
    for g, v in m["gate_to_verdict"].items():
        L.append(f"- {g}: {v} · chief-токены {m['gate_chief_tokens'].get(g, 0)}")
    L.append("\n## Гейт → бакет исхода NO_GO (что реально двигалось)\n")
    for g, v in m["gate_to_nogo_bucket"].items():
        L.append(f"- {g}: {v}")
    L.append("\n## CHIEF_ERROR_FALLBACK (chief упал → дешёвый NO_GO)\n")
    if m["chief_error_fallback"]:
        for r in m["chief_error_fallback"]:
            L.append(f"- `{r['card_id']}` {r['ts_utc']} {r['asset']} bucket={r['bucket']} "
                     f"— {r['headline']} (исходный гейт: {r['original_gate_reason']})")
    else:
        L.append("- нет")
    L.append(f"\n## routing_audit skipped: {m['routing_skipped']}")
    L.append(f"\n## drops по причинам: {m['drops_by_reason']}")
    L.append("\n## NO_GO бакеты по source_trust (direct vs aggregator)\n")
    for k, v in m["nogo_by_trust"].items():
        L.append(f"- {k}: {v}")
    L.append(f"\n## title_only среди idio-промахов: {m['idio_title_only']}/{m['idio_total']}")
    L.append("")
    return "\n".join(L)


def render_sources(m: dict) -> str:
    L = ["# Качество источников и тел (news_buffer.sqlite)", ""]
    L.append("## raw_items по источнику/статусу\n")
    L.append("| источник | статус | n |")
    L.append("|---|---|---|")
    for s, st, n in m["raw_by_source_status"]:
        L.append(f"| {s} | {st} | {n} |")
    L.append("\n## machine_docs по методу извлечения\n")
    L.append("| метод | статус | quality | n |")
    L.append("|---|---|---|---|")
    for me, st, q, n in m["docs_by_method"]:
        L.append(f"| {me} | {st} | {q} | {n} |")
    L.append(f"\n## Google-обёртки: raw с news.google.com = {m['google_wrapped_raw']}")
    L.append(f"- докам когда-либо google-wrapped (metadata.google_news_url): "
             f"{m['once_google_docs_by_method']}")
    L.append(f"- состояние URL: {m['google_url_state']}")
    L.append(f"- с ошибкой резолва (metadata.error != null): {m['google_resolve_errors']}")
    L.append("\n## По источнику: полные тела vs title_only\n")
    L.append("| источник | docs | full_body | title_only | full% | avg_text_len |")
    L.append("|---|---|---|---|---|---|")
    for s, n, fb, to, alen in m["per_source_quality"]:
        L.append(f"| {s} | {n} | {fb} | {to} | {pct(fb, n)} | {alen} |")
    L.append("\n## SEC EDGAR доки (official LEADING)\n")
    L.append("| title | метод | статус | text_len |")
    L.append("|---|---|---|---|")
    for t, me, st, ln in m["sec_edgar_docs"]:
        L.append(f"| {str(t)[:70]} | {me} | {st} | {ln} |")
    L.append("")
    return "\n".join(L)


def render_cost(m: dict) -> str:
    L = ["# Токеномика (cheap vs chief)", ""]
    L.append("| период | карточек | cheap ток | cheap ₽ | chief ток | chief ₽ "
             "| chief calls | chief rate | chief NO_GO rate |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for p in PERIODS:
        a = m["per_period"][p]
        L.append(f"| {p} | {a['cards']} | {a['cheap_tokens']} | {a['cheap_rub']} "
                 f"| {a['chief_tokens']} | {a['chief_rub']} | {a['chief_calls']} "
                 f"| {a['chief_rate']} | {a['chief_nogo_rate']} |")
    L.append("\n## Бюджет по дням (llm_budget.jsonl)\n")
    L.append("| день | токены | ₽ | карточек | сканов |")
    L.append("|---|---|---|---|---|")
    for day in sorted(m["budget_by_day"]):
        v = m["budget_by_day"][day]
        L.append(f"| {day} | {v['tokens']} | {v['rub']} | {v['cards']} | {v['scans']} |")
    L.append(f"\n- Всего за историю: **{m['total_rub']} ₽**")
    L.append(f"- С калибровки (10-11.06): {m['rub_since_calibration_days']} ₽ · "
             f"Telegram-карточек после калибровки: {m['telegram_cards_post_calibration']} · "
             f"**₽/полезная карточка: {m['rub_per_telegram_card_since_cal']}**")
    L.append("")
    return "\n".join(L)


# ── main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=3.0)
    ap.add_argument("--date", default=None, help="имя папки отчёта (дефолт = дата as_of)")
    args = ap.parse_args()

    d = collect(args.threshold)
    out = OUT_BASE / (args.date or (d["as_of"][:10] or "unknown"))
    out.mkdir(parents=True, exist_ok=True)

    inv = phase1(d)
    tg = phase2(d)
    nogo = phase3(d, args.threshold)
    routing = phase4(d)
    sources = phase5()
    cost = phase6(d, tg)

    (out / "telegram_cards.md").write_text(render_telegram(tg, d["as_of"]), encoding="utf-8")
    (out / "no_go_outcomes.md").write_text(render_nogo(nogo), encoding="utf-8")
    (out / "routing_audit.md").write_text(render_routing(routing), encoding="utf-8")
    (out / "source_quality.md").write_text(render_sources(sources), encoding="utf-8")
    (out / "cost_audit.md").write_text(render_cost(cost), encoding="utf-8")
    raw = {"inventory": inv, "telegram_cards": tg, "no_go": nogo, "routing": routing,
           "sources": sources, "cost": cost}
    (out / "raw_metrics.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    print(f"as_of {d['as_of']} · cards {inv['cards_total']} · GO/WATCH {len(tg)} "
          f"· NO_GO scored {nogo['nogo_scored']} · idio {nogo['buckets'].get('MISSED_IDIO_MOVE', 0)}")
    print(f"report → {out}")


if __name__ == "__main__":
    main()
