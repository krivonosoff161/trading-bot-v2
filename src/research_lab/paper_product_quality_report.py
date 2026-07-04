"""Aggregate quality/readiness report for the subscriber-facing paper product.

The report is intentionally private-root only.  It may read private derived
training rows, but it writes only aggregate counts and no raw signal text,
recipient ids, secrets, fills, or order instructions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUMMARY_SCHEMA = "paper_product_quality_report.v1"
MIN_FAMILY_SAMPLE = 20


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

    def to_dict(self) -> dict[str, Any]:
        decisive = self.take + self.stop
        take_rate = self.take / decisive if decisive else 0.0
        stop_rate = self.stop / decisive if decisive else 0.0
        avg_net_r = self.net_r_sum / self.net_r_count if self.net_r_count else 0.0
        avg_net_pct = self.net_pct_sum / self.net_pct_count if self.net_pct_count else 0.0
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
            "quality_label": label,
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


def _operator_action(
    *,
    delivery: dict[str, Any],
    product_trades: dict[str, Any],
    active_blockers: dict[str, int],
) -> str:
    if int(delivery.get("errors") or 0) > 0:
        return "fix_telegram_delivery_errors"
    if int(product_trades.get("active_trades") or 0) == 0:
        return "wait_for_new_active_paper_candidates"
    if int(product_trades.get("active_live_ready") or 0) == 0:
        if active_blockers.get("missing_ready_strategy_id"):
            return "fix_promotion_gap_missing_ready_strategy_id"
        return "inspect_active_live_blockers"
    if int(delivery.get("sent") or 0) == 0 and int(delivery.get("duplicates") or 0) > 0:
        return "no_new_telegram_cards_duplicates_only"
    return "collect_outcomes"


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
        f"- top live blockers: {summary['active_live_blockers']}",
        "",
        "## Telegram",
        "",
        f"- preview rendered: {summary['telegram']['preview_rendered']}",
        f"- eligible this cycle: {summary['telegram']['eligible']}",
        f"- sent this cycle: {summary['telegram']['sent']}",
        f"- duplicate skips this cycle: {summary['telegram']['duplicates']}",
        f"- delivery errors: {summary['telegram']['errors']}",
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
            "## Boundaries",
            "",
            "- paper_only: true",
            "- execution_allowed: false",
            "- no private fills are included",
            "- no raw prompts, signal text, recipient ids, or secrets are included",
        ]
    )
    return "\n".join(lines) + "\n"


def build_paper_product_quality_report(private_root: Path) -> dict[str, Any]:
    private_root = Path(private_root)
    derived = private_root / "state" / "derived"
    product_trades = _read_json(derived / "paper_product_trades.json")
    preview = _read_json(derived / "paper_telegram_preview.json")
    delivery = _read_json(derived / "paper_telegram_delivery.json")
    training_summary = _read_json(derived / "paper_signal_training.json")
    training_rows = _read_jsonl(derived / "paper_signal_training.jsonl")
    sent = _sent_key_summary(private_root)

    active_blockers = _top_counts(product_trades.get("by_live_block") or {})
    families = _family_stats(training_rows)
    quality_labels: dict[str, int] = {}
    for family in families:
        label = str(family.get("quality_label") or "")
        quality_labels[label] = quality_labels.get(label, 0) + 1

    telegram = {
        "preview_rendered": int(preview.get("rendered") or 0),
        "eligible": int(delivery.get("eligible") or 0),
        "sent": int(delivery.get("sent") or 0),
        "duplicates": int(delivery.get("duplicates") or 0),
        "errors": int(delivery.get("errors") or 0),
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
        "training_rows": int(training_summary.get("rows") or len(training_rows)),
        "training_terminal_only": bool(training_summary.get("terminal_only", True)),
        "training_by_result": _top_counts(training_summary.get("by_result") or {}),
        "quality_labels": dict(sorted(quality_labels.items())),
        "families": families,
        "telegram": telegram,
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
