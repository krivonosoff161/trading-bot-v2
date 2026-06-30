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
from src.utils.llm_formatter import formatter_provider_status, premium_vision_status  # noqa: E402
from src.utils.telegram import telegram_status  # noqa: E402
from scripts.subscriptions import list_delivery_users  # noqa: E402


def _exists(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }


def _paper_subscription_delivery_status() -> dict[str, Any]:
    token_status = telegram_status()
    try:
        users = list_delivery_users()
    except Exception as exc:  # noqa: BLE001 - status must report configuration errors, not crash.
        return {
            "token_set": token_status["token_set"],
            "chat_env": "SUBSCRIPTION_USERS",
            "chat_ids_count": 0,
            "configured": False,
            "delivery_target": "active_subscription_users",
            "load_error": type(exc).__name__,
        }
    count = sum(
        1
        for user in users
        if str(user.get("status") or "").lower() in {"active", "superadmin"}
    )
    return {
        "token_set": token_status["token_set"],
        "chat_env": "SUBSCRIPTION_USERS",
        "chat_ids_count": count,
        "configured": bool(token_status["token_set"] and count),
        "delivery_target": "active_subscription_users",
        "load_error": "",
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


def _snapshot_status_breakdown(path: Path, field: str = "status") -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "items": 0,
        "by_status": {},
        "by_problem": {},
        "read_error": "",
    }
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
    items = data.get("items")
    if not isinstance(items, list):
        return metrics
    metrics["items"] = len(items)
    by_status: dict[str, int] = {}
    by_problem: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        status = str(item.get(field) or "missing")
        by_status[status] = by_status.get(status, 0) + 1
        problem = str(item.get("problem") or "")
        if problem:
            by_problem[problem] = by_problem.get(problem, 0) + 1
    metrics["by_status"] = by_status
    metrics["by_problem"] = by_problem
    return metrics


def _bump_field_count(target: dict[str, dict[str, int]], field: str, value: Any) -> None:
    key = str(value or "missing")
    bucket = target.setdefault(field, {})
    bucket[key] = bucket.get(key, 0) + 1


def _jsonl_field_breakdown(path: Path, fields: tuple[str, ...]) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "rows": 0,
        "invalid_json": 0,
        "by": {field: {} for field in fields},
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
        if not isinstance(row, dict):
            continue
        for field in fields:
            _bump_field_count(metrics["by"], field, row.get(field))
    return metrics


def _snapshot_field_breakdown(path: Path, fields: tuple[str, ...]) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "items": 0,
        "by": {field: {} for field in fields},
        "priority_min": None,
        "priority_max": None,
        "read_error": "",
    }
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
    items = data.get("items")
    if not isinstance(items, list):
        return metrics
    metrics["items"] = len(items)
    priorities: list[int] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for field in fields:
            _bump_field_count(metrics["by"], field, item.get(field))
        priority = item.get("priority")
        if isinstance(priority, int):
            priorities.append(priority)
    if priorities:
        metrics["priority_min"] = min(priorities)
        metrics["priority_max"] = max(priorities)
    return metrics


def _jsonl_schema_metrics(path: Path, *, schema: str | tuple[str, ...] | None = None) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "rows": 0,
        "schema_rows": 0,
        "invalid_json": 0,
        "paper_only_false": 0,
        "execution_allowed_true": 0,
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
        if schema is None:
            metrics["schema_rows"] += 1
        elif isinstance(schema, tuple) and row.get("schema") in schema:
            metrics["schema_rows"] += 1
        elif isinstance(schema, str) and row.get("schema") == schema:
            metrics["schema_rows"] += 1
        if row.get("paper_only") is False:
            metrics["paper_only_false"] += 1
        if row.get("execution_allowed") is True:
            metrics["execution_allowed_true"] += 1
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


def _excel_journal_freshness(root: Path, paper_signal_training: Path) -> dict[str, Any]:
    return _freshness_metrics(root / "scripts" / "journal.xlsx", paper_signal_training)


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


def _section_between(path: Path, start: str, end: str) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    start_idx = text.find(start)
    if start_idx < 0:
        return ""
    end_idx = text.find(end, start_idx + len(start))
    if end_idx < 0:
        return text[start_idx:]
    return text[start_idx:end_idx]


_LEGACY_TEXT_MOJIBAKE_MARKERS = (
    "\u0432\u0402",  # mojibake punctuation/dash marker.
    "\u0432\u045a",
    "\u0432\u045b",
    "\u0440\u045f",  # mojibake emoji marker.
    "\u0420\u045f",
    "\u0420\u0452",
    "\u0420\u2019",
    "\u0420\u0405",
    "\u00c3",
    "\u00c2",
    "\u00e5",
    "\u00cf",
)


def _text_quality_metrics(paths: tuple[Path, ...]) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "schema": "legacy_product_text_quality.v1",
        "files_scanned": 0,
        "files_with_markers": 0,
        "marker_hits": 0,
        "read_errors": 0,
        "clean": True,
        "items": [],
        "non_claim": (
            "This checks operator-facing legacy product text only. It does not check "
            "paper-signal math, farm validation, or the canonical paper/PFR runtime."
        ),
    }
    for path in paths:
        item: dict[str, Any] = {"path": str(path), "exists": path.exists(), "marker_hits": 0, "read_error": ""}
        if not path.exists():
            metrics["items"].append(item)
            continue
        metrics["files_scanned"] += 1
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            item["read_error"] = type(exc).__name__
            metrics["read_errors"] += 1
            metrics["clean"] = False
            metrics["items"].append(item)
            continue
        hits = sum(text.count(marker) for marker in _LEGACY_TEXT_MOJIBAKE_MARKERS)
        item["marker_hits"] = hits
        if hits:
            metrics["files_with_markers"] += 1
            metrics["marker_hits"] += hits
            metrics["clean"] = False
        metrics["items"].append(item)
    return metrics


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
    legacy_text = report["legacy_product_text_quality"]
    product_launch = report["product_analyzer_launch_contract"]
    telegram_delivery_freshness = chain["telegram_delivery_freshness"]
    product_launch_isolated = (
        product_launch["manual_telegram_current_for_farm"] is False
        and product_launch["telegram_bot_main_starts_scanner_loop"] is False
        and product_launch["manual_chart_send_default"] is False
        and product_launch["manual_latest_auto_execute_import_gated"] is True
        and product_launch["farm_pfr_runtime_uses_manual_product_stack"] is False
        and product_launch["old_main_consumes_paper_queue"] is False
        and product_launch["telegram_send_default"] is False
        and product_launch["execution_allowed"] is False
    )
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
        llm_boundaries["telegram_chart_formatter_effective_shared_router"] is True
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
    product_signal_events = training_data["product_signal_events"]
    product_signal_training = training_data["product_signal_training"]
    paper_training_freshness = training_data["paper_signal_training_freshness"]
    product_training_freshness = training_data["product_signal_training_freshness"]
    excel_journal_freshness = training_data["excel_journal_freshness"]
    paper_training_ready = (
        paper_training["rows"] > 0
        and paper_training["schema_rows"] == paper_training["rows"]
        and paper_training["invalid_json"] == 0
        and paper_training["paper_only_false"] == 0
        and paper_training["execution_allowed_true"] == 0
        and paper_training["read_error"] == ""
        and not paper_training_freshness["stale_vs_source"]
    )
    product_signal_events_ready = (
        not journals["product_signal_events"]["exists"]
        or (
            product_signal_events["schema_rows"] == product_signal_events["rows"]
            and product_signal_events["invalid_json"] == 0
            and product_signal_events["paper_only_false"] == 0
            and product_signal_events["execution_allowed_true"] == 0
            and product_signal_events["read_error"] == ""
        )
    )
    product_signal_training_ready = (
        not journals["product_signal_events"]["exists"]
        or (
            journals["product_signal_training"]["exists"]
            and product_signal_training["schema_rows"] == product_signal_training["rows"]
            and product_signal_training["invalid_json"] == 0
            and product_signal_training["paper_only_false"] == 0
            and product_signal_training["execution_allowed_true"] == 0
            and product_signal_training["read_error"] == ""
            and not product_training_freshness["stale_vs_source"]
        )
    )
    excel_journal_current = journals["excel"]["exists"] and not excel_journal_freshness["stale_vs_source"]
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
        and excel_journal_current
        and training_export_ready
        and paper_training_ready
    )
    visible_cycle_needs_journal_rebuild = (
        not visible_cycle_blocked
        and pfr["exists"]
        and paper_chain_ready
        and runtime_observation_ready
        and training_export_ready
        and paper_training_ready
        and not excel_journal_current
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
        "paper_main_runtime_current": _gate(
            "pass" if runtime_observation_ready else "warn",
            "Current main-compatible runtime path is the paper-only observer, not old main.py."
            if runtime_observation_ready
            else "Current main-compatible paper runtime has not produced a clean observation yet.",
            action=(
                "Run main_paper_bridge, main_paper_consumer, main_paper_runtime_adapter, "
                "then main_paper_runtime --apply."
            )
            if not runtime_observation_ready
            else "",
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
                    "Run python -X utf8 scripts/build_journal.py."
                    if visible_cycle_needs_journal_rebuild
                    else (
                    "Run the bounded chain rebuild: farm_loop --run-paper-signals, main paper bridge/"
                    "consumer/runtime/preview, and paper_signal_training_export."
                    )
                )
                if not visible_cycle_ready
                else ""
            ),
        ),
        "main_runtime_consumer": _gate(
            "planned",
            "Old live main.py remains intentionally isolated; paper lifecycle is handled by main_paper_runtime.",
            action="Keep old main execution disabled unless a new reviewed paper executor contract is built.",
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
            "Paper Telegram subscriber delivery is configured."
            if telegram["paper"]["configured"] else "Paper Telegram subscriber delivery is not configured; paper loop still works.",
            action="Configure TELEGRAM_BOT_TOKEN and active subscriptions after paper-alert text/chart review."
            if not telegram["paper"]["configured"] else "",
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
            (
                "pass"
                if (
                    journals["paper_telegram_delivery_snapshot"]["exists"]
                    and not telegram_delivery_freshness["stale_vs_source"]
                )
                else "warn"
            ),
            "Paper Telegram delivery audit artifact exists; sender is explicit opt-in."
            if (
                journals["paper_telegram_delivery_snapshot"]["exists"]
                and not telegram_delivery_freshness["stale_vs_source"]
            )
            else "Paper Telegram delivery audit is older than the preview artifact."
            if telegram_delivery_freshness["stale_vs_source"]
            else "Paper Telegram sender has not been dry-run against the preview artifact yet.",
            action="Run python -m scripts.strategy_lab.paper_telegram_sender for dry-run; add --send only after subscriber delivery review."
            if (
                not journals["paper_telegram_delivery_snapshot"]["exists"]
                or telegram_delivery_freshness["stale_vs_source"]
            )
            else "",
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
                "Audit provider routing, clean legacy Telegram/product text, and keep the double-gated "
                "auto_execute path before reviving manual product delivery."
            ),
        ),
        "product_analyzer_launch_contract": _gate(
            "pass" if product_launch_isolated else "blocked",
            (
                "Product analyzer launch paths are isolated from the canonical farm/PFR paper loop."
                if product_launch_isolated
                else "Product analyzer launch paths are not isolated enough for the current paper loop."
            ),
            action=(
                "Keep farm/PFR launches on bat/strategy_lab_farm_full_cycle_loop.bat."
                if product_launch_isolated
                else "Restore launcher isolation before running the visible paper cycle."
            ),
        ),
        "legacy_product_text_quality": _gate(
            "pass" if legacy_text["clean"] else "warn",
            "Legacy product/Telegram operator text has no known mojibake markers."
            if legacy_text["clean"]
            else (
                "Legacy product/Telegram operator text contains mojibake markers; "
                "farm/PFR is unaffected, but product delivery is not ready."
            ),
            action="Clean or migrate legacy product text before using old Telegram/analyze_chart surfaces."
            if not legacy_text["clean"] else "",
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
                "Text-only legacy chart analyzer is routed through the shared LLM_PROVIDER path by env or launcher."
                if telegram_analyzer_provider_ready
                else "Legacy Telegram chart analyzer uses the Yandex formatter path, not the scanner LLM_PROVIDER router."
            ),
            action=(
                "Premium vision is checked by the separate premium_vision_provider gate."
                if telegram_analyzer_provider_ready
                else "Audit provider routing before reviving Telegram product delivery."
            ),
        ),
        "premium_vision_provider": _gate(
            "pass" if llm_boundaries["premium_vision_configured"] else "warn",
            (
                "VIP screenshot analysis provider is configured via "
                f"{llm_boundaries['premium_vision_provider']}."
            )
            if llm_boundaries["premium_vision_configured"]
            else "VIP screenshot analysis has no configured image-capable provider.",
            action=(
                "Set ALIBABA_API_KEY/ALIBABA_VISION_MODEL or a valid Yandex vision URI "
                "before using VIP screenshots."
            )
            if not llm_boundaries["premium_vision_configured"] else "",
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
            (
                "pass"
                if excel_journal_current
                else "warn"
            ),
            "Excel journal exists and is current against the paper-signal training export."
            if excel_journal_current
            else "Excel journal is missing or older than the paper-signal training export.",
            action="Run python -X utf8 scripts/build_journal.py." if not excel_journal_current else "",
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
        "product_signal_event_log": _gate(
            "pass" if product_signal_events_ready else "warn",
            "Product/manual/VIP signal-event log is absent or schema-valid and paper-only."
            if product_signal_events_ready
            else "Product/manual/VIP signal-event log has invalid rows or execution-enabled rows.",
            action="Inspect logs/signals/signal_events.jsonl before using product events for training."
            if not product_signal_events_ready else "",
        ),
        "product_signal_training_export": _gate(
            "pass" if product_signal_training_ready else "warn",
            "Product/manual/VIP signal events are mirrored into private training rows."
            if product_signal_training_ready
            else "Product/manual/VIP events exist but private training export is missing, stale, invalid, or not paper-only.",
            action="Run python -m scripts.strategy_lab.product_signal_training_export."
            if not product_signal_training_ready else "",
        ),
    }


_INTENTIONAL_BOUNDARY_GATES = frozenset(
    {
        "main_runtime_consumer",
        "manual_product_analyzer_boundary",
    }
)

_OPERATOR_CONFIGURATION_GATES = frozenset(
    {
        "paper_telegram_surface",
        "scanner_telegram_surface",
        "scanner_llm_provider",
        "strategy_lab_llm_policy",
    }
)


def _operator_gate_item(name: str, gate: dict[str, str]) -> dict[str, str]:
    return {
        "name": name,
        "status": gate.get("status", ""),
        "message": gate.get("message", ""),
        "action": gate.get("action", ""),
    }


def _build_operator_next_actions(readiness: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Classify readiness gates into operator-facing action groups."""
    status_counts: dict[str, int] = {}
    blocking: list[dict[str, str]] = []
    operator_configuration: list[dict[str, str]] = []
    intentional_boundaries: list[dict[str, str]] = []
    rebuild_actions: list[dict[str, str]] = []

    for name, gate in readiness.items():
        status = gate.get("status", "")
        status_counts[status] = status_counts.get(status, 0) + 1
        item = _operator_gate_item(name, gate)
        if status == "blocked":
            blocking.append(item)
        if name in _INTENTIONAL_BOUNDARY_GATES:
            intentional_boundaries.append(item)
            continue
        if status in {"warn", "blocked"} and name in _OPERATOR_CONFIGURATION_GATES:
            operator_configuration.append(item)
            continue
        if status == "warn" and gate.get("action"):
            rebuild_actions.append(item)

    return {
        "schema": "operator_next_actions.v1",
        "launch_blocked": bool(blocking),
        "status_counts": status_counts,
        "blocking": blocking,
        "operator_configuration": operator_configuration,
        "intentional_boundaries": intentional_boundaries,
        "rebuild_actions": rebuild_actions,
        "non_claim": (
            "This summary classifies operational gates only. It does not prove a "
            "trading edge and does not authorize live orders."
        ),
    }


def _build_product_analyzer_revival_checklist(report: dict[str, Any]) -> dict[str, Any]:
    """Derived checklist for reviving the manual product analyzer without blurring runtime ownership."""
    llm = report["llm_surface_boundaries"]
    analyzer = report["product_analyzer_boundary"]
    launch = report["product_analyzer_launch_contract"]
    premium_ready = bool(llm["premium_vision_configured"]) and not bool(llm["premium_vision_review_required"])
    text_path_ready = (
        llm["telegram_chart_formatter_prompt_integrity"] is True
        and llm["telegram_chart_formatter_mojibake_detected"] is False
        and llm["telegram_chart_formatter_effective_shared_router"] is True
        and llm["scanner_formatter_provider_mismatch"] is False
    )
    manual_surface_isolated = (
        analyzer["analyze_chart_send_default"] is False
        and analyzer["safe_for_farm_pfr_runtime"] is False
        and launch["manual_latest_auto_execute_import_gated"] is True
        and launch["farm_pfr_runtime_uses_manual_product_stack"] is False
        and launch["old_main_consumes_paper_queue"] is False
        and launch["telegram_send_default"] is False
        and launch["execution_allowed"] is False
    )
    return {
        "schema": "product_analyzer_revival_checklist.v1",
        "status": "review_required",
        "canonical_paper_cycle_allowed": text_path_ready and manual_surface_isolated,
        "manual_product_alerts_allowed": False,
        "live_execution_allowed": False,
        "validated": {
            "text_prompt_integrity": llm["telegram_chart_formatter_prompt_integrity"],
            "text_prompt_no_mojibake": not llm["telegram_chart_formatter_mojibake_detected"],
            "text_cards_use_effective_shared_router": llm["telegram_chart_formatter_effective_shared_router"],
            "premium_vision_provider_configured": premium_ready,
            "scanner_formatter_provider_aligned": not llm["scanner_formatter_provider_mismatch"],
            "manual_chart_send_default_off": analyzer["analyze_chart_send_default"] is False,
            "manual_latest_auto_execute_double_gated": launch["manual_latest_auto_execute_import_gated"],
            "farm_pfr_does_not_use_manual_product_stack": launch["farm_pfr_runtime_uses_manual_product_stack"] is False,
            "old_main_does_not_consume_paper_queue": launch["old_main_consumes_paper_queue"] is False,
        },
        "remaining_review": (
            ([] if premium_ready else ["premium_vision_provider_and_prompt"])
            + [
                "manual_telegram_card_text_and_chart_payload",
                "product_alert_rate_limit_and_dedup",
                "executor_contract_before_any_old_main_reuse",
            ]
        ),
        "allowed_next_step": (
            "Keep using bat/strategy_lab_farm_full_cycle_loop.bat for farm/PFR/paper. "
            "Review generated paper_telegram_preview cards and use paper_telegram_sender "
            "dry-run before any explicit PAPER_CHAT_ID send."
        ),
        "non_claim": (
            "This checklist proves isolation and review status only. It does not prove "
            "trade edge, unattended Telegram readiness, or live execution safety."
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
    product_signal_training = private_root / "state" / "derived" / "product_signal_training.jsonl"
    product_signal_training_snapshot = private_root / "state" / "derived" / "product_signal_training.json"
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
    product_signal_events_log = ROOT / "logs" / "signals" / "signal_events.jsonl"
    old_main = ROOT / "main.py"
    telegram_bot = ROOT / "scripts" / "telegram_bot.py"
    start_bat = ROOT / "start.bat"
    start_telegram_bot_bat = ROOT / "bat" / "start_telegram_bot.bat"
    auto_execute = ROOT / "scripts" / "auto_execute.py"
    scanner_farm_loop = ROOT / "scripts" / "strategy_lab" / "scanner_farm_loop.py"
    universe_farm_loop = ROOT / "scripts" / "strategy_lab" / "universe_farm_loop.py"
    llm_client = ROOT / "src" / "utils" / "llm_client.py"
    llm_formatter = ROOT / "src" / "utils" / "llm_formatter.py"
    analyze_chart = ROOT / "scripts" / "analyze_chart.py"
    run_latest_analysis = ROOT / "scripts" / "run_latest_analysis.py"
    llm_formatter_status = formatter_provider_status()
    premium_status = premium_vision_status()
    product_start_sets_shared_router = _contains(start_bat, "PRODUCT_ANALYZER_LLM_ROUTER=llm_client")
    product_tg_start_sets_shared_router = _contains(
        start_telegram_bot_bat,
        "PRODUCT_ANALYZER_LLM_ROUTER=llm_client",
    )
    product_launcher_sets_shared_router = (
        product_start_sets_shared_router and product_tg_start_sets_shared_router
    )
    chart_formatter_effective_shared_router = (
        llm_formatter_status["shared_router_active"] or product_launcher_sets_shared_router
    )
    chart_formatter_effective_provider = (
        os.getenv("LLM_PROVIDER", "yandex").strip().lower()
        if chart_formatter_effective_shared_router
        else llm_formatter_status["provider"]
    )
    chart_formatter_effective_scope = (
        "shared_llm_client_opt_in"
        if chart_formatter_effective_shared_router
        else llm_formatter_status["provider_scope"]
    )
    chart_formatter_effective_shared_entrypoints = (
        ["generate_client_text", "generate_edu_text"] if chart_formatter_effective_shared_router else []
    )
    telegram_bot_main_body = _section_between(telegram_bot, "async def main() -> None:", "def _setup_rotating_log")
    run_latest_entry_block = _section_between(
        run_latest_analysis,
        'if result and result.get("entry_signal") == "ENTRY":',
        '    print()',
    )
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
            start_bat,
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
            "paper": _paper_subscription_delivery_status(),
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
            "premium_vision_provider_status": premium_status,
            "premium_vision_provider": premium_status["provider"],
            "premium_vision_configured": premium_status["configured"],
            "premium_vision_review_required": premium_status["review_required"],
            "telegram_chart_formatter_configured": llm_formatter_status["configured"],
            "telegram_chart_formatter_uses_llm_provider_env": llm_formatter_status["follows_llm_provider_env"],
            "telegram_chart_formatter_launcher_sets_shared_router": product_launcher_sets_shared_router,
            "telegram_chart_formatter_effective_shared_router": chart_formatter_effective_shared_router,
            "telegram_chart_formatter_effective_provider": chart_formatter_effective_provider,
            "telegram_chart_formatter_effective_provider_scope": chart_formatter_effective_scope,
            "telegram_chart_formatter_effective_shared_entrypoints": chart_formatter_effective_shared_entrypoints,
            "telegram_chart_formatter_uses_budget_guard": llm_formatter_status["budget_guard"],
            "telegram_chart_formatter_prompt_integrity": _contains_all(llm_formatter, llm_formatter_prompt_markers),
            "telegram_chart_formatter_mojibake_detected": _contains_any(llm_formatter, mojibake_markers),
            "scanner_formatter_provider_mismatch": (
                os.getenv("LLM_PROVIDER", "yandex").strip().lower()
                != chart_formatter_effective_provider
            ),
            "analyze_chart_can_send_telegram": _contains(analyze_chart, "--send-telegram"),
            "analyze_chart_send_default": False,
            "strategy_lab_llm_separate_provider": "src.research_lab.llm_provider",
            "strategy_lab_llm_default_enabled": False,
            "note": (
                "Alibaba/Yandex routing in src.utils.llm_client applies to scanner/advisory calls. "
                "Product analyzer text defaults to the legacy formatter in a bare shell, but the "
                "reviewed product launchers route text-only formatter calls through the shared "
                "LLM_PROVIDER path. Premium vision is handled by premium_vision_provider.v1."
            ),
        },
        "legacy_product_text_quality": _text_quality_metrics(
            (
                llm_formatter,
                llm_client,
                analyze_chart,
                run_latest_analysis,
                telegram_bot,
                ROOT / "scripts" / "auto_execute.py",
            )
        ),
        "strategy_lab_llm": {
            "enabled": os.getenv("STRATEGY_LAB_LLM_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"},
            "provider_name": getattr(provider, "name", "unknown"),
            "configured": bool(getattr(provider, "configured", False)),
        },
        "launch_surfaces": launch_surfaces,
        "paper_data_flow": {
            "schema": "paper_data_flow.v1",
            "current_owner": "scripts.strategy_lab.farm_loop with --run-paper-signals",
            "current_main_compatible_runtime": "src.research_lab.main_paper_runtime",
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
        "paper_source_composition": {
            "schema": "paper_source_composition.v1",
            "paper_signals": _jsonl_field_breakdown(
                paper_signal_log,
                ("source", "setup_family", "status", "timeframe"),
            ),
            "main_runtime_queue": _snapshot_field_breakdown(
                main_paper_runtime_queue_snapshot,
                ("source", "setup_family", "timeframe", "runtime_action"),
            ),
            "main_runtime_observation": _snapshot_field_breakdown(
                main_paper_runtime_observation_snapshot,
                ("source", "setup_family", "timeframe", "signal_status"),
            ),
            "priority_contract": [
                "live mover universe is the default paper-signal search lane",
                "PFR is inactive unless --pfr-db-path is provided",
                "pfr_reserved_new only reserves part of max_new; it never enables execution",
                "source is preserved through bridge, consumer, runtime queue, and observation",
                "main_paper_runtime_adapter sorts accepted paper rows by family/timeframe/risk priority",
                "old main.py remains isolated and does not consume the paper queue",
            ],
            "pfr_activation": {
                "requires_explicit_db_path": True,
                "db_path": str(pfr_db),
                "db_exists": pfr_db.exists(),
                "bounded_scan_default": 30,
                "source_name": "pfr_farm",
            },
            "execution_allowed": False,
        },
        "paper_priority_policy": {
            "schema": "paper_priority_policy.v1",
            "live_mover_lane": {
                "order": 1,
                "source": "paper_signals source=farm",
                "owner": "src.research_lab.paper_signals.cycle.run_cycle",
                "rule": "rank live movers by outcome memory, generate first, shared dedup",
            },
            "pfr_lane": {
                "order": 2,
                "source": "paper_signals source=pfr_farm",
                "owner": "src.research_lab.paper_signals.pfr_bridge",
                "requires_explicit_db_path": True,
                "bounded_scan_default": 30,
                "rule": "run after live movers; optional reserved slots; shared dedup and setup-id guard",
            },
            "main_instruction_view": {
                "order": 3,
                "owner": "src.research_lab.main_paper_bridge",
                "active_statuses": ["armed", "opened_paper"],
                "rule": "export active paper signals into main-readable instructions",
            },
            "runtime_queue": {
                "order": 4,
                "owner": "src.research_lab.main_paper_runtime_adapter",
                "sort_order": ["family", "timeframe", "risk", "symbol", "source_signal_id"],
                "rule": "accepted paper instructions become watch_paper queue items",
            },
            "execution_allowed": False,
            "old_main_py_consumer": False,
        },
        "telegram_delivery_flow": {
            "schema": "telegram_delivery_flow.v1",
            "farm_core_sends_telegram": False,
            "paper_sends_telegram_by_default": False,
            "paper_preview_artifact": "state/derived/paper_telegram_preview.json",
            "paper_sender_artifact": "state/derived/paper_telegram_delivery.json",
            "paper_sender_cli": "scripts.strategy_lab.paper_telegram_sender",
            "paper_sender_chat_env": "SUBSCRIPTION_USERS",
            "paper_delivery_target": "active_subscription_users",
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
            "chart_formatter_path": "src.utils.llm_formatter (text shared-router via launcher; vision legacy Yandex)",
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
        "product_analyzer_launch_contract": {
            "schema": "product_analyzer_launch_contract.v1",
            "canonical_paper_launcher": "bat/strategy_lab_farm_full_cycle_loop.bat",
            "canonical_farm_module": "scripts.strategy_lab.farm_loop",
            "canonical_requires_run_paper_signals": True,
            "manual_telegram_launcher": "start.bat",
            "manual_telegram_role": "legacy product analyzer bot",
            "manual_telegram_current_for_farm": False,
            "telegram_bot_main_starts_scanner_loop": "_scanner_loop" in telegram_bot_main_body,
            "telegram_bot_main_polls_updates": "getUpdates" in telegram_bot_main_body,
            "telegram_bot_auto_execute_opt_in": _contains(
                telegram_bot,
                "TELEGRAM_BOT_ALLOW_AUTO_EXECUTE",
            ),
            "manual_chart_analyzer": "scripts.analyze_chart",
            "manual_chart_send_default": False,
            "manual_chart_can_send_with_flag": _contains(analyze_chart, "--send-telegram"),
            "manual_chart_uses_private_okx_client": _contains(analyze_chart, "OKXClient"),
            "manual_latest_wrapper": "scripts.run_latest_analysis",
            "manual_latest_requires_human_prompt": _contains(run_latest_analysis, "input("),
            "manual_latest_auto_execute_import_gated": (
                "ALLOW_AUTO_EXECUTE_ENV" in run_latest_entry_block
                and "from scripts.auto_execute import AUTO_TRADE, execute_signal" in run_latest_entry_block
            ),
            "text_card_shared_router_entrypoint": "generate_client_text",
            "educational_qa_shared_router_entrypoint": "generate_edu_text",
            "shared_router_opt_in_env": "PRODUCT_ANALYZER_LLM_ROUTER",
            "shared_router_active": llm_formatter_status["shared_router_active"],
            "start_bat_sets_shared_router": product_start_sets_shared_router,
            "start_telegram_bot_bat_sets_shared_router": product_tg_start_sets_shared_router,
            "launcher_sets_shared_router": product_launcher_sets_shared_router,
            "effective_shared_router": chart_formatter_effective_shared_router,
            "effective_provider": chart_formatter_effective_provider,
            "premium_vision_provider": premium_status["provider"],
            "premium_vision_configured": premium_status["configured"],
            "premium_vision_review_required": premium_status["review_required"],
            "premium_vision_yandex_only": premium_status["provider"] == "yandex",
            "edu_qa_yandex_only": "generate_edu_text" in llm_formatter_status["yandex_only_entrypoints"],
            "edu_qa_shared_router_entrypoint": "generate_edu_text" in chart_formatter_effective_shared_entrypoints,
            "farm_pfr_runtime_uses_manual_product_stack": False,
            "old_main_consumes_paper_queue": False,
            "telegram_send_default": False,
            "execution_allowed": False,
            "revival_rule": (
                "Use the canonical paper launcher for farm/PFR/paper. Treat start.bat, "
                "analyze_chart, and run_latest_analysis as manual product surfaces until "
                "their prompts, provider routing, Telegram delivery, and auto-execute hooks "
                "pass a separate product review."
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
            "product_signal_events": _exists(product_signal_events_log),
            "product_signal_training": _exists(product_signal_training),
            "product_signal_training_snapshot": _exists(product_signal_training_snapshot),
        },
        "training_data": {
            "paper_signal_training": _jsonl_schema_metrics(
                paper_signal_training,
                schema=("TrainingRow.v2", "PaperSignalTrainingRow.v2", "PaperSignalTrainingRow.v1"),
            ),
            "paper_signal_training_freshness": _freshness_metrics(
                paper_signal_training,
                paper_signal_log,
            ),
            "excel_journal_freshness": _excel_journal_freshness(ROOT, paper_signal_training),
            "product_signal_events": _jsonl_schema_metrics(
                product_signal_events_log,
                schema="signal_event.v1",
            ),
            "product_signal_training": _jsonl_schema_metrics(
                product_signal_training,
                schema="ProductSignalTrainingRow.v1",
            ),
            "product_signal_training_freshness": _freshness_metrics(
                product_signal_training,
                product_signal_events_log,
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
            "telegram_delivery_breakdown": _snapshot_status_breakdown(paper_telegram_delivery_snapshot),
            "telegram_delivery_freshness": _freshness_metrics(
                paper_telegram_delivery_snapshot,
                paper_telegram_preview_snapshot,
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
    report["product_analyzer_revival_checklist"] = _build_product_analyzer_revival_checklist(report)
    report["readiness"] = _build_readiness(report)
    report["operator_next_actions"] = _build_operator_next_actions(report["readiness"])
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
    legacy_text = report["legacy_product_text_quality"]
    print(
        "llm_surface_boundaries: "
        f"scanner_router={llm_boundaries['scanner_provider_router']} "
        f"scanner_alibaba={llm_boundaries['scanner_supports_alibaba']} "
        f"telegram_formatter_provider={llm_boundaries['telegram_chart_formatter_provider']} "
        f"telegram_formatter_configured={llm_boundaries['telegram_chart_formatter_configured']} "
        f"telegram_uses_llm_provider={llm_boundaries['telegram_chart_formatter_uses_llm_provider_env']} "
        f"telegram_launcher_shared_router={llm_boundaries['telegram_chart_formatter_launcher_sets_shared_router']} "
        f"telegram_effective_shared_router={llm_boundaries['telegram_chart_formatter_effective_shared_router']} "
        f"telegram_effective_scope={llm_boundaries['telegram_chart_formatter_effective_provider_scope']} "
        f"provider_mismatch={llm_boundaries['scanner_formatter_provider_mismatch']} "
        f"prompt_integrity={llm_boundaries['telegram_chart_formatter_prompt_integrity']} "
        f"mojibake={llm_boundaries['telegram_chart_formatter_mojibake_detected']} "
        f"analyze_chart_send_default={llm_boundaries['analyze_chart_send_default']}"
    )
    print(
        "premium_vision: "
        f"provider={llm_boundaries['premium_vision_provider']} "
        f"configured={llm_boundaries['premium_vision_configured']} "
        f"review_required={llm_boundaries['premium_vision_review_required']}"
    )
    print(
        "legacy_product_text_quality: "
        f"clean={legacy_text['clean']} files_with_markers={legacy_text['files_with_markers']} "
        f"marker_hits={legacy_text['marker_hits']} read_errors={legacy_text['read_errors']}"
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
    sources = report["paper_source_composition"]
    priority = report["paper_priority_policy"]
    signal_sources = sources["paper_signals"]["by"].get("source", {})
    signal_families = sources["paper_signals"]["by"].get("setup_family", {})
    queue_sources = sources["main_runtime_queue"]["by"].get("source", {})
    queue_families = sources["main_runtime_queue"]["by"].get("setup_family", {})
    observation_sources = sources["main_runtime_observation"]["by"].get("source", {})
    print(
        "paper_source_composition: "
        f"signals_rows={sources['paper_signals']['rows']} "
        f"signal_sources={signal_sources} "
        f"signal_families={signal_families} "
        f"queue_items={sources['main_runtime_queue']['items']} "
        f"queue_sources={queue_sources} "
        f"queue_families={queue_families} "
        f"observation_sources={observation_sources} "
        f"pfr_explicit={sources['pfr_activation']['requires_explicit_db_path']} "
        f"execution_allowed={sources['execution_allowed']}"
    )
    print(
        "paper_priority_policy: "
        f"live_order={priority['live_mover_lane']['order']} "
        f"pfr_order={priority['pfr_lane']['order']} "
        f"pfr_requires_db={priority['pfr_lane']['requires_explicit_db_path']} "
        f"runtime_sort={priority['runtime_queue']['sort_order']} "
        f"old_main_consumer={priority['old_main_py_consumer']} "
        f"execution_allowed={priority['execution_allowed']}"
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
    launch_contract = report["product_analyzer_launch_contract"]
    print(
        "product_analyzer_launch_contract: "
        f"canonical={launch_contract['canonical_paper_launcher']} "
        f"manual_start={launch_contract['manual_telegram_launcher']} "
        f"manual_current_for_farm={launch_contract['manual_telegram_current_for_farm']} "
        f"tg_main_starts_scanner={launch_contract['telegram_bot_main_starts_scanner_loop']} "
        f"latest_auto_execute_gated={launch_contract['manual_latest_auto_execute_import_gated']} "
        f"shared_router_active={launch_contract['shared_router_active']} "
        f"launcher_shared_router={launch_contract['launcher_sets_shared_router']} "
        f"effective_shared_router={launch_contract['effective_shared_router']} "
        f"execution_allowed={launch_contract['execution_allowed']}"
    )
    revival = report["product_analyzer_revival_checklist"]
    print(
        "product_analyzer_revival_checklist: "
        f"status={revival['status']} "
        f"canonical_paper_cycle_allowed={revival['canonical_paper_cycle_allowed']} "
        f"manual_product_alerts_allowed={revival['manual_product_alerts_allowed']} "
        f"live_execution_allowed={revival['live_execution_allowed']} "
        f"remaining_review={revival['remaining_review']}"
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
        f"errors={chain['telegram_delivery']['errors']} "
        f"stale_vs_preview={chain['telegram_delivery_freshness']['stale_vs_source']}"
    )
    delivery_breakdown = chain["telegram_delivery_breakdown"]
    if delivery_breakdown["by_status"] or delivery_breakdown["by_problem"]:
        print(
            "paper_telegram_delivery_breakdown: "
            f"status={delivery_breakdown['by_status']} "
            f"problems={delivery_breakdown['by_problem']}"
        )
    training = report["training_data"]["paper_signal_training"]
    freshness = report["training_data"]["paper_signal_training_freshness"]
    excel_freshness = report["training_data"]["excel_journal_freshness"]
    print(
        "paper_signal_training: "
        f"rows={training['rows']} schema_rows={training['schema_rows']} "
        f"invalid_json={training['invalid_json']} paper_only_false={training['paper_only_false']} "
        f"stale_vs_source={freshness['stale_vs_source']}"
    )
    product_events = report["training_data"]["product_signal_events"]
    product_training = report["training_data"]["product_signal_training"]
    product_training_freshness = report["training_data"]["product_signal_training_freshness"]
    print(
        "product_signal_events: "
        f"exists={report['journals']['product_signal_events']['exists']} "
        f"rows={product_events['rows']} schema_rows={product_events['schema_rows']} "
        f"invalid_json={product_events['invalid_json']} "
        f"paper_only_false={product_events['paper_only_false']} "
        f"execution_allowed_true={product_events['execution_allowed_true']}"
    )
    print(
        "product_signal_training: "
        f"exists={report['journals']['product_signal_training']['exists']} "
        f"rows={product_training['rows']} schema_rows={product_training['schema_rows']} "
        f"invalid_json={product_training['invalid_json']} "
        f"paper_only_false={product_training['paper_only_false']} "
        f"execution_allowed_true={product_training['execution_allowed_true']} "
        f"stale_vs_source={product_training_freshness['stale_vs_source']}"
    )
    print(
        "excel_journal: "
        f"exists={excel_freshness['derived_exists']} "
        f"stale_vs_training={excel_freshness['stale_vs_source']} "
        f"age_delta_seconds={excel_freshness['age_delta_seconds']}"
    )
    print("readiness:")
    for name, gate in report["readiness"].items():
        action = f" action={gate['action']}" if gate.get("action") else ""
        print(f"  {name}: {gate['status']} - {gate['message']}{action}")
    next_actions = report["operator_next_actions"]
    print(
        "operator_next_actions: "
        f"launch_blocked={next_actions['launch_blocked']} "
        f"status_counts={next_actions['status_counts']} "
        f"blocking={len(next_actions['blocking'])} "
        f"operator_configuration={len(next_actions['operator_configuration'])} "
        f"intentional_boundaries={len(next_actions['intentional_boundaries'])} "
        f"rebuild_actions={len(next_actions['rebuild_actions'])}"
    )
    for item in next_actions["blocking"]:
        print(f"  BLOCKING {item['name']}: {item['message']} action={item['action']}")
    for item in next_actions["operator_configuration"]:
        print(f"  OPERATOR {item['name']}: {item['message']} action={item['action']}")
    for item in next_actions["intentional_boundaries"]:
        print(f"  BOUNDARY {item['name']}: {item['message']} action={item['action']}")
    for item in next_actions["rebuild_actions"]:
        print(f"  REBUILD {item['name']}: {item['message']} action={item['action']}")
    print("journals:")
    for name, item in report["journals"].items():
        print(f"  {name}: exists={item['exists']} bytes={item['size_bytes']} path={item['path']}")


def has_blocked_readiness(report: dict[str, Any]) -> bool:
    """Return True when a readiness gate found a hard launch blocker."""
    readiness = report.get("readiness")
    if not isinstance(readiness, dict):
        return False
    return any(isinstance(gate, dict) and gate.get("status") == "blocked" for gate in readiness.values())


def exit_code_for_report(report: dict[str, Any], *, fail_on_blocked: bool) -> int:
    if fail_on_blocked and has_blocked_readiness(report):
        return 2
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    ap.add_argument("--pfr-db-path", type=Path, default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--fail-on-blocked",
        action="store_true",
        help="Exit non-zero only when a readiness gate has status=blocked.",
    )
    args = ap.parse_args()
    report = collect(private_root=args.private_root, pfr_db_path=args.pfr_db_path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_human(report)
    raise SystemExit(exit_code_for_report(report, fail_on_blocked=args.fail_on_blocked))


if __name__ == "__main__":
    main()
