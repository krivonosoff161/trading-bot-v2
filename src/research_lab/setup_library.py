# -*- coding: utf-8 -*-
"""Setup library: machine-readable and human-readable setup cards.

For every hard validation report, creates/updates:
- machine JSON setup card
- human markdown setup card
- index row
- grouped by symbol/timeframe/strategy

All output goes to the private root. main_engine_ready is always False.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from src.research_lab.hard_validation_contract import (
    SetupCard,
    SetupLibraryEntry,
    write_json,
)
from src.research_lab.param_schemas import executable_params_ready
from src.research_lab.simulator_contract import validate_simulator_assumption_manifest

LIBRARY_DIR = "setup_library"
INDEX_FILE = "setup_index.jsonl"


def build_setup_card(
    report: dict[str, Any],
    candidate: dict[str, Any] | None = None,
) -> SetupCard:
    """Build a SetupCard from a HardValidationReport dict and optional candidate."""
    if not _simulator_report_ready(report):
        raise ValueError("hard validation report has invalid simulator provenance")
    verdict = report.get("verdict") or {}
    hard_status = verdict.get("hard_status", "UNKNOWN")
    candidate_id = report.get("candidate_id", "")
    params = (candidate or {}).get("params", {})
    return SetupCard(
        setup_id=f"setup-{candidate_id}",
        candidate_id=candidate_id,
        symbol=report.get("symbol", ""),
        timeframe=report.get("timeframe", ""),
        strategy_id=report.get("strategy_id", ""),
        params=params,
        filters=(candidate or {}).get("filters", {}),
        data_window=(candidate or {}).get("data_window", {}),
        lite_status=(candidate or {}).get("lite_status", ""),
        hard_status=hard_status,
        checks_summary=report.get("checks_summary", {}),
        failed_checks=verdict.get("failed_checks", []),
        risk_flags=(candidate or {}).get("risk_flags", []),
        entry_exit_summary=_entry_exit_summary(
            verdict, str(report.get("simulator_claim_ceiling") or "unavailable")
        ),
        regime_tags=_extract_regime_tags(candidate),
        simulator_manifest=dict(report.get("simulator_manifest") or {}),
        unsupported_simulator_dimensions=list(
            report.get("unsupported_simulator_dimensions") or []
        ),
        simulator_claim_ceiling=str(report.get("simulator_claim_ceiling") or "unavailable"),
        paper_forward_ready=(
            hard_status == "PAPER_FORWARD_READY"
            and _paper_params_ready(report.get("strategy_id", ""), params)
            and _simulator_report_ready(report)
        ),
        main_engine_ready=False,
        created_at=report.get("created_at", ""),
        updated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
    )


def write_setup_library(
    private_root: Path,
    cards: list[SetupCard],
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Write setup cards, index, and groupings to the private root."""
    summary: dict[str, Any] = {
        "cards_written": 0,
        "index_rows": 0,
        "dry_run": dry_run,
    }
    if dry_run or not cards:
        return summary

    lib_dir = private_root / LIBRARY_DIR
    cards_dir = lib_dir / "cards"
    reports_dir = lib_dir / "reports"
    by_symbol = lib_dir / "by_symbol"
    by_tf = lib_dir / "by_timeframe"
    by_strat = lib_dir / "by_strategy"

    for d in [cards_dir, reports_dir, by_symbol, by_tf, by_strat]:
        d.mkdir(parents=True, exist_ok=True)

    index_path = lib_dir / INDEX_FILE
    index_rows = _load_index(index_path)
    for card in cards:
        card_dict = card.to_dict()
        card_path = cards_dir / f"{card.setup_id}.json"
        write_json(card_path, card_dict)

        md_path = reports_dir / f"{card.setup_id}.md"
        md_path.write_text(_card_to_markdown(card), encoding="utf-8")

        _write_group_link(by_symbol / card.symbol, card.setup_id)
        _write_group_link(by_tf / card.timeframe, card.setup_id)
        _write_group_link(by_strat / card.strategy_id, card.setup_id)

        entry = SetupLibraryEntry(
            setup_id=card.setup_id,
            symbol=card.symbol,
            timeframe=card.timeframe,
            strategy_id=card.strategy_id,
            hard_status=card.hard_status,
            paper_forward_ready=card.paper_forward_ready,
            main_engine_ready=card.main_engine_ready,
            created_at=card.created_at,
            updated_at=card.updated_at,
        )
        index_rows[card.setup_id] = entry.to_dict()

        summary["cards_written"] += 1
        summary["index_rows"] += 1
    _write_index(index_path, index_rows)

    return summary


def _load_index(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        setup_id = str(row.get("setup_id") or "")
        if setup_id:
            rows[setup_id] = row
    return rows


def _write_index(path: Path, rows: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = [rows[k] for k in sorted(rows)]
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in ordered),
        encoding="utf-8",
    )


def _write_group_link(group_dir: Path, setup_id: str) -> None:
    group_dir.mkdir(parents=True, exist_ok=True)
    link_path = group_dir / f"{setup_id}.json"
    link_path.write_text(json.dumps({"setup_id": setup_id}), encoding="utf-8")


def _entry_exit_summary(
    verdict: dict[str, Any], simulator_claim_ceiling: str = "unavailable"
) -> str:
    hard_status = str(verdict.get("hard_status") or "")
    if hard_status == "NEEDS_MORE_DATA":
        msg = str(verdict.get("message") or "More validation data is required.")
        return f"Hard validation did not run enough checks: {msg}"
    if hard_status and hard_status != "PAPER_FORWARD_READY":
        failed_for_status = verdict.get("failed_checks") or []
        if failed_for_status:
            return f"Failed checks: {', '.join(failed_for_status)}."
        return f"Hard validation status: {hard_status}."
    failed = verdict.get("failed_checks") or []
    if not failed:
        return (
            "All statistical checks passed — eligible for bounded paper observation; "
            f"simulator claim ceiling remains {simulator_claim_ceiling}."
        )
    return f"Failed checks: {', '.join(failed)}."


def _paper_params_ready(strategy_id: str, params: dict[str, Any]) -> bool:
    """Minimum executable paper contract for the current setup-card bridge."""
    if not strategy_id:
        return False
    return executable_params_ready(str(strategy_id), params)


def _simulator_report_ready(report: dict[str, Any]) -> bool:
    try:
        manifest = validate_simulator_assumption_manifest(
            dict(report.get("simulator_manifest") or {})
        )
    except (TypeError, ValueError):
        return False
    return (
        list(report.get("unsupported_simulator_dimensions") or [])
        == manifest["unsupported_dimensions"]
        and str(report.get("simulator_claim_ceiling") or "") == manifest["claim_ceiling"]
    )


def _extract_regime_tags(candidate: dict[str, Any] | None) -> list[str]:
    if not candidate:
        return []
    regime = candidate.get("regime_summary") or {}
    dominant = regime.get("dominant_bucket")
    if dominant:
        return [str(dominant)]
    return []


def _card_to_markdown(card: SetupCard) -> str:
    lines = [
        f"# Setup Card: {card.setup_id}",
        "",
        f"**Symbol:** {card.symbol}  **TF:** {card.timeframe}  "
        f"**Strategy:** {card.strategy_id}",
        "",
        f"**Hard Status:** `{card.hard_status}`",
        f"**Paper Forward Ready:** {card.paper_forward_ready}",
        f"**Main Engine Ready:** {card.main_engine_ready} (disabled by design)",
        f"**Simulator:** `{card.simulator_manifest.get('simulator_model_id', 'unavailable')}`",
        f"**Simulator Claim Ceiling:** `{card.simulator_claim_ceiling}`",
        "",
        "## Parameters",
        "",
        f"```json\n{json.dumps(card.params, indent=2)}\n```",
        "",
        "## Checks Summary",
        "",
        f"- Total: {card.checks_summary.get('total', '?')}",
        f"- Passed: {card.checks_summary.get('passed', '?')}",
        f"- Failed: {card.checks_summary.get('failed', '?')}",
        "",
    ]
    if card.failed_checks:
        lines += ["**Failed checks:** " + ", ".join(card.failed_checks), ""]
    if card.regime_tags:
        lines += ["**Regime tags:** " + ", ".join(card.regime_tags), ""]
    lines += [
        "---",
        "*This card is a research artifact. It does not imply profitability "
        "or readiness for live trading.*",
        "",
        f"Updated: {card.updated_at}",
    ]
    return "\n".join(lines)
