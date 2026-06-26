"""Strategy Lab operational preflight.

Read-only status for paper/research operations: Telegram delivery config,
scanner LLM config, Strategy Lab advisory LLM config, journal/training files,
PFR database presence, and main-engine bridge readiness. It never prints secrets
and never calls providers unless the existing llm_health_report is called
separately with --probe-live.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass

from src.research_lab.llm_provider import load_provider  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402
from src.utils.telegram import telegram_status  # noqa: E402


def _exists(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }


def collect(*, private_root: Path | None = None, pfr_db_path: Path | None = None) -> dict[str, Any]:
    private_root = private_root or DEFAULT_PRIVATE_ROOT
    provider = load_provider(os.environ)
    pfr_db = pfr_db_path or (private_root / "state" / "strategy_lab.sqlite")
    paper_signal_snapshot = private_root / "state" / "derived" / "paper_signals.json"
    paper_signal_log = private_root / "state" / "derived" / "paper_signals.jsonl"
    main_paper_instruction_snapshot = private_root / "state" / "derived" / "main_paper_instructions.json"
    main_paper_instruction_log = private_root / "state" / "derived" / "main_paper_instructions.jsonl"
    main_signal_log = ROOT / "logs" / "signals" / "main_signals.jsonl"
    return {
        "mode": "paper_research_only",
        "safety": {
            "auto_trade": os.getenv("AUTO_TRADE", "").strip().lower() in {"1", "true", "yes", "on"},
            "orders_enabled_by_this_report": False,
            "prints_secrets": False,
        },
        "telegram": {
            "default": telegram_status(),
            "paper": telegram_status(chat_env="PAPER_CHAT_ID"),
            "scanner": telegram_status(chat_env="SCANNER_CHAT_ID"),
        },
        "scanner_llm": {
            "provider": os.getenv("LLM_PROVIDER", "yandex").strip().lower(),
            "alibaba_key_set": bool(os.getenv("ALIBABA_API_KEY", "").strip()),
            "yandex_key_set": bool(os.getenv("YANDEX_API_KEY", "").strip()),
            "cheap_model": os.getenv("LLM_CHEAP_MODEL", ""),
            "chief_model": os.getenv("LLM_CHIEF_MODEL", ""),
        },
        "strategy_lab_llm": {
            "enabled": os.getenv("STRATEGY_LAB_LLM_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"},
            "provider_name": getattr(provider, "name", "unknown"),
            "configured": bool(getattr(provider, "configured", False)),
        },
        "journals": {
            "excel": _exists(ROOT / "scripts" / "journal.xlsx"),
            "impulse_training": _exists(ROOT / "logs" / "impulse_pump" / "impulse_pump_training.jsonl"),
            "main_impulse_training": _exists(ROOT / "logs" / "main_impulse" / "main_impulse_training.jsonl"),
            "paper_signals": _exists(paper_signal_log),
            "paper_signal_snapshot": _exists(paper_signal_snapshot),
            "main_paper_instructions": _exists(main_paper_instruction_log),
            "main_paper_instruction_snapshot": _exists(main_paper_instruction_snapshot),
            "main_signals": _exists(main_signal_log),
        },
        "pfr": {
            "db": _exists(pfr_db),
        },
        "main_bridge": {
            "status": "instruction_view_ready_not_consumed" if main_paper_instruction_snapshot.exists() else "not_connected",
            "paper_sources_ready": paper_signal_snapshot.exists() or paper_signal_log.exists() or pfr_db.exists(),
            "instruction_view_exists": main_paper_instruction_snapshot.exists() or main_paper_instruction_log.exists(),
            "main_signal_log_exists": main_signal_log.exists(),
            "orders_enabled_by_bridge": False,
            "note": (
                "A main-readable paper instruction view may exist, but the Main WS/Telegram "
                "runtime does not consume it yet. No execution is enabled."
            ),
        },
    }


def _print_human(report: dict[str, Any]) -> None:
    print("Strategy Lab operational preflight")
    print(f"mode={report['mode']} auto_trade={report['safety']['auto_trade']}")
    tg = report["telegram"]
    print(
        "telegram: "
        f"default={tg['default']['configured']}({tg['default']['chat_ids_count']}) "
        f"paper={tg['paper']['configured']}({tg['paper']['chat_ids_count']}) "
        f"scanner={tg['scanner']['configured']}({tg['scanner']['chat_ids_count']})"
    )
    llm = report["scanner_llm"]
    print(
        "scanner_llm: "
        f"provider={llm['provider']} alibaba_key={llm['alibaba_key_set']} "
        f"yandex_key={llm['yandex_key_set']}"
    )
    lab = report["strategy_lab_llm"]
    print(f"strategy_lab_llm: enabled={lab['enabled']} provider={lab['provider_name']} configured={lab['configured']}")
    print(f"pfr_db: exists={report['pfr']['db']['exists']} path={report['pfr']['db']['path']}")
    bridge = report["main_bridge"]
    print(
        "main_bridge: "
        f"status={bridge['status']} paper_sources_ready={bridge['paper_sources_ready']} "
        f"instruction_view={bridge['instruction_view_exists']} "
        f"main_signal_log={bridge['main_signal_log_exists']} orders_enabled={bridge['orders_enabled_by_bridge']}"
    )
    print("journals:")
    for name, item in report["journals"].items():
        print(f"  {name}: exists={item['exists']} bytes={item['size_bytes']} path={item['path']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    ap.add_argument("--pfr-db-path", type=Path, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = collect(private_root=args.private_root, pfr_db_path=args.pfr_db_path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_human(report)


if __name__ == "__main__":
    main()
