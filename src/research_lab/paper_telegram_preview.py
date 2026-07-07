"""Offline Telegram-card preview for main paper trades.

The preview is a dry-run surface: it renders operator-facing cards, validates
length/HTML safety, and writes private derived artifacts. It never imports
Telegram senders, never reads tokens or chat IDs, and only fetches public
chart candles when the operator explicitly enables that opt-in.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import textwrap
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from src.research_lab.experiment import choose_symbol_file, load_candles
from src.research_lab.paths import market_data_glob
from src.research_lab.providers.okx_public import MarketDataError, OkxPublicMarketDataProvider, _httpx_get_direct
from src.strategy.chart_renderer import generate_chart_png

SCHEMA = "PaperTelegramPreview.v1"
SUMMARY_SCHEMA = "paper_telegram_preview.v1"
CARD_TEMPLATE_VERSION = "paper_telegram_card_v6_validation_tier_ru"
MAX_MESSAGE_CHARS = 4096
REQUIRED_DISCLAIMER = "\u0411\u0443\u043c\u0430\u0436\u043d\u044b\u0439 \u0440\u0435\u0436\u0438\u043c: \u044d\u0442\u043e \u043d\u0435 \u043e\u0440\u0434\u0435\u0440."
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
LABEL_VALIDATION = "\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430"
EXECUTION_OFF = "\u0410\u0432\u0442\u043e\u0438\u0441\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u0435 \u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u043e."

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
ACTIONABLE_PRODUCT_TRADE_STATUSES = ACTIONABLE_PAPER_SIGNAL_STATUSES
QUALITY_LABEL_RANK = {
    "candidate_watch": 0,
    "mixed": 1,
    "sample_too_small": 2,
    "needs_review": 3,
    "weak_after_costs": 4,
}
SUBSCRIBER_QUALITY_LABELS = frozenset({"candidate_watch", "mixed"})
VALIDATED_TIER = "validated_pfr"
FARM_CALCULATED_TIER = "farm_calculated"
RESEARCH_ONLY_TIER = "research_only"
VALIDATION_TIERS = frozenset({VALIDATED_TIER, FARM_CALCULATED_TIER, RESEARCH_ONLY_TIER})
VALIDATION_TIER_LABELS = {
    VALIDATED_TIER: "\u043f\u0440\u043e\u0448\u0435\u043b PFR/\u0432\u0430\u043b\u0438\u0434\u0430\u0446\u0438\u044e",
    FARM_CALCULATED_TIER: "\u0440\u0430\u0441\u0447\u0435\u0442\u043d\u044b\u0439 \u0441\u0438\u0433\u043d\u0430\u043b \u0444\u0435\u0440\u043c\u044b; \u043f\u043e\u043b\u043d\u044b\u0439 PFR \u043d\u0435 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d",
    RESEARCH_ONLY_TIER: "\u0438\u0441\u0441\u043b\u0435\u0434\u043e\u0432\u0430\u0442\u0435\u043b\u044c\u0441\u043a\u043e\u0435 \u043d\u0430\u0431\u043b\u044e\u0434\u0435\u043d\u0438\u0435",
}


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
    validation_tier: str
    text: str
    chart_path: str = ""
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
        if self.validation_tier not in VALIDATION_TIERS:
            raise ValueError(f"unsupported validation tier {self.validation_tier!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _consumer_snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_consumed.json"


def _trade_snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_trades.json"


def _product_trade_snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_product_trades.json"


def _paper_signal_snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_signals.json"


def _jsonl_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_telegram_preview.jsonl"


def _snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_telegram_preview.json"


def _card_ledger_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_telegram_card_ledger.json"


def _paper_review_chart_path(private_root: Path, source_signal_id: str) -> Path | None:
    if not source_signal_id:
        return None
    safe_id = source_signal_id.replace("/", "_").replace("\\", "_")
    path = Path(private_root) / "state" / "derived" / "paper_reviews" / f"{safe_id}.png"
    return path if path.exists() else None


def _legacy_base_chart_path(private_root: Path, source_signal_id: str) -> Path:
    safe_id = source_signal_id.replace("/", "_").replace("\\", "_") or "unknown"
    return Path(private_root) / "state" / "derived" / "paper_telegram_base_charts" / f"{safe_id}.png"


def _font(size: int):
    try:
        from PIL import ImageFont

        for candidate in ("arial.ttf", "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
        return ImageFont.load_default()
    except ImportError:
        return None


def _draw_wrapped(draw: Any, xy: tuple[int, int], text: str, *, font: Any, fill: str, width: int) -> int:
    x, y = xy
    for raw_line in str(text).splitlines() or [""]:
        for line in textwrap.wrap(raw_line, width=width) or [""]:
            draw.text((x, y), line, font=font, fill=fill)
            y += 19
    return y


def _record_targets_for_card(record: dict[str, Any]) -> str:
    if record.get("schema") == "PaperSignalCandidate.v1":
        return _paper_signal_targets(record)
    if record.get("signal_contract"):
        return _targets_from_contract(dict(record.get("signal_contract") or {}))
    return _targets_from_plan(list(record.get("take_profit_plan") or []))


def _record_entry_for_card(record: dict[str, Any]) -> str:
    if record.get("schema") == "PaperSignalCandidate.v1":
        return _entry_from_zone(record)
    if record.get("signal_contract"):
        return _fmt_price((record.get("signal_contract") or {}).get("entry"))
    return _fmt_price(record.get("entry"))


def _record_stop_for_card(record: dict[str, Any]) -> str:
    if record.get("schema") == "PaperSignalCandidate.v1":
        return _fmt_price(record.get("stop_loss"))
    if record.get("signal_contract"):
        return _fmt_price((record.get("signal_contract") or {}).get("stop"))
    return _fmt_price(record.get("stop"))


def _record_hold_for_card(record: dict[str, Any]) -> str:
    value = record.get("max_hold_min") or record.get("max_hold_minutes")
    if not value and record.get("signal_contract"):
        value = (record.get("signal_contract") or {}).get("max_hold_min")
    return str(value or "n/a")


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _record_entry_for_chart(record: dict[str, Any]) -> float | None:
    if record.get("schema") == "PaperSignalCandidate.v1":
        zone = record.get("entry_zone") or []
        if isinstance(zone, list) and len(zone) >= 2:
            lo = _float_or_none(zone[0])
            hi = _float_or_none(zone[1])
            if lo is not None and hi is not None:
                return (lo + hi) / 2
        return _float_or_none(record.get("entry"))
    if record.get("signal_contract"):
        return _float_or_none((record.get("signal_contract") or {}).get("entry"))
    return _float_or_none(record.get("entry"))


def _record_stop_for_chart(record: dict[str, Any]) -> float | None:
    if record.get("schema") == "PaperSignalCandidate.v1":
        return _float_or_none(record.get("stop_loss"))
    if record.get("signal_contract"):
        return _float_or_none((record.get("signal_contract") or {}).get("stop"))
    return _float_or_none(record.get("stop"))


def _tp_levels_for_chart(record: dict[str, Any]) -> dict[str, Any]:
    levels: dict[str, Any] = {
        "entry_price": _record_entry_for_chart(record),
        "sl": _record_stop_for_chart(record),
    }
    if record.get("schema") == "PaperSignalCandidate.v1":
        plan = record.get("take_profit_plan") or []
    elif record.get("signal_contract"):
        plan = ((record.get("signal_contract") or {}).get("exit_rule") or {}).get("params", {}).get("targets") or []
    else:
        plan = record.get("take_profit_plan") or []
    if isinstance(plan, list):
        for idx, item in enumerate(plan[:2], start=1):
            if isinstance(item, dict):
                levels[f"tp{idx}"] = _float_or_none(item.get("price"))
    return levels


def _chart_direction(side: str) -> str:
    normalized = str(side or "").strip().lower()
    if normalized in {"long", "buy"}:
        return "buy"
    if normalized in {"short", "sell"}:
        return "sell"
    return ""


def _timeframe_ms(timeframe: str) -> int:
    tf = str(timeframe or "").strip().lower()
    units = {"m": 60_000, "h": 60 * 60_000, "d": 24 * 60 * 60_000}
    try:
        return int(tf[:-1]) * units[tf[-1]]
    except (KeyError, ValueError, IndexError):
        return 60 * 60_000


def _record_epoch_ms(record: dict[str, Any]) -> int | None:
    raw = record.get("created_at") or record.get("ts") or record.get("captured_at")
    if raw is None:
        return None
    if isinstance(raw, int | float):
        return int(raw if raw > 10_000_000_000 else raw * 1000)
    text = str(raw).strip()
    if not text:
        return None
    try:
        return int(float(text) if float(text) > 10_000_000_000 else float(text) * 1000)
    except ValueError:
        pass
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _candles_near_record(candles: list[dict[str, Any]], record_ms: int | None, timeframe: str) -> list[dict[str, Any]]:
    if record_ms is None:
        return candles[-120:]
    tf_ms = _timeframe_ms(timeframe)
    max_lag_ms = max(tf_ms * 3, 30 * 60_000)
    before = [row for row in candles if int(row.get("ts") or 0) <= record_ms]
    if not before:
        return []
    if record_ms - int(before[-1].get("ts") or 0) > max_lag_ms:
        return []
    return before[-120:]


def _public_chart_fetch_enabled(override: bool | None = None) -> bool:
    if override is not None:
        return bool(override)
    return str(os.getenv("STRATEGY_LAB_PAPER_TELEGRAM_FETCH_CHART_CANDLES") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _public_chart_candles(
    symbol: str,
    timeframe: str,
    record_ms: int | None,
    *,
    fetch_enabled: bool | None = None,
) -> list[dict[str, Any]]:
    if not _public_chart_fetch_enabled(fetch_enabled):
        return []
    tf_ms = _timeframe_ms(timeframe)
    end_ts = int(record_ms or time.time() * 1000)
    start_ts = end_ts - tf_ms * 140
    provider = OkxPublicMarketDataProvider(timeout=2.0, max_pages=2, sleep_seconds=0.0, http_get=_httpx_get_direct)
    try:
        return provider.fetch_ohlcv(symbol, timeframe, start_ts, end_ts)[-120:]
    except (MarketDataError, OSError, RuntimeError, TimeoutError, ValueError):
        return []


def _prepared_candles_chart_path(
    private_root: Path,
    record: dict[str, Any],
    source_signal_id: str,
    *,
    fetch_public_chart_candles: bool | None = None,
) -> Path | None:
    symbol = str(record.get("okx_inst_id") or record.get("pair") or record.get("symbol") or "").replace("-", "_")
    timeframe = str(record.get("timeframe") or "").strip().lower()
    if not symbol or not timeframe:
        return None
    path = choose_symbol_file(market_data_glob(private_root, timeframe), symbol, timeframe=timeframe)
    prepared_candles: list[dict[str, Any]] = []
    if path is not None:
        try:
            prepared_candles = load_candles(path)
        except Exception:  # noqa: BLE001 - card rendering must not break preview generation
            prepared_candles = []
    record_ms = _record_epoch_ms(record)
    candles = _candles_near_record(prepared_candles, record_ms, timeframe) if len(prepared_candles) >= 30 else []
    if len(candles) < 30:
        candles = _public_chart_candles(
            symbol,
            timeframe,
            record_ms,
            fetch_enabled=fetch_public_chart_candles,
        )
    if len(candles) < 30 and len(prepared_candles) >= 30:
        candles = prepared_candles[-120:]
    if len(candles) < 30:
        return None
    raw = [
        [row.get("ts"), row.get("open"), row.get("high"), row.get("low"), row.get("close"), row.get("vol", 0.0)]
        for row in reversed(candles)
    ]
    out_path = _legacy_base_chart_path(private_root, source_signal_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        generate_chart_png(
            raw,
            {},
            str(record.get("okx_inst_id") or symbol).replace("_", "-"),
            str(record.get("created_at") or record.get("ts") or ""),
            str(out_path),
            llm_levels=_tp_levels_for_chart(record),
            entry_signal="ENTRY",
            direction=_chart_direction(str(record.get("side") or "")),
            trade_style=str(record.get("setup_family") or ""),
            tf_label=timeframe,
        )
    except Exception:  # noqa: BLE001 - fall back to simple review chart
        return None
    return out_path if out_path.exists() else None


def _render_telegram_card_image(
    private_root: Path,
    record: dict[str, Any],
    source_signal_id: str,
    *,
    fetch_public_chart_candles: bool | None = None,
) -> str:
    base_chart = _prepared_candles_chart_path(
        private_root,
        record,
        source_signal_id,
        fetch_public_chart_candles=fetch_public_chart_candles,
    )
    if base_chart is None:
        base_chart = _paper_review_chart_path(private_root, source_signal_id)
    return str(base_chart) if base_chart is not None else ""


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


def _metadata(record: dict[str, Any]) -> dict[str, Any]:
    contract = record.get("signal_contract")
    if isinstance(contract, dict):
        meta = contract.get("metadata")
        if isinstance(meta, dict):
            return meta
    context = record.get("validator_context")
    return context if isinstance(context, dict) else {}


def validation_tier(record: dict[str, Any]) -> str:
    """Classify what the Telegram card is allowed to claim about validation."""
    meta = _metadata(record)
    ready_strategy_id = str(record.get("ready_strategy_id") or meta.get("ready_strategy_id") or "").strip()
    verdict = str(
        record.get("source_validation_verdict") or meta.get("source_validation_verdict") or ""
    ).strip()
    if bool(record.get("live_ready")) or (ready_strategy_id and verdict == "PAPER_FORWARD_READY"):
        return VALIDATED_TIER

    source = str(record.get("source") or "").strip()
    schema = str(record.get("schema") or "").strip()
    origin = str(record.get("origin") or "").strip()
    if origin == "outcome_retest" or source in {"outcome_retest", "retest", "research"}:
        return RESEARCH_ONLY_TIER
    if schema in {"PaperProductTrade.v1", "PaperSignalCandidate.v1"} or source in {"farm", "pfr_farm"}:
        return FARM_CALCULATED_TIER
    return FARM_CALCULATED_TIER


def _validation_line(record: dict[str, Any]) -> str:
    tier = validation_tier(record)
    label = VALIDATION_TIER_LABELS[tier]
    return f"<b>{LABEL_VALIDATION}:</b> {html.escape(label)}"


def _reason_label(reason: str) -> str:
    side = "LONG" if reason.startswith("long ") else "SHORT" if reason.startswith("short ") else ""
    normalized = reason.removeprefix("long ").removeprefix("short ").strip()
    labels = {
        "continuation, not exhausted; trend over 10 bars": "\u043f\u0440\u043e\u0434\u043e\u043b\u0436\u0435\u043d\u0438\u0435 \u0442\u0440\u0435\u043d\u0434\u0430 \u0431\u0435\u0437 \u043f\u0440\u0438\u0437\u043d\u0430\u043a\u0430 \u0441\u0438\u043b\u044c\u043d\u043e\u0433\u043e \u0438\u0441\u0442\u043e\u0449\u0435\u043d\u0438\u044f",
        "liquidity-sweep + reclaim of structure": "\u0446\u0435\u043d\u0430 \u0441\u043d\u044f\u043b\u0430 \u043b\u0438\u043a\u0432\u0438\u0434\u043d\u043e\u0441\u0442\u044c \u0438 \u0432\u0435\u0440\u043d\u0443\u043b\u0430\u0441\u044c \u043e\u0431\u0440\u0430\u0442\u043d\u043e \u0432 \u0441\u0442\u0440\u0443\u043a\u0442\u0443\u0440\u0443",
        "pullback-continuation; dip into trend": "\u043e\u0442\u043a\u0430\u0442 \u0432\u043d\u0443\u0442\u0440\u0438 \u0442\u0440\u0435\u043d\u0434\u0430, \u0438\u0434\u0435\u044f \u043d\u0430 \u043f\u0440\u043e\u0434\u043e\u043b\u0436\u0435\u043d\u0438\u0435 \u0434\u0432\u0438\u0436\u0435\u043d\u0438\u044f",
        "tactical early-TP scalp; fast in/out": "\u0431\u044b\u0441\u0442\u0440\u044b\u0439 \u0442\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u0432\u0445\u043e\u0434 \u0441 \u0440\u0430\u043d\u043d\u0435\u0439 \u0444\u0438\u043a\u0441\u0430\u0446\u0438\u0435\u0439",
    }
    if normalized.startswith("fade exhaustion"):
        text = "\u0434\u0432\u0438\u0436\u0435\u043d\u0438\u0435 \u0440\u0430\u0441\u0442\u044f\u043d\u0443\u043b\u043e\u0441\u044c, \u0438\u0434\u0435\u044f \u043d\u0430 \u043a\u0440\u0430\u0442\u043a\u0438\u0439 \u0432\u043e\u0437\u0432\u0440\u0430\u0442"
    elif normalized.startswith("accepted farm/PFR paper queue item"):
        text = "\u043a\u0430\u043d\u0434\u0438\u0434\u0430\u0442 \u043f\u0440\u043e\u0448\u0435\u043b paper-\u043e\u0447\u0435\u0440\u0435\u0434\u044c \u0444\u0435\u0440\u043c\u044b"
    else:
        text = labels.get(normalized)
    if not text:
        text = "\u0443\u0441\u043b\u043e\u0432\u0438\u044f \u0441\u0446\u0435\u043d\u0430\u0440\u0438\u044f \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u044b \u0440\u0430\u0441\u0447\u0435\u0442\u043d\u043e\u0439 \u0444\u0435\u0440\u043c\u043e\u0439"
    return f"{side}: {text}" if side else text


def _trade_text(record: dict[str, Any]) -> str:
    pair = html.escape(str(record.get("okx_inst_id") or "unknown"))
    timeframe = html.escape(str(record.get("timeframe") or "unknown"))
    side = html.escape(_side_label(str(record.get("side") or "unknown")))
    family = html.escape(_family_label(str(record.get("setup_family") or "unknown")))
    status = html.escape(_status_label(str(record.get("status") or "queued")))
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
            f"<b>\u0411\u0443\u043c\u0430\u0436\u043d\u044b\u0439 \u0441\u0438\u0433\u043d\u0430\u043b: {pair} \u00b7 {timeframe} \u00b7 {side}</b>",
            HUMAN_DISCLAIMER,
            f"<code>{REQUIRED_DISCLAIMER}</code>",
            _validation_line(record),
            "",
            f"<b>{LABEL_IDEA}:</b> {family}",
            f"<b>{LABEL_ENTRY}:</b> <code>{_fmt_price(record.get('entry'))}</code>",
            f"<b>{LABEL_STOP}:</b> <code>{_fmt_price(record.get('stop'))}</code>",
            f"<b>{LABEL_TARGETS}:</b> <code>{targets}</code>",
            f"<b>{LABEL_MAX_HOLD}:</b> <code>{html.escape(str(record.get('max_hold_min') or 'n/a'))} \u043c\u0438\u043d</code>",
            "",
            f"<b>{LABEL_STATUS}:</b> {status}",
            result_line,
            "",
            f"<i>{EXECUTION_OFF}</i>",
        ]
    )


def _product_trade_text(record: dict[str, Any]) -> str:
    pair = html.escape(str(record.get("okx_inst_id") or "unknown"))
    timeframe = html.escape(str(record.get("timeframe") or "unknown"))
    side = html.escape(_side_label(str(record.get("side") or "unknown")))
    family = html.escape(_family_label(str(record.get("setup_family") or "unknown")))
    status = html.escape(_status_label(str(record.get("status") or "armed")))
    reason = html.escape(_reason_label(str(record.get("reason_now") or "paper product candidate")))
    return "\n".join(
        [
            f"<b>\u0411\u0443\u043c\u0430\u0436\u043d\u044b\u0439 \u0441\u0438\u0433\u043d\u0430\u043b: {pair} \u00b7 {timeframe} \u00b7 {side}</b>",
            HUMAN_DISCLAIMER,
            f"<code>{REQUIRED_DISCLAIMER}</code>",
            _validation_line(record),
            "",
            f"<b>{LABEL_IDEA}:</b> {family}",
            f"<b>{LABEL_ENTRY}:</b> <code>{_fmt_price(record.get('entry'))}</code>",
            f"<b>{LABEL_STOP}:</b> <code>{_fmt_price(record.get('stop'))}</code>",
            f"<b>{LABEL_TARGETS}:</b> <code>{_targets_from_plan(list(record.get('take_profit_plan') or []))}</code>",
            f"<b>{LABEL_MAX_HOLD}:</b> <code>{html.escape(str(record.get('max_hold_min') or 'n/a'))} \u043c\u0438\u043d</code>",
            "",
            f"<b>{LABEL_REASON}:</b> {reason}",
            f"<b>{LABEL_STATUS}:</b> {status}",
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
    reason = html.escape(_reason_label(str(record.get("reason_now") or "farm paper candidate")))
    risk = html.escape(str(record.get("risk_pct") or "n/a"))
    return "\n".join(
        [
            f"<b>\u041a\u0430\u043d\u0434\u0438\u0434\u0430\u0442 \u0444\u0435\u0440\u043c\u044b: {pair} \u00b7 {timeframe} \u00b7 {side}</b>",
            HUMAN_DISCLAIMER,
            f"<code>{REQUIRED_DISCLAIMER}</code>",
            _validation_line(record),
            "",
            f"<b>{LABEL_IDEA}:</b> {family}",
            f"<b>{LABEL_ENTRY}:</b> <code>{_entry_from_zone(record)}</code>",
            f"<b>{LABEL_STOP}:</b> <code>{_fmt_price(record.get('stop_loss'))}</code>",
            f"<b>{LABEL_TARGETS}:</b> <code>{_paper_signal_targets(record)}</code>",
            f"<b>{LABEL_MAX_HOLD}:</b> <code>{html.escape(str(record.get('max_hold_minutes') or 'n/a'))} \u043c\u0438\u043d</code>",
            "",
            f"<b>{LABEL_REASON}:</b> {reason}",
            f"<b>{LABEL_STATUS}:</b> {status}",
            f"<b>\u0420\u0438\u0441\u043a:</b> <code>{risk}%</code>",
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
    source_status = str(meta.get("source_validation_verdict") or record.get("source_status") or "armed")
    return "\n".join(
        [
            f"<b>\u0411\u0443\u043c\u0430\u0436\u043d\u044b\u0439 \u0441\u0438\u0433\u043d\u0430\u043b: {pair} \u00b7 {timeframe} \u00b7 {side}</b>",
            HUMAN_DISCLAIMER,
            f"<code>{REQUIRED_DISCLAIMER}</code>",
            _validation_line(record),
            "",
            f"<b>{LABEL_IDEA}:</b> {family}",
            f"<b>{LABEL_ENTRY}:</b> <code>{_fmt_price(contract.get('entry'))}</code>",
            f"<b>{LABEL_STOP}:</b> <code>{_fmt_price(contract.get('stop'))}</code>",
            f"<b>{LABEL_TARGETS}:</b> <code>{_targets_from_contract(contract)}</code>",
            f"<b>{LABEL_MAX_HOLD}:</b> <code>{html.escape(str(contract.get('max_hold_min') or 'n/a'))} \u043c\u0438\u043d</code>",
            "",
            f"<b>{LABEL_REASON}:</b> {reason}",
            f"<b>{LABEL_STATUS}:</b> {html.escape(_status_label(source_status))}",
            "",
            f"<i>{EXECUTION_OFF}</i>",
        ]
    )


def render_preview_text(record: dict[str, Any]) -> str:
    if record.get("schema") == "MainPaperTrade.v1":
        return _trade_text(record)
    if record.get("schema") == "PaperProductTrade.v1":
        return _product_trade_text(record)
    if record.get("schema") == "PaperSignalCandidate.v1":
        return _paper_signal_text(record)
    return _consumer_text(record)


def validate_preview(record: dict[str, Any], text: str) -> list[str]:
    problems: list[str] = []
    is_trade = record.get("schema") == "MainPaperTrade.v1"
    is_product_trade = record.get("schema") == "PaperProductTrade.v1"
    is_candidate = record.get("schema") == "PaperSignalCandidate.v1"
    if (
        not is_trade
        and not is_product_trade
        and not is_candidate
        and record.get("consumer_status") != "accepted_for_paper_watch"
    ):
        problems.append("consumer_not_accepted")
    if record.get("paper_only") is not True:
        problems.append("paper_only_not_true")
    if record.get("execution_allowed") is not False:
        problems.append("execution_allowed_not_false")
    if len(text) > MAX_MESSAGE_CHARS:
        problems.append("telegram_message_too_long")
    if REQUIRED_DISCLAIMER not in text:
        problems.append("missing_research_disclaimer")
    if EXECUTION_OFF not in text:
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


def _quality_report_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_product_quality_report.json"


def _family_quality(private_root: Path) -> dict[str, dict[str, Any]]:
    path = _quality_report_path(private_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in data.get("families") or []:
        if isinstance(item, dict) and str(item.get("family") or ""):
            out[str(item["family"])] = item
    return out


def _product_preview_rank(row: dict[str, Any], family_quality: dict[str, dict[str, Any]]) -> tuple[Any, ...]:
    family = str(row.get("setup_family") or "")
    quality = family_quality.get(family) or {}
    label = str(quality.get("quality_label") or "sample_too_small")
    return (
        0 if bool(row.get("live_ready")) else 1,
        QUALITY_LABEL_RANK.get(label, 9),
        0 if str(row.get("status") or "") == "opened_paper" else 1,
        -int(quality.get("rows") or 0),
        str(row.get("timeframe") or ""),
        family,
        str(row.get("source_signal_id") or ""),
    )


def _subscriber_quality_problem(row: dict[str, Any], family_quality: dict[str, dict[str, Any]]) -> str:
    if bool(row.get("live_ready")):
        return ""
    family = str(row.get("setup_family") or "")
    quality = family_quality.get(family) or {}
    label = str(quality.get("quality_label") or "sample_too_small")
    if label in SUBSCRIBER_QUALITY_LABELS:
        return ""
    return f"quality_label:{label}"


def _rank_product_preview_rows(private_root: Path, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    family_quality = _family_quality(private_root)
    if not family_quality:
        return rows, False
    return sorted(rows, key=lambda row: _product_preview_rank(row, family_quality)), True


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


def build_paper_telegram_preview(
    private_root: Path,
    *,
    limit: int = 20,
    fetch_public_chart_candles: bool | None = None,
) -> dict[str, Any]:
    rows, source_path = _load_records(_trade_snapshot_path(private_root))
    source_schema = "main_paper_trade_ledger.v1"
    if not rows:
        rows, source_path = _load_records(_product_trade_snapshot_path(private_root))
        source_schema = "paper_product_trade_ledger.v1"
    if not rows:
        rows, source_path = _load_records(_consumer_snapshot_path(private_root))
        source_schema = "main_paper_consumer.v1"
    if not rows:
        rows, source_path = _load_paper_signal_candidates(_paper_signal_snapshot_path(private_root))
        source_schema = "paper_signals.v1"

    previews: list[PaperTelegramPreview] = []
    skipped_rejected = 0
    skipped_non_actionable = 0
    skipped_quality_gate = 0
    quality_gate_reasons: dict[str, int] = {}
    quality_ranked = False
    family_quality: dict[str, dict[str, Any]] = {}
    if source_schema == "paper_product_trade_ledger.v1":
        family_quality = _family_quality(Path(private_root))
        rows, quality_ranked = _rank_product_preview_rows(Path(private_root), rows)
    for row in rows:
        if source_schema == "main_paper_consumer.v1" and row.get("consumer_status") != "accepted_for_paper_watch":
            skipped_rejected += 1
            continue
        if source_schema == "main_paper_trade_ledger.v1" and str(row.get("status") or "") in NON_ACTIONABLE_TRADE_STATUSES:
            skipped_non_actionable += 1
            continue
        if source_schema == "paper_product_trade_ledger.v1" and (
            str(row.get("status") or "") not in ACTIONABLE_PRODUCT_TRADE_STATUSES
        ):
            skipped_non_actionable += 1
            continue
        if source_schema == "paper_product_trade_ledger.v1" and quality_ranked:
            problem = _subscriber_quality_problem(row, family_quality)
            if problem:
                skipped_quality_gate += 1
                quality_gate_reasons[problem] = quality_gate_reasons.get(problem, 0) + 1
                continue
        if source_schema == "paper_signals.v1" and str(row.get("status") or "") not in ACTIONABLE_PAPER_SIGNAL_STATUSES:
            skipped_non_actionable += 1
            continue
        if len(previews) >= limit:
            break
        tier = validation_tier(row)
        text = render_preview_text(row)
        problems = validate_preview(row, text)
        preview_id = f"preview_{row.get('instruction_id') or row.get('paper_trade_id') or len(previews)}"
        if source_schema == "paper_signals.v1":
            preview_id = f"preview_candidate_{row.get('signal_id') or len(previews)}"
        source_signal_id = str(row.get("source_signal_id") or row.get("signal_id") or "")
        previews.append(
            PaperTelegramPreview(
                telegram_card_id=f"tgcard_{source_signal_id or preview_id}_{_card_hash(text)}",
                preview_id=preview_id,
                instruction_id=str(row.get("instruction_id") or ""),
                source_signal_id=source_signal_id,
                pair=str(row.get("okx_inst_id") or row.get("pair") or row.get("symbol") or ""),
                timeframe=str(row.get("timeframe") or ""),
                side=str(row.get("side") or ""),
                setup_family=str(row.get("setup_family") or ""),
                consumer_status=str(row.get("consumer_status") or row.get("status") or ""),
                validation_tier=tier,
                text=text,
                chart_path=_render_telegram_card_image(
                    Path(private_root),
                    row,
                    source_signal_id,
                    fetch_public_chart_candles=fetch_public_chart_candles,
                ),
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
    by_validation_tier: dict[str, int] = {}
    chart_path_types: dict[str, int] = {}
    for preview in previews:
        by_validation_tier[preview.validation_tier] = by_validation_tier.get(preview.validation_tier, 0) + 1
        chart_type = Path(preview.chart_path).parent.name if preview.chart_path else "missing"
        chart_path_types[chart_type] = chart_path_types.get(chart_type, 0) + 1
    summary = {
        "schema": SUMMARY_SCHEMA,
        "source_schema": source_schema,
        "source_exists": source_path is not None,
        "source_path": str(source_path) if source_path else "",
        "records_read": len(rows),
        "rendered": len(previews),
        "invalid": invalid,
        "charts_available": sum(1 for preview in previews if preview.chart_path),
        "skipped_rejected": skipped_rejected,
        "skipped_non_actionable": skipped_non_actionable,
        "skipped_quality_gate": skipped_quality_gate,
        "quality_gate_reasons": quality_gate_reasons,
        "quality_ranked": quality_ranked,
        "by_validation_tier": by_validation_tier,
        "chart_path_types": dict(sorted(chart_path_types.items())),
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
