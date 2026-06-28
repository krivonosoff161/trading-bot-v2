"""Offline Telegram-card preview for paper-watch instructions.

The preview is a dry-run surface: it reads the paper-only main consumer audit,
renders operator-facing cards, validates length/HTML safety, and writes private
derived artifacts. It never imports Telegram senders, never reads tokens or chat
IDs, and never sends network requests.
"""

from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA = "PaperTelegramPreview.v1"
SUMMARY_SCHEMA = "paper_telegram_preview.v1"
MAX_MESSAGE_CHARS = 4096
REQUIRED_DISCLAIMER = "research-only, not an order"
HUMAN_DISCLAIMER = "Это paper-наблюдение, не ордер и не команда к входу."


@dataclass(frozen=True)
class PaperTelegramPreview:
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


def _jsonl_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_telegram_preview.jsonl"


def _snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_telegram_preview.json"


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


def _targets(contract: dict[str, Any]) -> str:
    targets = ((contract.get("exit_rule") or {}).get("params") or {}).get("targets") or []
    rendered = []
    for item in targets[:3]:
        label = html.escape(str(item.get("label") or "tp"))
        rendered.append(f"{label}={_fmt_price(item.get('price'))}")
    return ", ".join(rendered) or "n/a"


def _family_label(family: str) -> str:
    labels = {
        "early_tp_tactical": "быстрый тактический TP",
        "reversal_fade": "отбой после растяжения",
        "liquidity_sweep_reclaim": "снятие ликвидности и возврат",
        "continuation": "продолжение движения",
        "pullback_continuation": "откат по тренду",
        "pfr_momentum_breakout": "PFR импульсный пробой",
        "pfr_mean_reversion_fade": "PFR возврат к среднему",
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
        "armed": "ждет входа",
        "opened_paper": "открыт в paper-наблюдении",
        "accepted_for_paper_watch": "принят к paper-наблюдению",
    }
    return labels.get(status, status or "unknown")


def _reason_label(reason: str) -> str:
    side = "LONG" if reason.startswith("long ") else "SHORT" if reason.startswith("short ") else ""
    normalized = reason.removeprefix("long ").removeprefix("short ").strip()
    labels = {
        "continuation, not exhausted; trend over 10 bars": "продолжение тренда без признака сильного истощения",
        "liquidity-sweep + reclaim of structure": "цена сняла ликвидность и вернулась обратно в структуру",
        "pullback-continuation; dip into trend": "откат внутри тренда, идея на продолжение движения",
        "tactical early-TP scalp; fast in/out": "быстрый тактический вход с ранней фиксацией",
    }
    text = labels.get(normalized, reason or "paper-watch candidate")
    return f"{side}: {text}" if side else text


def render_preview_text(record: dict[str, Any]) -> str:
    contract = dict(record.get("signal_contract") or {})
    meta = dict(contract.get("metadata") or {})
    pair = html.escape(str(record.get("okx_inst_id") or record.get("pair") or "unknown"))
    family = html.escape(_family_label(str(record.get("setup_family") or "unknown")))
    side = html.escape(_side_label(str(record.get("side") or "unknown")))
    timeframe = html.escape(str(record.get("timeframe") or "unknown"))
    entry = _fmt_price(contract.get("entry"))
    stop = _fmt_price(contract.get("stop"))
    max_hold = html.escape(str(contract.get("max_hold_min") or "n/a"))
    reason = html.escape(_reason_label(str(meta.get("reason_now") or "paper-watch candidate")))
    source = html.escape(str(record.get("source_signal_id") or "unknown"))
    source_name = html.escape(str(contract.get("source") or meta.get("source") or record.get("source") or "paper_lane"))
    source_status = str(meta.get("source_validation_verdict") or record.get("source_status") or "armed")
    source_verdict = html.escape(_status_label(source_status))
    setup_id = html.escape(str(meta.get("setup_id") or meta.get("candidate_id") or "n/a"))
    return "\n".join(
        [
            f"<b>Paper-сетап: {pair} · {timeframe} · {side}</b>",
            HUMAN_DISCLAIMER,
            f"<code>{REQUIRED_DISCLAIMER}</code>",
            "",
            f"<b>Идея:</b> {family}",
            f"<b>Вход:</b> <code>{entry}</code>",
            f"<b>Стоп:</b> <code>{stop}</code>",
            f"<b>Цели:</b> <code>{_targets(contract)}</code>",
            f"<b>Макс. удержание:</b> <code>{max_hold} мин</code>",
            "",
            f"<b>Почему сейчас:</b> {reason}",
            f"<b>Статус:</b> {source_verdict}",
            f"<b>Источник:</b> <code>{source_name}</code>",
            f"<b>Setup:</b> <code>{setup_id}</code>",
            f"<b>Signal:</b> <code>{source}</code>",
            "",
            "<i>Автоисполнение выключено: execution_allowed=false</i>",
        ]
    )


def validate_preview(record: dict[str, Any], text: str) -> list[str]:
    problems: list[str] = []
    if record.get("consumer_status") != "accepted_for_paper_watch":
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
    return problems


def _load_consumer_records(private_root: Path) -> tuple[list[dict[str, Any]], Path | None]:
    path = _consumer_snapshot_path(private_root)
    if not path.exists():
        return [], None
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("items") or []), path


def build_paper_telegram_preview(private_root: Path, *, limit: int = 20) -> dict[str, Any]:
    rows, source_path = _load_consumer_records(private_root)
    previews: list[PaperTelegramPreview] = []
    skipped_rejected = 0
    for row in rows:
        if row.get("consumer_status") != "accepted_for_paper_watch":
            skipped_rejected += 1
            continue
        if len(previews) >= limit:
            break
        text = render_preview_text(row)
        problems = validate_preview(row, text)
        previews.append(
            PaperTelegramPreview(
                preview_id=f"preview_{row.get('instruction_id') or len(previews)}",
                instruction_id=str(row.get("instruction_id") or ""),
                source_signal_id=str(row.get("source_signal_id") or ""),
                pair=str(row.get("okx_inst_id") or row.get("pair") or ""),
                timeframe=str(row.get("timeframe") or ""),
                side=str(row.get("side") or ""),
                setup_family=str(row.get("setup_family") or ""),
                consumer_status=str(row.get("consumer_status") or ""),
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

    invalid = sum(1 for preview in previews if preview.problems)
    summary = {
        "schema": SUMMARY_SCHEMA,
        "source_schema": "main_paper_consumer.v1",
        "source_exists": source_path is not None,
        "source_path": str(source_path) if source_path else "",
        "records_read": len(rows),
        "rendered": len(previews),
        "invalid": invalid,
        "skipped_rejected": skipped_rejected,
        "limit": int(limit),
        "max_message_chars": MAX_MESSAGE_CHARS,
        "paper_only": True,
        "execution_allowed": False,
        "sends_network": False,
        "jsonl_path": str(out_jsonl),
        "snapshot_path": str(out_snapshot),
    }
    payload = {**summary, "items": [preview.to_dict() for preview in previews]}
    out_snapshot.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary
