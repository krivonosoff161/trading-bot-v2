"""Aggregate quality/readiness report for the subscriber-facing paper product.

The report is intentionally private-root only.  It may read private derived
training rows, but it writes only aggregate counts and no raw signal text,
recipient ids, secrets, fills, or order instructions.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUMMARY_SCHEMA = "paper_product_quality_report.v1"
MIN_FAMILY_SAMPLE = 20
ACTIVE_PRODUCT_STATUSES = {"armed", "opened_paper"}
PENDING_OUTCOME_RESULTS = {"pending", "pending_arm", "pending_open"}


@dataclass
class _FamilyStats:
    family: str
    rows: int = 0
    take: int = 0
    stop: int = 0
    simple_be: int = 0
    expired_no_entry: int = 0
    other: int = 0
    good_signal: int = 0
    bad_exit_gave_back: int = 0
    breakeven_save: int = 0
    net_r_sum: float = 0.0
    net_r_count: int = 0
    net_pct_sum: float = 0.0
    net_pct_count: int = 0
    paper_pnl_sum: float = 0.0
    paper_pnl_count: int = 0

    def add(self, row: dict[str, Any]) -> None:
        self.rows += 1
        result = str(row.get("result") or "")
        if result == "take":
            self.take += 1
        elif result == "stop":
            self.stop += 1
        elif result == "simple_be":
            self.simple_be += 1
        elif result == "expired_no_entry":
            self.expired_no_entry += 1
        else:
            self.other += 1

        diagnosis = str(row.get("diagnosis") or "")
        if diagnosis == "good_signal":
            self.good_signal += 1
        elif diagnosis == "bad_exit_gave_back":
            self.bad_exit_gave_back += 1
        elif diagnosis == "breakeven_save":
            self.breakeven_save += 1

        net_r = _float_or_none(row.get("net_r"))
        if net_r is not None:
            self.net_r_sum += net_r
            self.net_r_count += 1
        net_pct = _float_or_none(row.get("net_pct"))
        if net_pct is not None:
            self.net_pct_sum += net_pct
            self.net_pct_count += 1
        paper_pnl = _float_or_none(row.get("paper_pnl_usdt"))
        if paper_pnl is not None:
            self.paper_pnl_sum += paper_pnl
            self.paper_pnl_count += 1

    def to_dict(self) -> dict[str, Any]:
        decisive = self.take + self.stop
        take_rate = self.take / decisive if decisive else 0.0
        stop_rate = self.stop / decisive if decisive else 0.0
        avg_net_r = self.net_r_sum / self.net_r_count if self.net_r_count else 0.0
        avg_net_pct = self.net_pct_sum / self.net_pct_count if self.net_pct_count else 0.0
        avg_paper_pnl = self.paper_pnl_sum / self.paper_pnl_count if self.paper_pnl_count else 0.0
        label = _quality_label(rows=self.rows, take_rate=take_rate, stop_rate=stop_rate, avg_net_r=avg_net_r)
        return {
            "family": self.family,
            "rows": self.rows,
            "take": self.take,
            "stop": self.stop,
            "simple_be": self.simple_be,
            "expired_no_entry": self.expired_no_entry,
            "other": self.other,
            "decisive": decisive,
            "take_rate": round(take_rate, 4),
            "stop_rate": round(stop_rate, 4),
            "good_signal": self.good_signal,
            "bad_exit_gave_back": self.bad_exit_gave_back,
            "breakeven_save": self.breakeven_save,
            "avg_net_r": round(avg_net_r, 4),
            "avg_net_pct": round(avg_net_pct, 4),
            "paper_pnl_usdt": round(self.paper_pnl_sum, 6),
            "avg_paper_pnl_usdt": round(avg_paper_pnl, 6),
            "quality_label": label,
        }


@dataclass
class _GeometryProfileStats:
    profile_id: str
    rows: int = 0
    take: int = 0
    stop: int = 0
    timeout: int = 0
    simple_be: int = 0
    other: int = 0
    net_r_sum: float = 0.0
    net_r_count: int = 0
    paper_pnl_sum: float = 0.0
    paper_pnl_count: int = 0
    by_family: dict[str, int] | None = None

    def add(self, row: dict[str, Any]) -> None:
        self.rows += 1
        result = str(row.get("result") or "")
        if result == "take":
            self.take += 1
        elif result == "stop":
            self.stop += 1
        elif result == "timeout":
            self.timeout += 1
        elif result in {"simple_be", "partial_be"}:
            self.simple_be += 1
        else:
            self.other += 1
        net_r = _float_or_none(row.get("net_r"))
        if net_r is not None:
            self.net_r_sum += net_r
            self.net_r_count += 1
        paper_pnl = _float_or_none(row.get("paper_pnl_usdt"))
        if paper_pnl is not None:
            self.paper_pnl_sum += paper_pnl
            self.paper_pnl_count += 1
        family = str(row.get("family") or "unknown")
        if self.by_family is None:
            self.by_family = {}
        self.by_family[family] = self.by_family.get(family, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        decisive = self.take + self.stop
        take_rate = self.take / decisive if decisive else 0.0
        avg_net_r = self.net_r_sum / self.net_r_count if self.net_r_count else 0.0
        avg_paper_pnl = self.paper_pnl_sum / self.paper_pnl_count if self.paper_pnl_count else 0.0
        return {
            "profile_id": self.profile_id,
            "rows": self.rows,
            "take": self.take,
            "stop": self.stop,
            "timeout": self.timeout,
            "simple_be": self.simple_be,
            "other": self.other,
            "decisive": decisive,
            "take_rate": round(take_rate, 4),
            "avg_net_r": round(avg_net_r, 4),
            "paper_pnl_usdt": round(self.paper_pnl_sum, 6),
            "avg_paper_pnl_usdt": round(avg_paper_pnl, 6),
            "top_families": _top_counts(self.by_family or {}),
            "sample_label": "sample_too_small" if self.rows < 10 else "ready_for_compare",
        }


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _quality_label(*, rows: int, take_rate: float, stop_rate: float, avg_net_r: float) -> str:
    if rows < MIN_FAMILY_SAMPLE:
        return "sample_too_small"
    if take_rate >= 0.45 and stop_rate <= 0.35 and avg_net_r >= 0:
        return "candidate_watch"
    if stop_rate > take_rate:
        return "needs_review"
    if avg_net_r < 0:
        return "weak_after_costs"
    return "mixed"


def _sent_key_summary(private_root: Path) -> dict[str, int]:
    data = _read_json(private_root / "state" / "derived" / "paper_telegram_sent_keys.json")
    keys = [str(item) for item in data.get("sent_keys", []) if str(item)]
    previews = {key.rsplit(":", 1)[0] for key in keys if ":" in key}
    recipients = {key.rsplit(":", 1)[1] for key in keys if ":" in key}
    return {
        "sent_key_count": len(keys),
        "sent_preview_count": len(previews),
        "sent_recipient_count": len(recipients),
    }


def _top_counts(raw: Any, *, limit: int = 6) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    pairs = sorted(
        ((str(key), int(value or 0)) for key, value in raw.items()),
        key=lambda item: (-item[1], item[0]),
    )
    return dict(pairs[:limit])


def _has_pfr_budget_limit(reasons: dict[str, Any]) -> bool:
    budget_prefixes = ("pfr_fetch_limit", "pfr_scan_limit")
    return any(str(reason).startswith(budget_prefixes) for reason in reasons)


def _pfr_live_trigger_reasons(pfr_counts: dict[str, Any]) -> dict[str, int]:
    reasons: dict[str, int] = {}
    for key, value in pfr_counts.items():
        key_text = str(key)
        if key_text.startswith(("pfr_rejected:", "pfr_fetch_", "pfr_dedup_", "pfr_tf_", "pfr_no_builder")):
            reasons[key_text] = int(value or 0)
    return _top_counts(reasons, limit=12)


def _cycle_resource_reasons(gate_counts: dict[str, Any]) -> dict[str, int]:
    reasons: dict[str, int] = {}
    for key, value in gate_counts.items():
        key_text = str(key)
        if key_text in {"network_fetch_limit_reached", "stale_data"} or key_text.startswith(
            ("observe_network_fetch_limit_reached", "live_fetch_limit_reached")
        ):
            reasons[key_text] = reasons.get(key_text, 0) + int(value or 0)
    return _top_counts(reasons, limit=12)


def _pfr_near_trigger_counts(pfr_counts: dict[str, Any]) -> dict[str, int]:
    return _top_counts(
        {str(key): int(value or 0) for key, value in pfr_counts.items() if str(key).startswith("pfr_near_trigger:")},
        limit=12,
    )


def _validated_bridge_instructions(bridge: dict[str, Any]) -> int:
    items = bridge.get("items")
    if not isinstance(items, list):
        return 0
    total = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        contract = item.get("signal_contract")
        metadata = contract.get("metadata") if isinstance(contract, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        ready_strategy_id = str(item.get("ready_strategy_id") or metadata.get("ready_strategy_id") or "").strip()
        verdict = str(
            item.get("source_validation_verdict") or metadata.get("source_validation_verdict") or ""
        ).strip()
        tier = str(item.get("validation_tier") or metadata.get("validation_tier") or "").strip()
        if tier == "validated_pfr" or (ready_strategy_id and verdict == "PAPER_FORWARD_READY"):
            total += 1
    return total


def _delivery_int(delivery: dict[str, Any], primary: str, fallback: str) -> int:
    return int(delivery.get(primary, delivery.get(fallback) or 0) or 0)


def _active_live_blockers(product_trades: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in product_trades.get("items") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "") not in ACTIVE_PRODUCT_STATUSES:
            continue
        reason = str(row.get("live_block_reason") or "").strip()
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    if not counts and int(product_trades.get("active_live_blocked") or 0) > 0:
        return _top_counts(product_trades.get("by_live_block") or {})
    return _top_counts(counts)


def _hour_bucket(hours: float) -> str:
    if hours < 0:
        return "overdue"
    if hours <= 1:
        return "le_1h"
    if hours <= 3:
        return "le_3h"
    if hours <= 6:
        return "le_6h"
    if hours <= 12:
        return "le_12h"
    if hours <= 24:
        return "le_24h"
    return "gt_24h"


def _active_signal_lifecycle(paper_signals: dict[str, Any], *, now: float) -> dict[str, Any]:
    active_rows = [
        row
        for row in paper_signals.get("active") or []
        if isinstance(row, dict) and str(row.get("status") or "") in ACTIVE_PRODUCT_STATUSES
    ]
    by_status: dict[str, int] = {}
    by_outcome_result: dict[str, int] = {}
    age_buckets: dict[str, int] = {}
    expiry_buckets: dict[str, int] = {}
    overdue_expiry = 0
    next_expiry_hours: float | None = None
    oldest_age_hours = 0.0
    no_outcome = 0
    for row in active_rows:
        status = str(row.get("status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        outcome = row.get("outcome") if isinstance(row.get("outcome"), dict) else {}
        result = str(outcome.get("result") or "")
        if result:
            by_outcome_result[result] = by_outcome_result.get(result, 0) + 1
        else:
            no_outcome += 1
        created_at = _float_or_none(row.get("created_at"))
        if created_at is not None:
            age_hours = max(0.0, (now - created_at) / 3600)
            oldest_age_hours = max(oldest_age_hours, age_hours)
            age_bucket = _hour_bucket(age_hours)
            age_buckets[age_bucket] = age_buckets.get(age_bucket, 0) + 1
        expires_at = _float_or_none(row.get("expires_at"))
        if expires_at is not None:
            expiry_hours = (expires_at - now) / 3600
            expiry_bucket = _hour_bucket(expiry_hours)
            expiry_buckets[expiry_bucket] = expiry_buckets.get(expiry_bucket, 0) + 1
            if expiry_hours < 0:
                overdue_expiry += 1
            elif next_expiry_hours is None or expiry_hours < next_expiry_hours:
                next_expiry_hours = expiry_hours
    pending = sum(count for result, count in by_outcome_result.items() if result in PENDING_OUTCOME_RESULTS)
    return {
        "active": len(active_rows),
        "by_status": _top_counts(by_status),
        "by_outcome_result": _top_counts(by_outcome_result),
        "pending_outcomes": pending,
        "active_without_outcome": no_outcome,
        "oldest_age_hours": round(oldest_age_hours, 2),
        "next_expiry_hours": round(next_expiry_hours, 2) if next_expiry_hours is not None else None,
        "overdue_expiry": overdue_expiry,
        "age_buckets": _top_counts(age_buckets),
        "expiry_buckets": _top_counts(expiry_buckets),
        "terminal_training_backlog": 0,
    }


def _lifecycle_integrity(training_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [
        row
        for row in training_rows
        if str(row.get("lifecycle_schema") or "") == "PaperSignalLifecycle.v2"
    ]
    entry_expired = sum(
        str(row.get("result") or "") == "expired_no_entry"
        and (_float_or_none(row.get("observed_entry")) or 0.0) > 0
        for row in rows
    )
    negative_hold = sum(
        (_float_or_none(row.get("bars_held")) or 0.0) < 0
        for row in rows
    )
    return {
        "schema": "paper_signal_lifecycle_integrity.v1",
        "v2_rows": len(rows),
        "entry_expired_contradictions": entry_expired,
        "negative_bars_held": negative_hold,
        "valid": entry_expired == 0 and negative_hold == 0,
    }


def _pfr_funnel(
    *,
    ready_catalog: dict[str, Any],
    bridge: dict[str, Any],
    paper_status: dict[str, Any],
) -> dict[str, Any]:
    last_cycle = paper_status.get("last_cycle") if isinstance(paper_status.get("last_cycle"), dict) else {}
    pfr_counts = last_cycle.get("pfr_counts") if isinstance(last_cycle.get("pfr_counts"), dict) else {}
    gate_counts = last_cycle.get("gate_counts") if isinstance(last_cycle.get("gate_counts"), dict) else {}
    return {
        "catalog_ready": int(ready_catalog.get("ready") or 0),
        "catalog_rejected_quality": int(ready_catalog.get("rejected_quality") or 0),
        "catalog_ready_by_family": _top_counts(ready_catalog.get("ready_by_family") or {}),
        "catalog_ready_by_timeframe": _top_counts(ready_catalog.get("ready_by_timeframe") or {}),
        "bridge_active_source_signals": int(bridge.get("active_source_signals") or 0),
        "bridge_instructions": int(bridge.get("instructions") or 0),
        "bridge_validated_instructions": _validated_bridge_instructions(bridge),
        "bridge_skip_reasons": _top_counts(bridge.get("skip_reasons") or {}),
        "last_cycle_generated": int(last_cycle.get("generated") or 0) if last_cycle else 0,
        "last_cycle_pfr_generated": int(pfr_counts.get("pfr_generated") or 0)
        + int(pfr_counts.get("pfr_generated_pretrigger") or 0),
        "last_cycle_observed": int(last_cycle.get("observed") or 0) if last_cycle else 0,
        "last_cycle_pfr_counts": _top_counts(pfr_counts),
        "last_cycle_gate_counts": _top_counts(gate_counts),
        "live_trigger_reasons": _pfr_live_trigger_reasons(pfr_counts),
        "near_trigger_counts": _pfr_near_trigger_counts(pfr_counts),
        "cycle_resource_reasons": _cycle_resource_reasons(gate_counts),
    }


def _pfr_trigger_state(pfr_funnel: dict[str, Any]) -> dict[str, Any]:
    catalog_ready = int(pfr_funnel.get("catalog_ready") or 0)
    bridge_instructions = int(pfr_funnel.get("bridge_instructions") or 0)
    bridge_validated_instructions = int(pfr_funnel.get("bridge_validated_instructions") or 0)
    generated = int(pfr_funnel.get("last_cycle_pfr_generated") or 0)
    trigger_reasons = pfr_funnel.get("live_trigger_reasons") if isinstance(
        pfr_funnel.get("live_trigger_reasons"),
        dict,
    ) else {}
    if catalog_ready <= 0:
        state = "no_pfr_catalog_ready"
    elif bridge_validated_instructions > 0:
        state = "main_paper_has_pfr_instructions"
    elif generated > 0:
        state = "pfr_generated_waiting_downstream"
    elif trigger_reasons and _has_pfr_budget_limit(trigger_reasons):
        state = "pfr_trigger_scan_limited"
    elif trigger_reasons:
        state = "waiting_for_live_trigger"
    else:
        state = "pfr_catalog_ready_waiting_for_cycle"
    return {
        "state": state,
        "catalog_ready": catalog_ready,
        "bridge_instructions": bridge_instructions,
        "bridge_validated_instructions": bridge_validated_instructions,
        "last_cycle_generated": int(pfr_funnel.get("last_cycle_generated") or 0),
        "last_cycle_pfr_generated": generated,
        "top_reasons": _top_counts(trigger_reasons, limit=12),
    }


def _operator_action(
    *,
    delivery: dict[str, Any],
    product_trades: dict[str, Any],
    active_blockers: dict[str, int],
) -> str:
    if _delivery_int(delivery, "error_messages", "errors") > 0:
        return "fix_telegram_delivery_errors"
    if int(product_trades.get("active_trades") or 0) == 0:
        return "wait_for_new_active_paper_candidates"
    if int(product_trades.get("active_live_ready") or 0) == 0:
        active_sources = product_trades.get("active_by_source") or {}
        if active_blockers.get("missing_ready_strategy_id") and _only_research_sources(active_sources):
            return "strict_main_waiting_for_active_pfr_candidates"
        if active_blockers.get("missing_ready_strategy_id"):
            return "fix_pfr_context_missing_ready_strategy_id"
        return "inspect_active_live_blockers"
    if _delivery_int(delivery, "sent_messages", "sent") == 0 and _delivery_int(
        delivery,
        "duplicate_messages",
        "duplicates",
    ) > 0:
        return "no_new_telegram_cards_duplicates_only"
    return "collect_outcomes"


def _only_research_sources(active_sources: Any) -> bool:
    if not isinstance(active_sources, dict) or not active_sources:
        return False
    research_sources = {"farm", "scanner", "manual", "tactical"}
    present = {str(source) for source, count in active_sources.items() if int(count or 0) > 0}
    return bool(present) and present.issubset(research_sources)


def _family_stats(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_family: dict[str, _FamilyStats] = {}
    for row in rows:
        if row.get("schema") != "TrainingRow.v2":
            continue
        family = str(row.get("family") or "unknown")
        by_family.setdefault(family, _FamilyStats(family=family)).add(row)
    ranked = sorted(
        (stat.to_dict() for stat in by_family.values()),
        key=lambda item: (-int(item["rows"]), str(item["family"])),
    )
    return ranked


def _geometry_profile_stats(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_profile: dict[str, _GeometryProfileStats] = {}
    for row in rows:
        if row.get("schema") != "TrainingRow.v2":
            continue
        profile_id = str(row.get("farm_geometry_profile_id") or "legacy_or_unknown")
        by_profile.setdefault(profile_id, _GeometryProfileStats(profile_id=profile_id)).add(row)
    ranked = sorted(
        (stat.to_dict() for stat in by_profile.values()),
        key=lambda item: (-int(item["rows"]), str(item["profile_id"])),
    )
    return ranked


def _render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Paper Product Quality Report",
        "",
        "This is a private aggregate report. It is not a live-trading approval, "
        "not investment advice, and not an order-execution signal.",
        "",
        "## Current Funnel",
        "",
        f"- product trades: {summary['product_trades']}",
        f"- active paper trades: {summary['active_trades']}",
        f"- active live-ready: {summary['active_live_ready']}",
        f"- active live-blocked: {summary['active_live_blocked']}",
        f"- active by source: {summary['active_by_source']}",
        f"- active by family: {summary['active_by_family']}",
        f"- top live blockers: {summary['active_live_blockers']}",
        f"- total live blockers: {summary['total_live_blockers']}",
        f"- active lifecycle status: {summary['active_signal_lifecycle']['by_status']}",
        f"- active outcome states: {summary['active_signal_lifecycle']['by_outcome_result']}",
        f"- pending active outcomes: {summary['active_signal_lifecycle']['pending_outcomes']}",
        f"- oldest active age hours: {summary['active_signal_lifecycle']['oldest_age_hours']}",
        f"- next active expiry hours: {summary['active_signal_lifecycle']['next_expiry_hours']}",
        f"- active expiry buckets: {summary['active_signal_lifecycle']['expiry_buckets']}",
        f"- lifecycle v2 integrity: {summary['lifecycle_integrity']}",
        "",
        "## Strict PFR Funnel",
        "",
        f"- ready catalog: {summary['pfr_funnel']['catalog_ready']} ready / "
        f"{summary['pfr_funnel']['catalog_rejected_quality']} rejected-quality",
        f"- ready by family: {summary['pfr_funnel']['catalog_ready_by_family']}",
        f"- ready by timeframe: {summary['pfr_funnel']['catalog_ready_by_timeframe']}",
        f"- active source signals: {summary['pfr_funnel']['bridge_active_source_signals']}",
        f"- main-paper instructions: {summary['pfr_funnel']['bridge_instructions']}",
        f"- validated/PFR instructions: {summary['pfr_funnel']['bridge_validated_instructions']}",
        f"- bridge skip reasons: {summary['pfr_funnel']['bridge_skip_reasons']}",
        f"- last PFR counts: {summary['pfr_funnel']['last_cycle_pfr_counts']}",
        f"- live-trigger state: {summary['pfr_trigger_state']['state']}",
        f"- live-trigger top reasons: {summary['pfr_trigger_state']['top_reasons']}",
        f"- near-trigger buckets: {summary['pfr_funnel']['near_trigger_counts']}",
        f"- cycle resource/data reasons: {summary['pfr_funnel']['cycle_resource_reasons']}",
        "",
        "## Telegram",
        "",
        f"- preview rendered: {summary['telegram']['preview_rendered']}",
        f"- eligible cards this cycle: {summary['telegram']['eligible_cards']}",
        f"- target recipients: {summary['telegram']['target_recipients']}",
        f"- sent messages this cycle: {summary['telegram']['sent_messages']}",
        f"- sent cards this cycle: {summary['telegram']['sent_cards']}",
        f"- duplicate messages this cycle: {summary['telegram']['duplicate_messages']}",
        f"- duplicate cards this cycle: {summary['telegram']['duplicate_cards']}",
        f"- delivery errors: {summary['telegram']['error_messages']}",
        f"- cumulative sent previews: {summary['telegram']['sent_previews_total']}",
        "",
        "## Operator Action",
        "",
        f"`{summary['operator_action']}`",
        "",
        "## Family Quality",
        "",
        "| family | rows | take | stop | be | expired | avg_net_r | label |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in summary["families"][:12]:
        lines.append(
            "| {family} | {rows} | {take} | {stop} | {simple_be} | {expired_no_entry} | "
            "{avg_net_r} | {quality_label} |".format(**row)
        )
    lines.extend(
        [
            "",
            "## Farm Geometry Profiles",
            "",
            "| profile | rows | take | stop | timeout | be | avg_net_r | pnl_usdt | sample |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in summary["geometry_profiles"][:12]:
        lines.append(
            "| {profile_id} | {rows} | {take} | {stop} | {timeout} | {simple_be} | "
            "{avg_net_r} | {paper_pnl_usdt} | {sample_label} |".format(**row)
        )
    lines.extend(
        [
            "",
            "## Boundaries",
            "",
            "- paper_only: true",
            "- execution_allowed: false",
            "- no private fills are included",
            "- no raw prompts, signal text, recipient ids, or secrets are included",
        ]
    )
    return "\n".join(lines) + "\n"


def build_paper_product_quality_report(private_root: Path, *, now: float | None = None) -> dict[str, Any]:
    private_root = Path(private_root)
    now = time.time() if now is None else now
    derived = private_root / "state" / "derived"
    product_trades = _read_json(derived / "paper_product_trades.json")
    preview = _read_json(derived / "paper_telegram_preview.json")
    delivery = _read_json(derived / "paper_telegram_delivery.json")
    bridge = _read_json(derived / "main_paper_instructions.json")
    ready_catalog = _read_json(derived / "ready_strategy_catalog.json")
    paper_status = _read_json(derived / "paper_signals_status.json")
    paper_signals = _read_json(derived / "paper_signals.json")
    training_summary = _read_json(derived / "paper_signal_training.json")
    training_rows = _read_jsonl(derived / "paper_signal_training.jsonl")
    sent = _sent_key_summary(private_root)

    active_blockers = _active_live_blockers(product_trades)
    total_blockers = _top_counts(product_trades.get("by_live_block") or {})
    pfr_funnel = _pfr_funnel(ready_catalog=ready_catalog, bridge=bridge, paper_status=paper_status)
    families = _family_stats(training_rows)
    geometry_profiles = _geometry_profile_stats(training_rows)
    paper_pnl_values = [
        value
        for value in (_float_or_none(row.get("paper_pnl_usdt")) for row in training_rows)
        if value is not None
    ]
    quality_labels: dict[str, int] = {}
    for family in families:
        label = str(family.get("quality_label") or "")
        quality_labels[label] = quality_labels.get(label, 0) + 1

    telegram = {
        "preview_rendered": int(preview.get("rendered") or 0),
        "eligible": int(delivery.get("eligible") or 0),
        "eligible_cards": _delivery_int(delivery, "eligible_cards", "eligible"),
        "target_recipients": _delivery_int(delivery, "target_recipients", "targets"),
        "potential_messages": int(delivery.get("potential_messages") or 0),
        "sent": int(delivery.get("sent") or 0),
        "sent_messages": _delivery_int(delivery, "sent_messages", "sent"),
        "sent_cards": int(delivery.get("sent_cards") or 0),
        "duplicates": int(delivery.get("duplicates") or 0),
        "duplicate_messages": _delivery_int(delivery, "duplicate_messages", "duplicates"),
        "duplicate_cards": int(delivery.get("duplicate_cards") or 0),
        "errors": int(delivery.get("errors") or 0),
        "error_messages": _delivery_int(delivery, "error_messages", "errors"),
        "error_cards": int(delivery.get("error_cards") or 0),
        "configured": bool(delivery.get("configured")),
        "sends_network": bool(delivery.get("sends_network")),
        "sent_keys_total": sent["sent_key_count"],
        "sent_previews_total": sent["sent_preview_count"],
        "sent_recipients_total": sent["sent_recipient_count"],
    }
    summary = {
        "schema": SUMMARY_SCHEMA,
        "source_schemas": {
            "product_trades": product_trades.get("schema", ""),
            "training": training_summary.get("schema", ""),
            "telegram_delivery": delivery.get("schema", ""),
        },
        "product_trades": int(product_trades.get("trades") or 0),
        "live_ready": int(product_trades.get("live_ready") or 0),
        "live_blocked": int(product_trades.get("live_blocked") or 0),
        "active_trades": int(product_trades.get("active_trades") or 0),
        "active_live_ready": int(product_trades.get("active_live_ready") or 0),
        "active_live_blocked": int(product_trades.get("active_live_blocked") or 0),
        "active_live_blockers": active_blockers,
        "total_live_blockers": total_blockers,
        "active_by_source": _top_counts(product_trades.get("active_by_source") or {}),
        "active_by_family": _top_counts(product_trades.get("active_by_family") or {}),
        "training_rows": int(training_summary.get("rows") or len(training_rows)),
        "training_terminal_only": bool(training_summary.get("terminal_only", True)),
        "training_by_result": _top_counts(training_summary.get("by_result") or {}),
        "training_paper_pnl_usdt": round(sum(paper_pnl_values), 6),
        "training_avg_paper_pnl_usdt": (
            round(sum(paper_pnl_values) / len(paper_pnl_values), 6) if paper_pnl_values else 0.0
        ),
        "quality_labels": dict(sorted(quality_labels.items())),
        "families": families,
        "geometry_profiles": geometry_profiles,
        "telegram": telegram,
        "pfr_funnel": pfr_funnel,
        "pfr_trigger_state": _pfr_trigger_state(pfr_funnel),
        "active_signal_lifecycle": _active_signal_lifecycle(paper_signals, now=now),
        "lifecycle_integrity": _lifecycle_integrity(training_rows),
        "operator_action": _operator_action(
            delivery=delivery,
            product_trades=product_trades,
            active_blockers=active_blockers,
        ),
        "paper_only": True,
        "execution_allowed": False,
        "json_path": str(derived / "paper_product_quality_report.json"),
        "markdown_path": str(derived / "paper_product_quality_report.md"),
    }
    derived.mkdir(parents=True, exist_ok=True)
    (derived / "paper_product_quality_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (derived / "paper_product_quality_report.md").write_text(
        _render_markdown(summary),
        encoding="utf-8",
    )
    return summary
