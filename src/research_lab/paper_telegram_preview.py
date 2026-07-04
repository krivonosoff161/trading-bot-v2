"""Offline Telegram-card preview for main paper trades.

The preview is a dry-run surface: it renders operator-facing cards, validates
length/HTML safety, and writes private derived artifacts. It never imports
Telegram senders, never reads tokens or chat IDs, and never sends network
requests.
"""

from __future__ import annotations

import hashlib
import html
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA = "PaperTelegramPreview.v1"
SUMMARY_SCHEMA = "paper_telegram_preview.v1"
CARD_TEMPLATE_VERSION = "paper_telegram_card_v5_candidate_ru"
MAX_MESSAGE_CHARS = 4096
REQUIRED_DISCLAIMER = "research-only, not an order"
HUMAN_DISCLAIMER = "\u042d\u0442\u043e paper-\u043d\u0430\u0431\u043b\u044e\u0434\u0435\u043d\u0438\u0435, \u043d\u0435 \u043e\u0440\u0434\u0435\u0440 \u0438 \u043d\u0435 \u043a\u043e\u043c\u0430\u043d\u0434\u0430 \u043a \u0432\u0445\u043e\u0434\u0443."

LABEL_IDEA = "\u0418\u0434\u0435\u044f"
LABEL_ENTRY = "\u0412\u0445\u043e\u0434"
LABEL_STOP = "\u0421\u0442\u043e\u043f"
LABEL_TARGETS = "\u0426\u0435\u043b\u0438"
LABEL_MAX_HOLD = "\u041c\u0430\u043a\u0441. \u0443\u0434\u0435\u0440\u0436\u0430\u043d\u0438\u0435"
LABEL_STATUS = "\u0421\u0442\u0430\u0442\u0443\u0441"
LABEL_OUTCOME = "\u0418\u0441\u0445\u043e\u0434"
LABEL_REASON = "\u041f\u043e\u0447\u0435\u043c\u0443 \u0441\u0435\u0439\u0447\u0430\u0441"
LABEL_SOURCE = "\u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a"
EXECUTION_OFF = "\u0410\u0432\u0442\u043e\u0438\u0441\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u0435 \u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u043e: execution_allowed=false"

MOJIBAKE_MARKERS = (
    "\u0420\u00a0",
    "\u0420\u040f",
    "\u0420\u2019",
    "\u0420\u040b",
    "\u0420\u2020\u0420\u201a",
    "\u0421\u0453",
    "\u0421\u201a",
    "\u0420\u0406\u00b7",
    "\u0456\u201a",
)

NON_ACTIONABLE_TRADE_STATUSES = frozenset({"provider_error", "no_data", "pending_clock", "invalid"})
ACTIONABLE_PAPER_SIGNAL_STATUSES = frozenset({"armed", "opened_paper"})


@dataclass(frozen=True)
class PaperTelegramPreview:
    telegram_card_id: str
    preview_id: str
    instruction_id: str
    source_signal_id: str
    pair: str
    timeframe: str
    side: str
    setup_family: str
    consumer_status: str
    text: str
    problems: list[str] = field(default_factory=list)
    card_template_version: str = CARD_TEMPLATE_VERSION
    paper_only: bool = True
    execution_allowed: bool = False
    schema: str = SCHEMA

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            raise ValueError(f"schema must be {SCHEMA}")
        if self.execution_allowed:
            raise ValueError("paper preview must never allow execution")
        if not self.paper_only:
            raise ValueError("paper preview must be paper_only")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _consumer_snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_consumed.json"


def _trade_snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_trades.json"


def _paper_signal_snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_signals.json"


def _jsonl_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_telegram_preview.jsonl"


def _snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_telegram_preview.json"


def _card_ledger_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_telegram_card_ledger.json"


def _card_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _fmt_price(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if number >= 100:
        return f"{number:.2f}"
    if number >= 1:
        return f"{number:.4f}"
    return f"{number:.8f}".rstrip("0").rstrip(".")


def _targets_from_plan(plan: list[dict[str, Any]]) -> str:
    rendered = []
    for item in plan[:3]:
        label = html.escape(str(item.get("label") or "tp"))
        rendered.append(f"{label}={_fmt_price(item.get('price'))}")
    return ", ".join(rendered) or "n/a"


def _targets_from_contract(contract: dict[str, Any]) -> str:
    targets = ((contract.get("exit_rule") or {}).get("params") or {}).get("targets") or []
    return _targets_from_plan(list(targets))


def _family_label(family: str) -> str:
    labels = {
        "early_tp_tactical": "\u0431\u044b\u0441\u0442\u0440\u044b\u0439 \u0442\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 TP",
        "reversal_fade": "\u043e\u0442\u0431\u043e\u0439 \u043f\u043e\u0441\u043b\u0435 \u0440\u0430\u0441\u0442\u044f\u0436\u0435\u043d\u0438\u044f",
        "liquidity_sweep_reclaim": "\u0441\u043d\u044f\u0442\u0438\u0435 \u043b\u0438\u043a\u0432\u0438\u0434\u043d\u043e\u0441\u0442\u0438 \u0438 \u0432\u043e\u0437\u0432\u0440\u0430\u0442",
        "continuation": "\u043f\u0440\u043e\u0434\u043e\u043b\u0436\u0435\u043d\u0438\u0435 \u0434\u0432\u0438\u0436\u0435\u043d\u0438\u044f",
        "pullback_continuation": "\u043e\u0442\u043a\u0430\u0442 \u043f\u043e \u0442\u0440\u0435\u043d\u0434\u0443",
        "momentum_breakout": "\u0438\u043c\u043f\u0443\u043b\u044c\u0441\u043d\u044b\u0439 \u043f\u0440\u043e\u0431\u043e\u0439",
        "mean_reversion_fade": "\u0432\u043e\u0437\u0432\u0440\u0430\u0442 \u043a \u0441\u0440\u0435\u0434\u043d\u0435\u043c\u0443",
        "pfr_momentum_breakout": "PFR \u0438\u043c\u043f\u0443\u043b\u044c\u0441\u043d\u044b\u0439 \u043f\u0440\u043e\u0431\u043e\u0439",
        "pfr_mean_reversion_fade": "PFR \u0432\u043e\u0437\u0432\u0440\u0430\u0442 \u043a \u0441\u0440\u0435\u0434\u043d\u0435\u043c\u0443",
    }
    return labels.get(family, family or "unknown")


def _side_label(side: str) -> str:
    normalized = side.strip().lower()
    if normalized == "long":
        return "LONG"
    if normalized == "short":
        return "SHORT"
    return side.upper() or "UNKNOWN"


def _status_label(status: str) -> str:
    labels = {
        "armed": "\u0436\u0434\u0435\u0442 \u0432\u0445\u043e\u0434\u0430",
        "queued": "\u043f\u043e\u0441\u0442\u0430\u0432\u043b\u0435\u043d \u0432 \u043e\u0447\u0435\u0440\u0435\u0434\u044c main-paper",
        "opened_paper": "\u043e\u0442\u043a\u0440\u044b\u0442 \u0432 paper-\u043d\u0430\u0431\u043b\u044e\u0434\u0435\u043d\u0438\u0438",
        "accepted_for_paper_watch": "\u043f\u0440\u0438\u043d\u044f\u0442 \u043a paper-\u043d\u0430\u0431\u043b\u044e\u0434\u0435\u043d\u0438\u044e",
        "PAPER_FORWARD_READY": "\u043f\u0440\u043e\u0448\u0435\u043b hard validation \u0434\u043b\u044f forward-paper",
        "closed_take": "\u0437\u0430\u043a\u0440\u044b\u0442 \u043f\u043e \u0446\u0435\u043b\u0438",
        "closed_stop": "\u0437\u0430\u043a\u0440\u044b\u0442 \u043f\u043e \u0441\u0442\u043e\u043f\u0443",
        "closed_expired": "\u0438\u0441\u0442\u0435\u043a \u043f\u043e \u0432\u0440\u0435\u043c\u0435\u043d\u0438",
        "provider_error": "\u043e\u0448\u0438\u0431\u043a\u0430 \u043f\u043e\u0441\u0442\u0430\u0432\u0449\u0438\u043a\u0430 \u0434\u0430\u043d\u043d\u044b\u0445",
        "no_data": "\u043d\u0435\u0442 \u0441\u0432\u0435\u0436\u0438\u0445 \u0434\u0430\u043d\u043d\u044b\u0445",
    }
    return labels.get(status, status or "unknown")


def _reason_label(reason: str) -> str:
    side = "LONG" if reason.startswith("long ") else "SHORT" if reason.startswith("short ") else ""
    normalized = reason.removeprefix("long ").removeprefix("short ").strip()
    labels = {
        "continuation, not exhausted; trend over 10 bars": "\u043f\u0440\u043e\u0434\u043e\u043b\u0436\u0435\u043d\u0438\u0435 \u0442\u0440\u0435\u043d\u0434\u0430 \u0431\u0435\u0437 \u043f\u0440\u0438\u0437\u043d\u0430\u043a\u0430 \u0441\u0438\u043b\u044c\u043d\u043e\u0433\u043e \u0438\u0441\u0442\u043e\u0449\u0435\u043d\u0438\u044f",
        "liquidity-sweep + reclaim of structure": "\u0446\u0435\u043d\u0430 \u0441\u043d\u044f\u043b\u0430 \u043b\u0438\u043a\u0432\u0438\u0434\u043d\u043e\u0441\u0442\u044c \u0438 \u0432\u0435\u0440\u043d\u0443\u043b\u0430\u0441\u044c \u043e\u0431\u0440\u0430\u0442\u043d\u043e \u0432 \u0441\u0442\u0440\u0443\u043a\u0442\u0443\u0440\u0443",
        "pullback-continuation; dip into trend": "\u043e\u0442\u043a\u0430\u0442 \u0432\u043d\u0443\u0442\u0440\u0438 \u0442\u0440\u0435\u043d\u0434\u0430, \u0438\u0434\u0435\u044f \u043d\u0430 \u043f\u0440\u043e\u0434\u043e\u043b\u0436\u0435\u043d\u0438\u0435 \u0434\u0432\u0438\u0436\u0435\u043d\u0438\u044f",
        "tactical early-TP scalp; fast in/out": "\u0431\u044b\u0441\u0442\u0440\u044b\u0439 \u0442\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u0432\u0445\u043e\u0434 \u0441 \u0440\u0430\u043d\u043d\u0435\u0439 \u0444\u0438\u043a\u0441\u0430\u0446\u0438\u0435\u0439",
    }
    text = labels.get(normalized, reason or "paper-watch candidate")
    return f"{side}: {text}" if side else text


def _trade_text(record: dict[str, Any]) -> str:
    pair = html.escape(str(record.get("okx_inst_id") or "unknown"))
    timeframe = html.escape(str(record.get("timeframe") or "unknown"))
    side = html.escape(_side_label(str(record.get("side") or "unknown")))
    family = html.escape(_family_label(str(record.get("setup_family") or "unknown")))
    status = html.escape(_status_label(str(record.get("status") or "queued")))
    setup_id = html.escape(str(record.get("ready_strategy_id") or "n/a"))
    signal_id = html.escape(str(record.get("source_signal_id") or "unknown"))
    targets = _targets_from_plan(list(record.get("take_profit_plan") or []))
    outcome = dict(record.get("outcome") or {})
    result_line = ""
    if outcome:
        result_line = (
            f"\n<b>{LABEL_OUTCOME}:</b> <code>{html.escape(str(outcome.get('result') or 'unknown'))}</code>"
            f" net=<code>{html.escape(str(outcome.get('net_pct', 'n/a')))}%</code>"
        )
    return "\n".join(
        [
            f"<b>Main paper: {pair} \u00b7 {timeframe} \u00b7 {side}</b>",
            HUMAN_DISCLAIMER,
            f"<code>{REQUIRED_DISCLAIMER}</code>",
            "",
            f"<b>{LABEL_IDEA}:</b> {family}",
            f"<b>{LABEL_ENTRY}:</b> <code>{_fmt_price(record.get('entry'))}</code>",
            f"<b>{LABEL_STOP}:</b> <code>{_fmt_price(record.get('stop'))}</code>",
            f"<b>{LABEL_TARGETS}:</b> <code>{targets}</code>",
            f"<b>{LABEL_MAX_HOLD}:</b> <code>{html.escape(str(record.get('max_hold_min') or 'n/a'))} \u043c\u0438\u043d</code>",
            "",
            f"<b>{LABEL_STATUS}:</b> {status}",
            f"<b>Setup:</b> <code>{setup_id}</code>",
            f"<b>Signal:</b> <code>{signal_id}</code>",
            result_line,
            "",
            f"<i>{EXECUTION_OFF}</i>",
        ]
    )


def _entry_from_zone(record: dict[str, Any]) -> str:
    zone = record.get("entry_zone") or []
    if isinstance(zone, list) and len(zone) >= 2:
        return f"{_fmt_price(zone[0])}..{_fmt_price(zone[1])}"
    return _fmt_price(record.get("ref_price"))


def _paper_signal_targets(record: dict[str, Any]) -> str:
    plan = record.get("take_profit_plan") or []
    if isinstance(plan, list):
        return _targets_from_plan([item for item in plan if isinstance(item, dict)])
    return "n/a"


def _paper_signal_text(record: dict[str, Any]) -> str:
    symbol = str(record.get("okx_inst_id") or record.get("symbol") or "unknown").replace("_", "-")
    pair = html.escape(symbol)
    timeframe = html.escape(str(record.get("timeframe") or "unknown"))
    side = html.escape(_side_label(str(record.get("side") or "unknown")))
    family = html.escape(_family_label(str(record.get("setup_family") or "unknown")))
    status = html.escape(_status_label(str(record.get("status") or "armed")))
    source = html.escape(str(record.get("source") or "farm"))
    signal_id = html.escape(str(record.get("signal_id") or "unknown"))
    reason = html.escape(_reason_label(str(record.get("reason_now") or "farm paper candidate")))
    risk = html.escape(str(record.get("risk_pct") or "n/a"))
    validation = html.escape(str((record.get("validator_context") or {}).get("hard_status") or "not_hard_validated"))
    return "\n".join(
        [
            f"<b>Farm paper candidate: {pair} \u00b7 {timeframe} \u00b7 {side}</b>",
            HUMAN_DISCLAIMER,
            f"<code>{REQUIRED_DISCLAIMER}</code>",
            "",
            f"<b>{LABEL_IDEA}:</b> {family}",
            f"<b>{LABEL_ENTRY}:</b> <code>{_entry_from_zone(record)}</code>",
            f"<b>{LABEL_STOP}:</b> <code>{_fmt_price(record.get('stop_loss'))}</code>",
            f"<b>{LABEL_TARGETS}:</b> <code>{_paper_signal_targets(record)}</code>",
            f"<b>{LABEL_MAX_HOLD}:</b> <code>{html.escape(str(record.get('max_hold_minutes') or 'n/a'))} \u043c\u0438\u043d</code>",
            "",
            f"<b>{LABEL_REASON}:</b> {reason}",
            f"<b>{LABEL_STATUS}:</b> {status}",
            f"<b>{LABEL_SOURCE}:</b> <code>{source}</code>",
            f"<b>Validation:</b> <code>{validation}</code>",
            f"<b>Risk:</b> <code>{risk}%</code>",
            f"<b>Signal:</b> <code>{signal_id}</code>",
            "",
            f"<i>{EXECUTION_OFF}</i>",
        ]
    )


def _consumer_text(record: dict[str, Any]) -> str:
    contract = dict(record.get("signal_contract") or {})
    meta = dict(contract.get("metadata") or {})
    pair = html.escape(str(record.get("okx_inst_id") or record.get("pair") or "unknown"))
    family = html.escape(_family_label(str(record.get("setup_family") or "unknown")))
    side = html.escape(_side_label(str(record.get("side") or "unknown")))
    timeframe = html.escape(str(record.get("timeframe") or "unknown"))
    reason = html.escape(_reason_label(str(meta.get("reason_now") or "paper-watch candidate")))
    source = html.escape(str(record.get("source_signal_id") or "unknown"))
    source_name = html.escape(str(contract.get("source") or meta.get("source") or "paper_lane"))
    source_status = str(meta.get("source_validation_verdict") or record.get("source_status") or "armed")
    setup_id = html.escape(str(meta.get("ready_strategy_id") or meta.get("setup_id") or meta.get("candidate_id") or "n/a"))
    return "\n".join(
        [
            f"<b>Main paper: {pair} \u00b7 {timeframe} \u00b7 {side}</b>",
            HUMAN_DISCLAIMER,
            f"<code>{REQUIRED_DISCLAIMER}</code>",
            "",
            f"<b>{LABEL_IDEA}:</b> {family}",
            f"<b>{LABEL_ENTRY}:</b> <code>{_fmt_price(contract.get('entry'))}</code>",
            f"<b>{LABEL_STOP}:</b> <code>{_fmt_price(contract.get('stop'))}</code>",
            f"<b>{LABEL_TARGETS}:</b> <code>{_targets_from_contract(contract)}</code>",
            f"<b>{LABEL_MAX_HOLD}:</b> <code>{html.escape(str(contract.get('max_hold_min') or 'n/a'))} \u043c\u0438\u043d</code>",
            "",
            f"<b>{LABEL_REASON}:</b> {reason}",
            f"<b>{LABEL_STATUS}:</b> {html.escape(_status_label(source_status))}",
            f"<b>{LABEL_SOURCE}:</b> <code>{source_name}</code>",
            f"<b>Setup:</b> <code>{setup_id}</code>",
            f"<b>Signal:</b> <code>{source}</code>",
            "",
            f"<i>{EXECUTION_OFF}</i>",
        ]
    )


def render_preview_text(record: dict[str, Any]) -> str:
    if record.get("schema") == "MainPaperTrade.v1":
        return _trade_text(record)
    if record.get("schema") == "PaperSignalCandidate.v1":
        return _paper_signal_text(record)
    return _consumer_text(record)


def validate_preview(record: dict[str, Any], text: str) -> list[str]:
    problems: list[str] = []
    is_trade = record.get("schema") == "MainPaperTrade.v1"
    is_candidate = record.get("schema") == "PaperSignalCandidate.v1"
    if not is_trade and not is_candidate and record.get("consumer_status") != "accepted_for_paper_watch":
        problems.append("consumer_not_accepted")
    if record.get("paper_only") is not True:
        problems.append("paper_only_not_true")
    if record.get("execution_allowed") is not False:
        problems.append("execution_allowed_not_false")
    if len(text) > MAX_MESSAGE_CHARS:
        problems.append("telegram_message_too_long")
    if REQUIRED_DISCLAIMER not in text:
        problems.append("missing_research_disclaimer")
    if "execution_allowed=false" not in text:
        problems.append("missing_execution_boundary")
    if "<script" in text.lower():
        problems.append("unsafe_html")
    if any(marker in text for marker in MOJIBAKE_MARKERS):
        problems.append("mojibake_text")
    return problems


def _load_records(path: Path) -> tuple[list[dict[str, Any]], Path | None]:
    if not path.exists():
        return [], None
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("items") or []), path


def _load_paper_signal_candidates(path: Path) -> tuple[list[dict[str, Any]], Path | None]:
    if not path.exists():
        return [], None
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_rows = data.get("active") or data.get("items") or []
    rows = [row for row in raw_rows if isinstance(row, dict)]
    source_path = path
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("status") or "") not in ACTIONABLE_PAPER_SIGNAL_STATUSES:
            continue
        candidate = dict(row)
        candidate["schema"] = "PaperSignalCandidate.v1"
        candidate.setdefault("paper_only", True)
        candidate.setdefault("execution_allowed", False)
        candidates.append(candidate)
    candidates.sort(
        key=lambda row: (
            0 if str(row.get("status") or "") == "opened_paper" else 1,
            str(row.get("timeframe") or ""),
            str(row.get("setup_family") or ""),
            str(row.get("signal_id") or ""),
        )
    )
    return candidates, source_path


def _load_card_ledger(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    items = data.get("items") or []
    return {
        str(item.get("telegram_card_id")): dict(item)
        for item in items
        if isinstance(item, dict) and str(item.get("telegram_card_id") or "")
    }


def _write_card_ledger(private_root: Path, previews: list[PaperTelegramPreview]) -> dict[str, Any]:
    path = _card_ledger_path(private_root)
    now = time.time()
    ledger = _load_card_ledger(path)
    for preview in previews:
        item = preview.to_dict()
        item["last_seen_at"] = now
        ledger[preview.telegram_card_id] = item
    items = sorted(
        ledger.values(),
        key=lambda item: (str(item.get("source_signal_id") or ""), str(item.get("telegram_card_id") or "")),
    )
    signal_ids = {
        str(item.get("source_signal_id") or "")
        for item in items
        if str(item.get("source_signal_id") or "")
    }
    summary = {
        "schema": "paper_telegram_card_ledger.v1",
        "items": items,
        "cards": len(items),
        "signals": len(signal_ids),
        "paper_only": True,
        "execution_allowed": False,
        "sends_network": False,
        "snapshot_path": str(path),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def build_paper_telegram_preview(private_root: Path, *, limit: int = 20) -> dict[str, Any]:
    rows, source_path = _load_records(_trade_snapshot_path(private_root))
    source_schema = "main_paper_trade_ledger.v1"
    if not rows:
        rows, source_path = _load_records(_consumer_snapshot_path(private_root))
        source_schema = "main_paper_consumer.v1"
    if not rows:
        rows, source_path = _load_paper_signal_candidates(_paper_signal_snapshot_path(private_root))
        source_schema = "paper_signals.v1"

    previews: list[PaperTelegramPreview] = []
    skipped_rejected = 0
    skipped_non_actionable = 0
    for row in rows:
        if source_schema == "main_paper_consumer.v1" and row.get("consumer_status") != "accepted_for_paper_watch":
            skipped_rejected += 1
            continue
        if source_schema == "main_paper_trade_ledger.v1" and str(row.get("status") or "") in NON_ACTIONABLE_TRADE_STATUSES:
            skipped_non_actionable += 1
            continue
        if source_schema == "paper_signals.v1" and str(row.get("status") or "") not in ACTIONABLE_PAPER_SIGNAL_STATUSES:
            skipped_non_actionable += 1
            continue
        if len(previews) >= limit:
            break
        text = render_preview_text(row)
        problems = validate_preview(row, text)
        preview_id = f"preview_{row.get('instruction_id') or row.get('paper_trade_id') or len(previews)}"
        if source_schema == "paper_signals.v1":
            preview_id = f"preview_candidate_{row.get('signal_id') or len(previews)}"
        previews.append(
            PaperTelegramPreview(
                telegram_card_id=f"tgcard_{row.get('source_signal_id') or preview_id}_{_card_hash(text)}",
                preview_id=preview_id,
                instruction_id=str(row.get("instruction_id") or ""),
                source_signal_id=str(row.get("source_signal_id") or row.get("signal_id") or ""),
                pair=str(row.get("okx_inst_id") or row.get("pair") or row.get("symbol") or ""),
                timeframe=str(row.get("timeframe") or ""),
                side=str(row.get("side") or ""),
                setup_family=str(row.get("setup_family") or ""),
                consumer_status=str(row.get("consumer_status") or row.get("status") or ""),
                text=text,
                problems=problems,
            )
        )

    out_jsonl = _jsonl_path(private_root)
    out_snapshot = _snapshot_path(private_root)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for preview in previews:
            fh.write(json.dumps(preview.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    ledger_summary = _write_card_ledger(Path(private_root), previews)

    invalid = sum(1 for preview in previews if preview.problems)
    summary = {
        "schema": SUMMARY_SCHEMA,
        "source_schema": source_schema,
        "source_exists": source_path is not None,
        "source_path": str(source_path) if source_path else "",
        "records_read": len(rows),
        "rendered": len(previews),
        "invalid": invalid,
        "skipped_rejected": skipped_rejected,
        "skipped_non_actionable": skipped_non_actionable,
        "items": [preview.to_dict() for preview in previews],
        "jsonl_path": str(out_jsonl),
        "snapshot_path": str(out_snapshot),
        "paper_only": True,
        "execution_allowed": False,
        "sends_network": False,
        "card_template_version": CARD_TEMPLATE_VERSION,
        "card_ledger_path": ledger_summary["snapshot_path"],
        "card_ledger_cards": ledger_summary["cards"],
        "card_ledger_signals": ledger_summary["signals"],
    }
    out_snapshot.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def load_paper_telegram_preview_summary(private_root: Path) -> dict[str, Any]:
    path = _snapshot_path(private_root)
    if not path.exists():
        return {
            "schema": SUMMARY_SCHEMA,
            "exists": False,
            "rendered": 0,
            "invalid": 0,
            "paper_only": True,
            "execution_allowed": False,
            "sends_network": False,
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    data["exists"] = True
    data.setdefault("paper_only", True)
    data.setdefault("execution_allowed", False)
    data.setdefault("sends_network", False)
    return data
