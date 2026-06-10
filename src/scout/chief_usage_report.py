# -*- coding: utf-8 -*-
"""
chief_usage_report.py — офлайн-отчёт «за что платим chief» (read-only, без LLM/сети).

Читает scanner_reasoning.jsonl (+ журнал для source/layer/low_conf) и считает:
вызовы и токены chief по escalation_gate, финальные вердикты по гейтам,
разрез source/layer, доля title-only вызовов, NO_GO-rate chief.
Старые записи (до введения гейта) попадают в бакет "(legacy)".

Запуск:  python src/scout/chief_usage_report.py [--json]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = _ROOT / "logs" / "scout"
REASONING = LOG_DIR / "scanner_reasoning.jsonl"
JOURNAL = LOG_DIR / "scanner_journal.jsonl"


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


def summarize(*, reasoning_rows: list[dict] | None = None,
              journal_rows: list[dict] | None = None) -> dict[str, Any]:
    reasoning_rows = reasoning_rows if reasoning_rows is not None else _load_jsonl(REASONING)
    journal_rows = journal_rows if journal_rows is not None else _load_jsonl(JOURNAL)
    jix = {str(r.get("card_id")): r for r in journal_rows if r.get("card_id")}

    calls_by_gate: Counter = Counter()
    tokens_by_gate: Counter = Counter()
    verdict_by_gate: dict[str, Counter] = defaultdict(Counter)
    by_source: Counter = Counter()
    by_layer: Counter = Counter()
    low_conf_calls = 0
    chief_calls = chief_tokens = cheap_calls = cheap_tokens = 0
    chief_no_go = 0

    for r in reasoning_rows:
        orch = r.get("orchestrator") or {}
        gate = str(orch.get("escalation_gate") or "") or "(legacy)"
        ctoks = sum(int(u.get("total_tokens") or 0) for u in (r.get("usage") or [])
                    if u.get("role") == "chief")
        cheap_calls += sum(1 for u in (r.get("usage") or []) if u.get("role") == "cheap")
        cheap_tokens += sum(int(u.get("total_tokens") or 0) for u in (r.get("usage") or [])
                            if u.get("role") == "cheap")
        if not orch.get("chief_called"):
            continue                                  # гейты без chief — отдельным счётчиком ниже
        chief_calls += 1
        chief_tokens += ctoks
        calls_by_gate[gate] += 1
        tokens_by_gate[gate] += ctoks
        verdict_by_gate[gate][str(orch.get("final_verdict"))] += 1
        if str(orch.get("final_verdict")) == "NO_GO":
            chief_no_go += 1
        j = jix.get(str(r.get("card_id")), {})
        by_source[str(j.get("source"))] += 1
        by_layer[str(j.get("layer"))] += 1
        if j.get("low_confidence"):
            low_conf_calls += 1

    no_chief_gates = Counter()
    for r in reasoning_rows:
        orch = r.get("orchestrator") or {}
        if not orch.get("chief_called"):
            no_chief_gates[str(orch.get("escalation_gate") or "") or "(legacy)"] += 1

    return {
        "cards": len(reasoning_rows),
        "cheap": {"calls": cheap_calls, "tokens": cheap_tokens},
        "chief": {"calls": chief_calls, "tokens": chief_tokens,
                  "call_rate": round(chief_calls / len(reasoning_rows), 3) if reasoning_rows else 0.0,
                  "no_go_rate": round(chief_no_go / chief_calls, 3) if chief_calls else 0.0,
                  "low_confidence_calls": low_conf_calls},
        "chief_calls_by_gate": dict(calls_by_gate.most_common()),
        "chief_tokens_by_gate": dict(tokens_by_gate.most_common()),
        "verdicts_by_gate": {g: dict(c) for g, c in verdict_by_gate.items() if sum(c.values())},
        "no_chief_by_gate": dict(no_chief_gates.most_common()),
        "chief_by_source": dict(by_source.most_common()),
        "chief_by_layer": dict(by_layer.most_common()),
    }


def render_text(rep: dict[str, Any]) -> str:
    ch, cp = rep["chief"], rep["cheap"]
    lines = [
        f"Карточек: {rep['cards']} · cheap {cp['calls']} вызовов/{cp['tokens']} ток · "
        f"chief {ch['calls']} вызовов/{ch['tokens']} ток (rate {ch['call_rate']:.0%}, "
        f"NO_GO-rate {ch['no_go_rate']:.0%}, title-only вызовов {ch['low_confidence_calls']})",
        "",
        "chief-вызовы по гейтам (вызовы · токены · вердикты):",
    ]
    for g, n in rep["chief_calls_by_gate"].items():
        if not n:
            continue
        lines.append(f"  {g:<26} {n:<4} {rep['chief_tokens_by_gate'].get(g, 0):<8} "
                     f"{json.dumps(rep['verdicts_by_gate'].get(g, {}), ensure_ascii=False)}")
    lines += ["", f"без chief по гейтам: {json.dumps(rep['no_chief_by_gate'], ensure_ascii=False)}",
              f"chief по source: {json.dumps(rep['chief_by_source'], ensure_ascii=False)}",
              f"chief по layer: {json.dumps(rep['chief_by_layer'], ensure_ascii=False)}"]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rep = summarize()
    print(json.dumps(rep, ensure_ascii=False, indent=2) if args.json else render_text(rep))


if __name__ == "__main__":
    main()
