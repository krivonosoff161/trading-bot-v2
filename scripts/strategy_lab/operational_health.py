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


def _gate(status: str, message: str, *, action: str = "") -> dict[str, str]:
    return {"status": status, "message": message, "action": action}


def _scanner_llm_ready(scanner: dict[str, Any]) -> bool:
    provider = str(scanner.get("provider") or "").lower()
    if provider == "alibaba":
        return bool(scanner.get("alibaba_key_set"))
    if provider == "yandex":
        return bool(scanner.get("yandex_key_set"))
    return bool(scanner.get("alibaba_key_set") or scanner.get("yandex_key_set"))


def _build_readiness(report: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Operator gates for the current paper/research lifecycle.

    These gates are intentionally conservative. A blocked main consumer is not a
    runtime failure: it is the current safety boundary until a tested consumer exists.
    """
    safety = report["safety"]
    telegram = report["telegram"]
    scanner_llm = report["scanner_llm"]
    lab_llm = report["strategy_lab_llm"]
    journals = report["journals"]
    pfr = report["pfr"]["db"]
    bridge = report["main_bridge"]

    return {
        "auto_trade_off": _gate(
            "pass" if not safety["auto_trade"] else "blocked",
            "AUTO_TRADE is off for paper/research mode."
            if not safety["auto_trade"] else "AUTO_TRADE is enabled; do not run research wrappers.",
            action="Unset AUTO_TRADE before paper/research operation." if safety["auto_trade"] else "",
        ),
        "pfr_source_available": _gate(
            "pass" if pfr["exists"] else "warn",
            "PFR database is available for paper-signal seeding."
            if pfr["exists"] else "PFR database is missing; farm can still run, but PFR seeding is inactive.",
            action="Run the farm/validator first or pass --pfr-db-path." if not pfr["exists"] else "",
        ),
        "paper_signal_store_available": _gate(
            "pass" if journals["paper_signals"]["exists"] else "warn",
            "Paper-signal JSONL store exists."
            if journals["paper_signals"]["exists"] else "No paper-signal store yet; first cycle will create it.",
            action="Run farm_loop with --run-paper-signals." if not journals["paper_signals"]["exists"] else "",
        ),
        "main_instruction_view_available": _gate(
            "pass" if bridge["instruction_view_exists"] else "warn",
            "Main-readable paper instruction view exists."
            if bridge["instruction_view_exists"] else "Main-readable paper instruction view is not built yet.",
            action="Run python -m scripts.strategy_lab.main_paper_bridge." if not bridge["instruction_view_exists"] else "",
        ),
        "main_paper_consumer_available": _gate(
            "pass" if bridge["consumer_view_exists"] else "warn",
            "Paper-only main consumer audit view exists."
            if bridge["consumer_view_exists"] else "Paper-only main consumer audit view is not built yet.",
            action="Run python -m scripts.strategy_lab.main_paper_consumer."
            if not bridge["consumer_view_exists"] else "",
        ),
        "main_runtime_consumer": _gate(
            "planned",
            "Old main/Telegram runtime does not consume farm/PFR paper instructions as an executor.",
            action="Keep it disabled until a separate reviewed runtime adapter exists.",
        ),
        "scanner_telegram_surface": _gate(
            "pass" if telegram["scanner"]["configured"] else "warn",
            "Scanner Telegram channel is configured."
            if telegram["scanner"]["configured"] else "Scanner Telegram channel is not configured.",
            action="Set SCANNER_CHAT_ID only when operator notifications are desired."
            if not telegram["scanner"]["configured"] else "",
        ),
        "paper_telegram_surface": _gate(
            "pass" if telegram["paper"]["configured"] else "warn",
            "Paper Telegram channel is configured."
            if telegram["paper"]["configured"] else "Paper Telegram channel is not configured; paper loop still works.",
            action="Set PAPER_CHAT_ID after paper-alert text/chart review." if not telegram["paper"]["configured"] else "",
        ),
        "scanner_llm_provider": _gate(
            "pass" if _scanner_llm_ready(scanner_llm) else "warn",
            "Scanner LLM provider has a configured key."
            if _scanner_llm_ready(scanner_llm) else "Scanner LLM provider has no matching key configured.",
            action="Set the key for the selected LLM_PROVIDER if scanner analysis is needed."
            if not _scanner_llm_ready(scanner_llm) else "",
        ),
        "strategy_lab_llm_policy": _gate(
            "pass" if not lab_llm["enabled"] or lab_llm["configured"] else "blocked",
            "Strategy Lab LLM is disabled by default or configured with gates."
            if not lab_llm["enabled"] or lab_llm["configured"]
            else "Strategy Lab LLM is enabled but provider is not configured.",
            action="Configure STRATEGY_LAB_LLM_* or turn STRATEGY_LAB_LLM_ENABLED off."
            if lab_llm["enabled"] and not lab_llm["configured"] else "",
        ),
        "journal_rebuild_available": _gate(
            "pass" if journals["excel"]["exists"] else "warn",
            "Excel journal exists and can be rebuilt locally."
            if journals["excel"]["exists"] else "Excel journal does not exist yet.",
            action="Run python scripts/build_journal.py." if not journals["excel"]["exists"] else "",
        ),
        "training_data_exports": _gate(
            "pass"
            if (
                journals["impulse_training"]["exists"]
                or journals["main_impulse_training"]["exists"]
                or journals["paper_signal_training"]["exists"]
            )
            else "warn",
            "At least one training-data export exists."
            if (
                journals["impulse_training"]["exists"]
                or journals["main_impulse_training"]["exists"]
                or journals["paper_signal_training"]["exists"]
            )
            else "No impulse/main-impulse training export is present; paper signals still have their own JSONL.",
            action="Modernize journal/training export after paper outcomes stabilize."
            if not (
                journals["impulse_training"]["exists"]
                or journals["main_impulse_training"]["exists"]
                or journals["paper_signal_training"]["exists"]
            ) else "",
        ),
    }


def collect(*, private_root: Path | None = None, pfr_db_path: Path | None = None) -> dict[str, Any]:
    private_root = private_root or DEFAULT_PRIVATE_ROOT
    provider = load_provider(os.environ)
    pfr_db = pfr_db_path or (private_root / "state" / "strategy_lab.sqlite")
    paper_signal_snapshot = private_root / "state" / "derived" / "paper_signals.json"
    paper_signal_log = private_root / "state" / "derived" / "paper_signals.jsonl"
    paper_signal_training = private_root / "state" / "derived" / "paper_signal_training.jsonl"
    paper_signal_training_snapshot = private_root / "state" / "derived" / "paper_signal_training.json"
    main_paper_instruction_snapshot = private_root / "state" / "derived" / "main_paper_instructions.json"
    main_paper_instruction_log = private_root / "state" / "derived" / "main_paper_instructions.jsonl"
    main_paper_consumed_snapshot = private_root / "state" / "derived" / "main_paper_consumed.json"
    main_paper_consumed_log = private_root / "state" / "derived" / "main_paper_consumed.jsonl"
    main_signal_log = ROOT / "logs" / "signals" / "main_signals.jsonl"
    report = {
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
            "paper_signal_training": _exists(paper_signal_training),
            "paper_signal_training_snapshot": _exists(paper_signal_training_snapshot),
            "main_paper_instructions": _exists(main_paper_instruction_log),
            "main_paper_instruction_snapshot": _exists(main_paper_instruction_snapshot),
            "main_paper_consumed": _exists(main_paper_consumed_log),
            "main_paper_consumed_snapshot": _exists(main_paper_consumed_snapshot),
            "main_signals": _exists(main_signal_log),
        },
        "pfr": {
            "db": _exists(pfr_db),
        },
        "main_bridge": {
            "status": "instruction_view_ready_not_consumed" if main_paper_instruction_snapshot.exists() else "not_connected",
            "paper_sources_ready": paper_signal_snapshot.exists() or paper_signal_log.exists() or pfr_db.exists(),
            "instruction_view_exists": main_paper_instruction_snapshot.exists() or main_paper_instruction_log.exists(),
            "consumer_view_exists": main_paper_consumed_snapshot.exists() or main_paper_consumed_log.exists(),
            "main_signal_log_exists": main_signal_log.exists(),
            "orders_enabled_by_bridge": False,
            "note": (
                "A main-readable paper instruction view may exist, but the Main WS/Telegram "
                "runtime does not consume it yet. No execution is enabled."
            ),
        },
    }
    report["readiness"] = _build_readiness(report)
    return report


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
    print("readiness:")
    for name, gate in report["readiness"].items():
        action = f" action={gate['action']}" if gate.get("action") else ""
        print(f"  {name}: {gate['status']} - {gate['message']}{action}")
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
