"""Run a small real A/B check for Yandex vs Alibaba text providers.

The script writes full raw outputs to logs/llm_provider_ab/ so provider quality,
latency, and cost can be compared without changing product routing.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "logs" / "llm_provider_ab"
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

CASES = [
    {
        "case_id": "education_leverage",
        "role": "mid",
        "system": (
            "Ты учебный помощник по риск-менеджменту. Объясняй простым русским языком. "
            "Не обещай прибыль и не давай персональный финансовый совет."
        ),
        "user": "Как на OKX рассчитывается плечо и почему 20x опаснее 5x?",
    },
    {
        "case_id": "manual_no_trade",
        "role": "chief",
        "system": (
            "Ты форматируешь уже рассчитанный торговый анализ. Не меняй уровни, "
            "не придумывай вход, если статус NO_TRADE."
        ),
        "user": (
            "Пара BTC-USDT-SWAP. Статус NO_TRADE. Причина: цена в середине диапазона, "
            "RR ниже 2, объем слабый. Сформируй короткий ответ пользователю."
        ),
    },
    {
        "case_id": "scanner_watch",
        "role": "chief",
        "system": (
            "Ты объясняешь scanner WATCH карточку. Это не сигнал на вход. "
            "Сохрани уровни и явно скажи, что это наблюдение."
        ),
        "user": (
            "WATCH BICO-USDT-SWAP: движение сильное, входа нет, ждем откат к зоне "
            "0.041-0.043, отмена ниже 0.039."
        ),
    },
]


async def _call_provider(provider: str, case: dict, max_tokens: int) -> dict:
    os.environ["LLM_PROVIDER"] = provider
    from src.utils import llm_client

    importlib.reload(llm_client)
    started = time.perf_counter()
    text, usage = await llm_client.call(
        case["role"],
        case["system"],
        case["user"],
        max_tokens=max_tokens,
        timeout=45,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    return {
        "provider": provider,
        "case_id": case["case_id"],
        "role": case["role"],
        "latency_ms": latency_ms,
        "text": text or "",
        "usage": usage,
        "ok": bool(text),
    }


async def run_ab(*, providers: list[str], max_tokens: int, apply: bool) -> dict:
    load_dotenv()
    rows = []
    for case in CASES:
        for provider in providers:
            rows.append(await _call_provider(provider, case, max_tokens))
    report = {
        "schema": "llm_provider_ab.v1",
        "created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "providers": providers,
        "cases": [c["case_id"] for c in CASES],
        "rows": rows,
    }
    if apply:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = OUT_DIR / f"provider_ab_{stamp}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["path"] = str(path)
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", default="yandex,alibaba")
    ap.add_argument("--max-tokens", type=int, default=500)
    ap.add_argument("--apply", action="store_true", help="write full outputs to logs/llm_provider_ab")
    args = ap.parse_args()
    providers = [p.strip().lower() for p in args.providers.split(",") if p.strip()]
    report = asyncio.run(run_ab(providers=providers, max_tokens=args.max_tokens, apply=args.apply))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
