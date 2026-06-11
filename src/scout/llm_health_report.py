# -*- coding: utf-8 -*-
"""Offline LLM health/cost report for the news scanner.

Read-only by default. It does not call model APIs unless ``--probe-live`` is
provided explicitly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except Exception:
    pass

from src.utils import llm_budget_guard as B  # noqa: E402
from src.utils import llm_client as L  # noqa: E402

SCOUT_LOG_DIR = _ROOT / "logs" / "scout"
REASONING = SCOUT_LOG_DIR / "scanner_reasoning.jsonl"
BUDGET = SCOUT_LOG_DIR / "llm_budget.jsonl"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def summarize(day: str | None = None) -> dict[str, Any]:
    day = day or B.today_utc()
    budget_rows = _load_jsonl(BUDGET)
    reasoning_rows = _load_jsonl(REASONING)
    daily_budget = [r for r in budget_rows if str(r.get("ts") or "")[:10] == day]
    daily_reasoning = [r for r in reasoning_rows if str(r.get("ts") or r.get("recorded_at") or "")[:10] == day]

    role_model: dict[tuple[str, str, str], list[float]] = defaultdict(lambda: [0, 0, 0.0, 0.0, 0])
    statuses: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    for row in daily_reasoning:
        for usage in row.get("usage") or []:
            key = (
                str(usage.get("provider") or ""),
                str(usage.get("model") or ""),
                str(usage.get("role") or ""),
            )
            role_model[key][0] += 1
            role_model[key][1] += int(usage.get("total_tokens") or 0)
            role_model[key][2] += float(usage.get("cost_usd") or 0.0)
            role_model[key][3] += float(usage.get("cost_rub") or 0.0)
            if usage.get("status") == "budget_skipped":
                role_model[key][4] += 1
            statuses[str(usage.get("status") or "legacy")] += 1
            if usage.get("error_type"):
                errors[str(usage.get("error_type"))] += 1

    totals = {
        "passes": len(daily_budget),
        "cards": sum(int(r.get("n_cards") or 0) for r in daily_budget),
        "dropped": sum(int(r.get("n_dropped") or 0) for r in daily_budget),
        "llm_fail": sum(int(r.get("n_llm_fail") or 0) for r in daily_budget),
        "tokens": sum(int(r.get("total_tokens") or 0) for r in daily_budget),
        "cost_rub": round(sum(float(r.get("cost_rub") or 0.0) for r in daily_budget), 4),
    }
    configured = {
        "provider": L.PROVIDER,
        "cheap_model": L.model_for("cheap"),
        "mid_model": L.model_for("mid"),
        "chief_model": L.model_for("chief"),
        "audit_model": L.model_for("audit"),
        "alibaba_key_set": bool(os.getenv("ALIBABA_API_KEY", "").strip()),
        "yandex_key_set": bool(os.getenv("YANDEX_API_KEY", "").strip()),
        "budget_caps": B.budget_caps(),
        "session": B.session_snapshot(),
    }
    models = [
        {
            "provider": provider,
            "model": model,
            "role": role,
            "calls": int(v[0]),
            "tokens": int(v[1]),
            "cost_usd": round(v[2], 6),
            "cost_rub": round(v[3], 4),
            "budget_skipped": int(v[4]),
        }
        for (provider, model, role), v in sorted(role_model.items(), key=lambda item: item[1][3], reverse=True)
    ]
    return {
        "day": day,
        "configured": configured,
        "totals": totals,
        "models": models,
        "usage_statuses": dict(statuses),
        "errors": dict(errors),
        "latest_budget_row": daily_budget[-1] if daily_budget else None,
    }


async def _probe_live() -> list[dict[str, Any]]:
    results = []
    for role in ("cheap", "chief"):
        text, usage = await L.call(role, "Return JSON only.", 'Return {"ok":true}.', json_mode=True, max_tokens=8, timeout=30)
        results.append({
            "role": role,
            "ok": bool(text),
            "usage": usage,
            "text_preview": (text or "")[:80],
        })
    return results


def _print_human(report: dict[str, Any]) -> None:
    c = report["configured"]
    t = report["totals"]
    print(f"LLM health for {report['day']}")
    print(f"provider={c['provider']} cheap={c['cheap_model']} chief={c['chief_model']}")
    print(f"keys: alibaba={c['alibaba_key_set']} yandex={c['yandex_key_set']}")
    print(f"caps: {json.dumps(c['budget_caps'], ensure_ascii=False, sort_keys=True)}")
    print(f"today: passes={t['passes']} cards={t['cards']} tokens={t['tokens']} cost_rub={t['cost_rub']}")
    print(f"errors: {json.dumps(report['errors'], ensure_ascii=False, sort_keys=True)}")
    if report["models"]:
        print("models:")
        for row in report["models"]:
            print(
                f"  {row['role']:5s} {row['model']} calls={row['calls']} "
                f"tokens={row['tokens']} rub={row['cost_rub']} skipped={row['budget_skipped']}"
            )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=None, help="UTC day YYYY-MM-DD; default=today")
    ap.add_argument("--json", action="store_true", help="Print JSON report")
    ap.add_argument("--probe-live", action="store_true", help="Make one tiny cheap/chief API probe")
    args = ap.parse_args()
    report = summarize(args.day)
    if args.probe_live:
        report["probe_live"] = asyncio.run(_probe_live())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_human(report)
        if args.probe_live:
            print("probe:")
            for row in report["probe_live"]:
                usage = row["usage"]
                print(f"  {row['role']}: ok={row['ok']} status={usage.get('status')} error={usage.get('error_type')}")


if __name__ == "__main__":
    main()
