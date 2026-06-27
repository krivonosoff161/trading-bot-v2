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
from src.utils.llm_formatter import formatter_provider_status  # noqa: E402
from src.utils.telegram import telegram_status  # noqa: E402


def _exists(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }


def _snapshot_metrics(path: Path, fields: tuple[str, ...]) -> dict[str, Any]:
    metrics: dict[str, Any] = {"path": str(path), "exists": path.exists(), "items": 0, "read_error": ""}
    for field in fields:
        metrics[field] = 0
    if not path.exists():
        return metrics
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        metrics["read_error"] = type(exc).__name__
        return metrics
    if not isinstance(data, dict):
        metrics["read_error"] = "not_object"
        return metrics
    for field in fields:
        value = data.get(field, 0)
        metrics[field] = value if isinstance(value, int) else 0
    items = data.get("items")
    metrics["items"] = len(items) if isinstance(items, list) else 0
    return metrics


def _jsonl_schema_metrics(path: Path, *, schema: str | None = None) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "rows": 0,
        "schema_rows": 0,
        "invalid_json": 0,
        "paper_only_false": 0,
        "read_error": "",
    }
    if not path.exists():
        return metrics
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        metrics["read_error"] = type(exc).__name__
        return metrics
    for line in lines:
        if not line.strip():
            continue
        metrics["rows"] += 1
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            metrics["invalid_json"] += 1
            continue
        if schema is None or row.get("schema") == schema:
            metrics["schema_rows"] += 1
        if row.get("paper_only") is False:
            metrics["paper_only_false"] += 1
    return metrics


def _freshness_metrics(derived: Path, source: Path) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "source_path": str(source),
        "derived_path": str(derived),
        "source_exists": source.exists(),
        "derived_exists": derived.exists(),
        "source_mtime": 0.0,
        "derived_mtime": 0.0,
        "stale_vs_source": False,
        "age_delta_seconds": 0.0,
    }
    if source.exists():
        metrics["source_mtime"] = source.stat().st_mtime
    if derived.exists():
        metrics["derived_mtime"] = derived.stat().st_mtime
    if source.exists() and derived.exists():
        delta = metrics["source_mtime"] - metrics["derived_mtime"]
        metrics["age_delta_seconds"] = round(float(delta), 3)
        metrics["stale_vs_source"] = delta > 1.0
    return metrics


def _gate(status: str, message: str, *, action: str = "") -> dict[str, str]:
    return {"status": status, "message": message, "action": action}


def _surface(path: Path, *, role: str, current: bool, boundary: str) -> dict[str, Any]:
    return {
        **_exists(path),
        "role": role,
        "current": current,
        "boundary": boundary,
    }


def _contains(path: Path, needle: str) -> bool:
    try:
        return needle in path.read_text(encoding="utf-8")
    except OSError:
        return False


def _contains_all(path: Path, needles: tuple[str, ...]) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return all(needle in text for needle in needles)


def _contains_any(path: Path, needles: tuple[str, ...]) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(needle in text for needle in needles)


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
    training_data = report["training_data"]
    pfr = report["pfr"]["db"]
    bridge = report["main_bridge"]
    chain = report["paper_chain"]
    surfaces = report["launch_surfaces"]
    telegram_flow = report["telegram_delivery_flow"]
    llm_boundaries = report["llm_surface_boundaries"]
    paper_chain_ready = (
        chain["instructions"]["instructions"] > 0
        and chain["consumer"]["accepted"] > 0
        and chain["runtime_queue"]["queued"] > 0
        and chain["telegram_preview"]["rendered"] > 0
        and chain["consumer"]["rejected"] == 0
        and chain["runtime_queue"]["invalid"] == 0
        and chain["telegram_preview"]["invalid"] == 0
    )
    runtime_observation_ready = (
        chain["runtime_observation"]["rows_read"] > 0
        and chain["runtime_observation"]["invalid"] == 0
        and chain["runtime_observation"]["provider_error"] == 0
    )
    telegram_ownership_ready = (
        telegram_flow["farm_core_sends_telegram"] is False
        and telegram_flow["paper_sends_telegram_by_default"] is False
        and telegram_flow["execution_authority"] is False
        and telegram_flow["telegram_analyzer_current_for_farm"] is False
        and telegram_flow["telegram_analyzer_requires_auto_execute_opt_in"] is True
        and surfaces["scanner_runtime"]["current"] is True
        and surfaces["legacy_ws_scanner"]["current"] is False
    )
    telegram_analyzer_provider_ready = (
        llm_boundaries["telegram_chart_formatter_uses_llm_provider_env"] is True
        and llm_boundaries["scanner_formatter_provider_mismatch"] is False
    )
    canonical_surface_ready = (
        surfaces["control_room"]["exists"]
        and surfaces["farm_full_cycle_loop"]["exists"]
        and surfaces["farm_full_cycle_stop"]["exists"]
    )
    legacy_loops = report["legacy_loop_boundaries"]
    legacy_loops_guarded = (
        legacy_loops["scanner_farm_loop_current"] is False
        and legacy_loops["universe_farm_loop_current"] is False
        and legacy_loops["scanner_farm_loop_has_abort_guard"]
        and legacy_loops["universe_farm_loop_has_abort_guard"]
    )
    legacy_main_isolated = (
        surfaces["old_main_py"]["exists"]
        and surfaces["old_main_py"]["current"] is False
        and bridge["orders_enabled_by_bridge"] is False
    )
    training_export_ready = (
        journals["impulse_training"]["exists"]
        or journals["main_impulse_training"]["exists"]
        or journals["paper_signal_training"]["exists"]
    )
    paper_training = training_data["paper_signal_training"]
    paper_training_freshness = training_data["paper_signal_training_freshness"]
    paper_training_ready = (
        paper_training["rows"] > 0
        and paper_training["schema_rows"] == paper_training["rows"]
        and paper_training["invalid_json"] == 0
        and paper_training["paper_only_false"] == 0
        and paper_training["read_error"] == ""
        and not paper_training_freshness["stale_vs_source"]
    )
    visible_cycle_blocked = (
        safety["auto_trade"]
        or not canonical_surface_ready
        or not legacy_main_isolated
        or not legacy_loops_guarded
        or not telegram_ownership_ready
        or (lab_llm["enabled"] and not lab_llm["configured"])
    )
    visible_cycle_ready = (
        not visible_cycle_blocked
        and pfr["exists"]
        and paper_chain_ready
        and runtime_observation_ready
        and journals["excel"]["exists"]
        and training_export_ready
        and paper_training_ready
    )

    return {
        "auto_trade_off": _gate(
            "pass" if not safety["auto_trade"] else "blocked",
            "AUTO_TRADE is off for paper/research mode."
            if not safety["auto_trade"] else "AUTO_TRADE is enabled; do not run research wrappers.",
            action="Unset AUTO_TRADE before paper/research operation." if safety["auto_trade"] else "",
        ),
        "canonical_launch_surface": _gate(
            "pass" if canonical_surface_ready else "blocked",
            "Canonical Strategy Lab control-room launch/loop/stop scripts are present."
            if canonical_surface_ready else "Canonical Strategy Lab launch scripts are missing.",
            action="Restore bat/strategy_lab_control_room.bat and farm full-cycle scripts."
            if not canonical_surface_ready else "",
        ),
        "legacy_live_runtime_isolated": _gate(
            "pass" if legacy_main_isolated else "blocked",
            "Old live/order-capable main.py is present but isolated from the farm/PFR paper bridge."
            if legacy_main_isolated else "Old main runtime ownership is ambiguous.",
            action="Do not wire farm/PFR into main.py; use paper runtime observer."
            if not legacy_main_isolated else "",
        ),
        "legacy_loop_guards": _gate(
            "pass" if legacy_loops_guarded else "blocked",
            "Archived farm loops require explicit legacy acknowledgement; canonical loop is farm_loop."
            if legacy_loops_guarded else "Archived farm loop guard is missing or ambiguous.",
            action="Restore ARCHIVE-LEGACY abort guards and use scripts.strategy_lab.farm_loop."
            if not legacy_loops_guarded else "",
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
        "main_paper_runtime_queue_available": _gate(
            "pass" if bridge["runtime_queue_exists"] else "warn",
            "Paper-only main runtime queue exists."
            if bridge["runtime_queue_exists"] else "Paper-only main runtime queue is not built yet.",
            action="Run python -m scripts.strategy_lab.main_paper_runtime_adapter."
            if not bridge["runtime_queue_exists"] else "",
        ),
        "main_paper_runtime_observation_available": _gate(
            "pass" if bridge["runtime_observation_exists"] else "warn",
            "Paper-only main runtime observer has produced a status artifact."
            if bridge["runtime_observation_exists"] else "Paper-only main runtime observer has not run yet.",
            action="Run python -m scripts.strategy_lab.main_paper_runtime --apply."
            if not bridge["runtime_observation_exists"] else "",
        ),
        "paper_chain_counts": _gate(
            "pass" if paper_chain_ready else "warn",
            "Paper chain has non-empty, valid instruction/consumer/runtime/preview counts."
            if paper_chain_ready else "Paper chain is incomplete, empty, or has rejected/invalid rows.",
            action=(
                "Run a bounded farm_loop --run-paper-signals smoke, then rebuild bridge/consumer/runtime/preview."
                if not paper_chain_ready else ""
            ),
        ),
        "paper_runtime_observed": _gate(
            "pass" if runtime_observation_ready else "warn",
            "Paper runtime observer read the queue without invalid/provider errors."
            if runtime_observation_ready else "Paper runtime observer has no clean observation yet.",
            action="Run python -m scripts.strategy_lab.main_paper_runtime --apply after queue rebuild."
            if not runtime_observation_ready else "",
        ),
        "ready_for_visible_paper_research_loop": _gate(
            "pass" if visible_cycle_ready else ("blocked" if visible_cycle_blocked else "warn"),
            "Visible farm/PFR/paper/main-paper/journal cycle is assembled and observed."
            if visible_cycle_ready
            else (
                "A safety or ownership boundary blocks the visible paper/research cycle."
                if visible_cycle_blocked
                else "Visible paper/research cycle is not fully assembled or observed yet."
            ),
            action=(
                "Fix blocked safety/ownership gates first."
                if visible_cycle_blocked
                else (
                    "Run the bounded chain rebuild: farm_loop --run-paper-signals, main paper bridge/"
                    "consumer/runtime/preview, and paper_signal_training_export."
                )
                if not visible_cycle_ready
                else ""
            ),
        ),
        "main_runtime_consumer": _gate(
            "planned",
            "Old live main/Telegram runtime still does not execute farm/PFR paper instructions.",
            action="Keep old main execution disabled; use the paper runtime observer for paper-only lifecycle.",
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
        "paper_telegram_preview_available": _gate(
            "pass" if journals["paper_telegram_preview_snapshot"]["exists"] else "warn",
            "Paper Telegram preview artifact exists and can be reviewed before sending."
            if journals["paper_telegram_preview_snapshot"]["exists"]
            else "Paper Telegram preview artifact is not built yet.",
            action="Run python -m scripts.strategy_lab.paper_telegram_preview."
            if not journals["paper_telegram_preview_snapshot"]["exists"] else "",
        ),
        "paper_telegram_sender_available": _gate(
            "pass" if journals["paper_telegram_delivery_snapshot"]["exists"] else "warn",
            "Paper Telegram delivery audit artifact exists; sender is explicit opt-in."
            if journals["paper_telegram_delivery_snapshot"]["exists"]
            else "Paper Telegram sender has not been dry-run against the preview artifact yet.",
            action="Run python -m scripts.strategy_lab.paper_telegram_sender for dry-run; add --send only after PAPER_CHAT_ID review."
            if not journals["paper_telegram_delivery_snapshot"]["exists"] else "",
        ),
        "telegram_delivery_ownership": _gate(
            "pass" if telegram_ownership_ready else "blocked",
            "Telegram surfaces are separated from farm execution; paper alerts are preview-only by default."
            if telegram_ownership_ready else "Telegram ownership is ambiguous.",
            action="Keep Telegram as a surface; do not import it into farm compute or paper execution."
            if not telegram_ownership_ready else "",
        ),
        "telegram_analyzer_execution_boundary": _gate(
            "pass" if telegram_flow["telegram_analyzer_requires_auto_execute_opt_in"] else "blocked",
            (
                "Legacy Telegram analyzer is explicitly not the farm/PFR launcher; old "
                "execution-adjacent paths require explicit auto-execute opt-in and are isolated "
                "from the paper loop."
            )
            if telegram_flow["telegram_analyzer_requires_auto_execute_opt_in"]
            else "Legacy Telegram analyzer can reach auto_execute without the explicit Telegram opt-in.",
            action=(
                "Restore TELEGRAM_BOT_ALLOW_AUTO_EXECUTE around scripts.auto_execute imports."
                if not telegram_flow["telegram_analyzer_requires_auto_execute_opt_in"]
                else "Do not use start.bat as a paper/PFR runtime. Use the control room or farm full-cycle loop."
            ),
        ),
        "manual_product_analyzer_boundary": _gate(
            "warn",
            "Manual chart/latest analyzers are product surfaces, not farm/PFR paper runtimes.",
            action=(
                "Audit provider routing, Telegram text, and the double-gated auto_execute path before "
                "reviving manual product delivery."
            ),
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
        "telegram_analyzer_llm_provider_review": _gate(
            "pass" if telegram_analyzer_provider_ready else "warn",
            (
                "Text-only legacy chart analyzer is explicitly opted in to the shared LLM_PROVIDER router."
                if telegram_analyzer_provider_ready
                else "Legacy Telegram chart analyzer uses the Yandex formatter path, not the scanner LLM_PROVIDER router."
            ),
            action=(
                "Premium vision and educational Q&A still need separate provider/prompt review."
                if telegram_analyzer_provider_ready
                else "Audit provider routing before reviving Telegram product delivery."
            ),
        ),
        "product_analyzer_prompt_integrity": _gate(
            "pass"
            if llm_boundaries["telegram_chart_formatter_prompt_integrity"]
            and not llm_boundaries["telegram_chart_formatter_mojibake_detected"]
            else "blocked",
            "Legacy chart formatter prompt is UTF-8 readable and still carries required risk/non-claim markers."
            if llm_boundaries["telegram_chart_formatter_prompt_integrity"]
            and not llm_boundaries["telegram_chart_formatter_mojibake_detected"]
            else "Legacy chart formatter prompt is missing required markers or contains mojibake markers.",
            action="Fix src.utils.llm_formatter before using the product analyzer."
            if (
                not llm_boundaries["telegram_chart_formatter_prompt_integrity"]
                or llm_boundaries["telegram_chart_formatter_mojibake_detected"]
            )
            else "",
        ),
        "journal_rebuild_available": _gate(
            "pass" if journals["excel"]["exists"] else "warn",
            "Excel journal exists and can be rebuilt locally."
            if journals["excel"]["exists"] else "Excel journal does not exist yet.",
            action="Run python scripts/build_journal.py." if not journals["excel"]["exists"] else "",
        ),
        "training_data_exports": _gate(
            "pass" if training_export_ready else "warn",
            "At least one training-data export exists."
            if training_export_ready else "No paper/impulse training export is present yet.",
            action="Modernize journal/training export after paper outcomes stabilize."
            if not training_export_ready else "",
        ),
        "paper_signal_training_export": _gate(
            "pass" if paper_training_ready else "warn",
            "Paper signal training export is current, non-empty, schema-valid, and paper-only."
            if paper_training_ready
            else "Paper signal training export is missing, stale, empty, invalid, or not paper-only.",
            action="Run python -m scripts.strategy_lab.paper_signal_training_export, then rebuild the journal."
            if not paper_training_ready else "",
        ),
    }


def _main_bridge_status(
    *,
    instruction_view_exists: bool,
    consumer_view_exists: bool,
    runtime_queue_exists: bool,
    runtime_observation_exists: bool,
) -> str:
    if runtime_observation_exists:
        return "paper_runtime_observed"
    if runtime_queue_exists:
        return "runtime_queue_ready"
    if consumer_view_exists:
        return "consumer_audit_ready"
    if instruction_view_exists:
        return "instruction_view_ready"
    return "not_connected"


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
    main_paper_runtime_queue_snapshot = private_root / "state" / "derived" / "main_paper_runtime_queue.json"
    main_paper_runtime_queue_log = private_root / "state" / "derived" / "main_paper_runtime_queue.jsonl"
    main_paper_runtime_observation_snapshot = private_root / "state" / "derived" / "main_paper_runtime_observation.json"
    main_paper_runtime_observation_log = private_root / "state" / "derived" / "main_paper_runtime_observation.jsonl"
    paper_telegram_preview_snapshot = private_root / "state" / "derived" / "paper_telegram_preview.json"
    paper_telegram_preview_log = private_root / "state" / "derived" / "paper_telegram_preview.jsonl"
    paper_telegram_delivery_snapshot = private_root / "state" / "derived" / "paper_telegram_delivery.json"
    paper_telegram_delivery_log = private_root / "state" / "derived" / "paper_telegram_delivery.jsonl"
    main_signal_log = ROOT / "logs" / "signals" / "main_signals.jsonl"
    old_main = ROOT / "main.py"
    telegram_bot = ROOT / "scripts" / "telegram_bot.py"
    auto_execute = ROOT / "scripts" / "auto_execute.py"
    scanner_farm_loop = ROOT / "scripts" / "strategy_lab" / "scanner_farm_loop.py"
    universe_farm_loop = ROOT / "scripts" / "strategy_lab" / "universe_farm_loop.py"
    llm_client = ROOT / "src" / "utils" / "llm_client.py"
    llm_formatter = ROOT / "src" / "utils" / "llm_formatter.py"
    analyze_chart = ROOT / "scripts" / "analyze_chart.py"
    run_latest_analysis = ROOT / "scripts" / "run_latest_analysis.py"
    llm_formatter_status = formatter_provider_status()
    llm_formatter_prompt_markers = (
        "\u0422\u044b \u2014 \u0430\u043d\u0430\u043b\u0438\u0442\u0438\u043a",
        "\U0001f4ca \u0421\u0415\u0419\u0427\u0410\u0421 \u041d\u0410 \u0420\u042b\u041d\u041a\u0415",
        "\u041d\u0415 \u0433\u0430\u0440\u0430\u043d\u0442",
        "\u043d\u0435 \u0438\u043d\u0432\u0435\u0441\u0442-\u0440\u0435\u043a\u043e\u043c\u0435\u043d\u0434\u0430\u0446\u0438\u044f",
    )
    mojibake_markers = (
        "\u0420\u045e\u0421\u2039",  # "Ты" when UTF-8 was decoded as cp1251.
        "\u0432\u0402",  # mojibake dash/quotes marker.
        "\u0440\u045f",  # mojibake emoji marker.
    )
    launch_surfaces = {
        "control_room": _surface(
            ROOT / "bat" / "strategy_lab_control_room.bat",
            role="canonical visible operator entrypoint; opens farm loop, dashboard, graph, and status windows",
            current=True,
            boundary="paper/research only",
        ),
        "farm_full_cycle_loop": _surface(
            ROOT / "bat" / "strategy_lab_farm_full_cycle_loop.bat",
            role="canonical one-window Strategy Lab farm loop with --run-paper-signals and PFR bridge",
            current=True,
            boundary="paper/research only; public OKX data; no orders",
        ),
        "farm_full_cycle_stop": _surface(
            ROOT / "bat" / "strategy_lab_farm_full_cycle_stop.bat",
            role="canonical stop-file helper for the farm full-cycle loop",
            current=True,
            boundary="stop signal only",
        ),
        "strategy_lab_start_legacy": _surface(
            ROOT / "bat" / "strategy_lab_start.bat",
            role="legacy standalone Strategy Lab wrapper; not the current full lifecycle",
            current=False,
            boundary="do not use for the current control-room cycle",
        ),
        "telegram_analyzer_start": _surface(
            ROOT / "start.bat",
            role="Telegram analyzer product surface; not the Strategy Lab farm launcher",
            current=False,
            boundary="operator notifications/analysis only; not farm/PFR execution",
        ),
        "manual_chart_analyzer": _surface(
            analyze_chart,
            role="manual/product chart analyzer; writes local report/snapshot/chart and can optionally send Telegram",
            current=False,
            boundary="review before product revival; not a farm/PFR runtime",
        ),
        "manual_latest_analysis": _surface(
            run_latest_analysis,
            role=(
                "interactive wrapper around chart analyzer; can reach AUTO_TRADE-gated "
                "auto_execute only after explicit manual wrapper opt-in"
            ),
            current=False,
            boundary="execution-adjacent manual product tool; never use as farm/PFR launcher",
        ),
        "legacy_product_stack": _surface(
            ROOT / "start_all.bat",
            role="legacy/frozen product stack launcher",
            current=False,
            boundary="do not use for Strategy Lab paper/PFR lifecycle",
        ),
        "old_main_py": _surface(
            old_main,
            role="old live/order-capable runtime; not a farm/PFR paper consumer",
            current=False,
            boundary="must remain isolated from farm/PFR paper instructions",
        ),
        "scanner_runtime": _surface(
            ROOT / "scripts" / "ws" / "ws_main_screener.py",
            role="scanner/news/Telegram intake surface; upstream context, not farm trigger owner",
            current=True,
            boundary="Telegram/LLM surface; no farm/PFR execution authority",
        ),
        "legacy_ws_scanner": _surface(
            ROOT / "scripts" / "ws" / "ws_scanner.py",
            role="legacy scanner surface that imports the OKX client; not the farm trigger owner",
            current=False,
            boundary="diagnostic/history only; do not use as canonical paper/farm path",
        ),
    }
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
        "llm_surface_boundaries": {
            "schema": "llm_surface_boundaries.v1",
            "scanner_provider_router": "src.utils.llm_client",
            "scanner_uses_llm_provider_env": _contains(llm_client, "LLM_PROVIDER"),
            "scanner_supports_alibaba": _contains(llm_client, "ALIBABA_API_KEY"),
            "telegram_chart_formatter": "src.utils.llm_formatter",
            "telegram_chart_formatter_provider": llm_formatter_status["provider_scope"],
            "telegram_chart_formatter_status": llm_formatter_status,
            "telegram_chart_formatter_configured": llm_formatter_status["configured"],
            "telegram_chart_formatter_uses_llm_provider_env": llm_formatter_status["follows_llm_provider_env"],
            "telegram_chart_formatter_uses_budget_guard": llm_formatter_status["budget_guard"],
            "telegram_chart_formatter_prompt_integrity": _contains_all(llm_formatter, llm_formatter_prompt_markers),
            "telegram_chart_formatter_mojibake_detected": _contains_any(llm_formatter, mojibake_markers),
            "scanner_formatter_provider_mismatch": (
                os.getenv("LLM_PROVIDER", "yandex").strip().lower()
                != llm_formatter_status["provider"]
            ),
            "analyze_chart_can_send_telegram": _contains(analyze_chart, "--send-telegram"),
            "analyze_chart_send_default": False,
            "strategy_lab_llm_separate_provider": "src.research_lab.llm_provider",
            "strategy_lab_llm_default_enabled": False,
            "note": (
                "Alibaba/Yandex routing in src.utils.llm_client applies to scanner/advisory calls. "
                "The legacy Telegram chart analyzer uses the Yandex-only formatter path and needs "
                "a separate prompt/provider audit before product revival."
            ),
        },
        "strategy_lab_llm": {
            "enabled": os.getenv("STRATEGY_LAB_LLM_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"},
            "provider_name": getattr(provider, "name", "unknown"),
            "configured": bool(getattr(provider, "configured", False)),
        },
        "launch_surfaces": launch_surfaces,
        "paper_data_flow": {
            "schema": "paper_data_flow.v1",
            "current_owner": "scripts.strategy_lab.farm_loop with --run-paper-signals",
            "selection_priority": [
                "live mover universe ranked by outcome memory",
                "paper_signals active store dedup and lifecycle",
                "PFR database seeding, bounded and scanned after live movers",
                "main_paper_bridge export of active paper signals",
                "main_paper_consumer audit view",
                "main_paper_runtime_adapter queue",
                "main_paper_runtime observer on public candles",
                "paper_telegram_preview surface only; no network send by default",
            ],
            "old_main_py_consumes_farm_pfr": False,
            "execution_allowed": False,
            "telegram_send_default": False,
        },
        "telegram_delivery_flow": {
            "schema": "telegram_delivery_flow.v1",
            "farm_core_sends_telegram": False,
            "paper_sends_telegram_by_default": False,
            "paper_preview_artifact": "state/derived/paper_telegram_preview.json",
            "paper_sender_artifact": "state/derived/paper_telegram_delivery.json",
            "paper_sender_cli": "scripts.strategy_lab.paper_telegram_sender",
            "paper_sender_chat_env": "PAPER_CHAT_ID",
            "scanner_surface_sends_to_subscribers": launch_surfaces["scanner_runtime"]["exists"],
            "telegram_analyzer_surface": "start.bat / scripts.telegram_bot",
            "telegram_analyzer_current_for_farm": False,
            "telegram_analyzer_imports_auto_execute": _contains(telegram_bot, "scripts.auto_execute"),
            "telegram_analyzer_auto_trade_guarded": _contains(auto_execute, "AUTO_TRADE"),
            "telegram_analyzer_requires_auto_execute_opt_in": _contains(
                telegram_bot,
                "TELEGRAM_BOT_ALLOW_AUTO_EXECUTE",
            ),
            "legacy_ws_scanner_uses_okx_client": launch_surfaces["legacy_ws_scanner"]["exists"],
            "scanner_provider_path": "src.utils.llm_client (LLM_PROVIDER: alibaba/yandex)",
            "chart_formatter_path": "src.utils.llm_formatter (Yandex chart formatter)",
            "secrets_printed": False,
            "execution_authority": False,
        },
        "product_analyzer_boundary": {
            "schema": "product_analyzer_boundary.v1",
            "analyze_chart_path": "scripts.analyze_chart",
            "analyze_chart_imports_okx_client": _contains(analyze_chart, "OKXClient"),
            "analyze_chart_reads_okx_credentials": _contains(analyze_chart, "OKX_API_KEY"),
            "analyze_chart_uses_llm_formatter": _contains(analyze_chart, "generate_client_text"),
            "analyze_chart_can_send_telegram": _contains(analyze_chart, "--send-telegram"),
            "analyze_chart_send_default": False,
            "analyze_chart_imports_auto_execute": _contains(analyze_chart, "scripts.auto_execute"),
            "run_latest_analysis_path": "scripts.run_latest_analysis",
            "run_latest_analysis_interactive": _contains(run_latest_analysis, "input("),
            "run_latest_analysis_wraps_analyze_chart": _contains(run_latest_analysis, "from scripts.analyze_chart import run"),
            "run_latest_analysis_imports_auto_execute": _contains(run_latest_analysis, "scripts.auto_execute"),
            "run_latest_analysis_auto_trade_guarded": _contains(run_latest_analysis, "if AUTO_TRADE"),
            "run_latest_analysis_requires_auto_execute_opt_in": _contains(
                run_latest_analysis,
                "RUN_LATEST_ANALYSIS_ALLOW_AUTO_EXECUTE",
            ),
            "safe_for_farm_pfr_runtime": False,
            "note": (
                "The legacy product analyzer is a manual/operator surface. It may read OKX credentials "
                "through OKXClient and run_latest_analysis can reach AUTO_TRADE-gated auto_execute only "
                "after an explicit manual wrapper opt-in. "
                "Keep the farm/PFR paper runtime on the derived paper instruction path."
            ),
        },
        "main_engine_boundary": {
            "schema": "main_engine_boundary.v1",
            "path": str(old_main),
            "exists": old_main.exists(),
            "order_capable": _contains(old_main, "place_market_order"),
            "sets_leverage": _contains(old_main, "set_leverage"),
            "imports_private_okx_client": _contains(old_main, "OKXClient"),
            "imports_telegram_sender": _contains(old_main, "send_message"),
            "consumes_farm_tasks_db": _contains(old_main, "farm_tasks.sqlite"),
            "consumes_strategy_lab_db": _contains(old_main, "strategy_lab.sqlite"),
            "consumes_main_paper_queue": _contains(old_main, "main_paper_runtime_queue"),
            "safe_to_use_as_paper_executor": False,
            "replacement_path": "src.research_lab.main_paper_runtime",
            "note": (
                "Old main.py is a live/demo order-capable engine. The current farm/PFR "
                "paper path stops at the paper runtime observer and must not be wired "
                "into old main.py without a new reviewed executor contract."
            ),
        },
        "legacy_loop_boundaries": {
            "schema": "legacy_loop_boundaries.v1",
            "scanner_farm_loop_current": False,
            "scanner_farm_loop_has_abort_guard": (
                _contains(scanner_farm_loop, "ARCHIVE-LEGACY")
                and _contains(scanner_farm_loop, "i_understand_legacy")
            ),
            "universe_farm_loop_current": False,
            "universe_farm_loop_has_abort_guard": (
                _contains(universe_farm_loop, "ARCHIVE-LEGACY")
                and _contains(universe_farm_loop, "i_understand_legacy")
            ),
            "canonical_replacement": "scripts.strategy_lab.farm_loop",
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
            "main_paper_runtime_queue": _exists(main_paper_runtime_queue_log),
            "main_paper_runtime_queue_snapshot": _exists(main_paper_runtime_queue_snapshot),
            "main_paper_runtime_observation": _exists(main_paper_runtime_observation_log),
            "main_paper_runtime_observation_snapshot": _exists(main_paper_runtime_observation_snapshot),
            "paper_telegram_preview": _exists(paper_telegram_preview_log),
            "paper_telegram_preview_snapshot": _exists(paper_telegram_preview_snapshot),
            "paper_telegram_delivery": _exists(paper_telegram_delivery_log),
            "paper_telegram_delivery_snapshot": _exists(paper_telegram_delivery_snapshot),
            "main_signals": _exists(main_signal_log),
        },
        "training_data": {
            "paper_signal_training": _jsonl_schema_metrics(
                paper_signal_training,
                schema="PaperSignalTrainingRow.v1",
            ),
            "paper_signal_training_freshness": _freshness_metrics(
                paper_signal_training,
                paper_signal_log,
            ),
        },
        "pfr": {
            "db": _exists(pfr_db),
        },
        "main_bridge": {
            "paper_sources_ready": paper_signal_snapshot.exists() or paper_signal_log.exists() or pfr_db.exists(),
            "instruction_view_exists": main_paper_instruction_snapshot.exists() or main_paper_instruction_log.exists(),
            "consumer_view_exists": main_paper_consumed_snapshot.exists() or main_paper_consumed_log.exists(),
            "runtime_queue_exists": (
                main_paper_runtime_queue_snapshot.exists() or main_paper_runtime_queue_log.exists()
            ),
            "runtime_observation_exists": (
                main_paper_runtime_observation_snapshot.exists() or main_paper_runtime_observation_log.exists()
            ),
            "main_signal_log_exists": main_signal_log.exists(),
            "orders_enabled_by_bridge": False,
            "note": (
                "A main-readable paper instruction view may exist, but the Main WS/Telegram "
                "runtime does not consume it yet. No execution is enabled."
            ),
        },
        "paper_chain": {
            "instructions": _snapshot_metrics(main_paper_instruction_snapshot, ("instructions",)),
            "consumer": _snapshot_metrics(main_paper_consumed_snapshot, ("instructions_read", "accepted", "rejected")),
            "runtime_queue": _snapshot_metrics(main_paper_runtime_queue_snapshot, ("rows_read", "queued", "invalid")),
            "runtime_observation": _snapshot_metrics(
                main_paper_runtime_observation_snapshot,
                ("rows_read", "observed", "reviewed", "pending", "invalid", "provider_error"),
            ),
            "telegram_preview": _snapshot_metrics(
                paper_telegram_preview_snapshot,
                ("records_read", "rendered", "invalid"),
            ),
            "telegram_delivery": _snapshot_metrics(
                paper_telegram_delivery_snapshot,
                ("records_read", "eligible", "sent", "skipped", "errors"),
            ),
        },
    }
    bridge = report["main_bridge"]
    bridge["status"] = _main_bridge_status(
        instruction_view_exists=bridge["instruction_view_exists"],
        consumer_view_exists=bridge["consumer_view_exists"],
        runtime_queue_exists=bridge["runtime_queue_exists"],
        runtime_observation_exists=bridge["runtime_observation_exists"],
    )
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
    llm_boundaries = report["llm_surface_boundaries"]
    print(
        "llm_surface_boundaries: "
        f"scanner_router={llm_boundaries['scanner_provider_router']} "
        f"scanner_alibaba={llm_boundaries['scanner_supports_alibaba']} "
        f"telegram_formatter_provider={llm_boundaries['telegram_chart_formatter_provider']} "
        f"telegram_formatter_configured={llm_boundaries['telegram_chart_formatter_configured']} "
        f"telegram_uses_llm_provider={llm_boundaries['telegram_chart_formatter_uses_llm_provider_env']} "
        f"provider_mismatch={llm_boundaries['scanner_formatter_provider_mismatch']} "
        f"prompt_integrity={llm_boundaries['telegram_chart_formatter_prompt_integrity']} "
        f"mojibake={llm_boundaries['telegram_chart_formatter_mojibake_detected']} "
        f"analyze_chart_send_default={llm_boundaries['analyze_chart_send_default']}"
    )
    lab = report["strategy_lab_llm"]
    print(f"strategy_lab_llm: enabled={lab['enabled']} provider={lab['provider_name']} configured={lab['configured']}")
    surfaces = report["launch_surfaces"]
    print(
        "launch_surfaces: "
        f"control_room={surfaces['control_room']['exists']} "
        f"farm_loop={surfaces['farm_full_cycle_loop']['exists']} "
        f"stop={surfaces['farm_full_cycle_stop']['exists']} "
        f"old_main_current={surfaces['old_main_py']['current']}"
    )
    flow = report["paper_data_flow"]
    print(
        "paper_data_flow: "
        f"owner={flow['current_owner']} "
        f"old_main_consumes={flow['old_main_py_consumes_farm_pfr']} "
        f"execution_allowed={flow['execution_allowed']} "
        f"telegram_send_default={flow['telegram_send_default']}"
    )
    delivery = report["telegram_delivery_flow"]
    print(
        "telegram_delivery_flow: "
        f"farm_core_sends={delivery['farm_core_sends_telegram']} "
        f"paper_send_default={delivery['paper_sends_telegram_by_default']} "
        f"paper_sender={delivery['paper_sender_cli']} "
        f"paper_chat_env={delivery['paper_sender_chat_env']} "
        f"scanner_surface={delivery['scanner_surface_sends_to_subscribers']} "
        f"tg_analyzer_farm={delivery['telegram_analyzer_current_for_farm']} "
        f"tg_analyzer_auto_execute={delivery['telegram_analyzer_imports_auto_execute']} "
        f"tg_analyzer_manual_opt_in={delivery['telegram_analyzer_requires_auto_execute_opt_in']} "
        f"legacy_ws_scanner_okx_client={delivery['legacy_ws_scanner_uses_okx_client']} "
        f"execution_authority={delivery['execution_authority']}"
    )
    analyzer = report["product_analyzer_boundary"]
    print(
        "product_analyzer_boundary: "
        f"analyze_okx_client={analyzer['analyze_chart_imports_okx_client']} "
        f"analyze_send_default={analyzer['analyze_chart_send_default']} "
        f"latest_auto_execute={analyzer['run_latest_analysis_imports_auto_execute']} "
        f"latest_auto_trade_guard={analyzer['run_latest_analysis_auto_trade_guarded']} "
        f"latest_manual_opt_in={analyzer['run_latest_analysis_requires_auto_execute_opt_in']} "
        f"safe_for_farm={analyzer['safe_for_farm_pfr_runtime']}"
    )
    main_boundary = report["main_engine_boundary"]
    print(
        "main_engine_boundary: "
        f"order_capable={main_boundary['order_capable']} "
        f"sets_leverage={main_boundary['sets_leverage']} "
        f"private_okx_client={main_boundary['imports_private_okx_client']} "
        f"consumes_farm_db={main_boundary['consumes_farm_tasks_db']} "
        f"consumes_paper_queue={main_boundary['consumes_main_paper_queue']} "
        f"safe_paper_executor={main_boundary['safe_to_use_as_paper_executor']}"
    )
    legacy_loops = report["legacy_loop_boundaries"]
    print(
        "legacy_loop_boundaries: "
        f"scanner_current={legacy_loops['scanner_farm_loop_current']} "
        f"scanner_guard={legacy_loops['scanner_farm_loop_has_abort_guard']} "
        f"universe_current={legacy_loops['universe_farm_loop_current']} "
        f"universe_guard={legacy_loops['universe_farm_loop_has_abort_guard']}"
    )
    print(f"pfr_db: exists={report['pfr']['db']['exists']} path={report['pfr']['db']['path']}")
    bridge = report["main_bridge"]
    print(
        "main_bridge: "
        f"status={bridge['status']} paper_sources_ready={bridge['paper_sources_ready']} "
        f"instruction_view={bridge['instruction_view_exists']} "
        f"runtime_observation={bridge['runtime_observation_exists']} "
        f"main_signal_log={bridge['main_signal_log_exists']} orders_enabled={bridge['orders_enabled_by_bridge']}"
    )
    chain = report["paper_chain"]
    print(
        "paper_chain_counts: "
        f"instructions={chain['instructions']['instructions']} "
        f"accepted={chain['consumer']['accepted']} rejected={chain['consumer']['rejected']} "
        f"queued={chain['runtime_queue']['queued']} invalid_queue={chain['runtime_queue']['invalid']} "
        f"observed={chain['runtime_observation']['observed']} "
        f"reviewed={chain['runtime_observation']['reviewed']} "
        f"runtime_errors={chain['runtime_observation']['invalid'] + chain['runtime_observation']['provider_error']} "
        f"preview={chain['telegram_preview']['rendered']} invalid_preview={chain['telegram_preview']['invalid']}"
    )
    print(
        "paper_telegram_delivery: "
        f"eligible={chain['telegram_delivery']['eligible']} "
        f"sent={chain['telegram_delivery']['sent']} "
        f"skipped={chain['telegram_delivery']['skipped']} "
        f"errors={chain['telegram_delivery']['errors']}"
    )
    training = report["training_data"]["paper_signal_training"]
    freshness = report["training_data"]["paper_signal_training_freshness"]
    print(
        "paper_signal_training: "
        f"rows={training['rows']} schema_rows={training['schema_rows']} "
        f"invalid_json={training['invalid_json']} paper_only_false={training['paper_only_false']} "
        f"stale_vs_source={freshness['stale_vs_source']}"
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
