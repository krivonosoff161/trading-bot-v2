# -*- coding: utf-8 -*-
"""Immutable evidence envelope joining one research subject end to end."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.feature_packet import FeaturePacket, OutcomeFeaturePacket
from src.research_lab.lineage_contract import ScannerEvent, append_jsonl, utc_now
from src.research_lab.market_data_packet import MarketDataPacket

SCHEMA = "ResearchEnvelope.v1"


def envelope_dir(private_root: str | Path) -> Path:
    return Path(private_root) / "state" / "lineage" / "research_envelopes"


def envelope_index_path(private_root: str | Path) -> Path:
    return Path(private_root) / "state" / "lineage" / "research_envelopes.jsonl"


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:20]
    except OSError:
        return "missing"


def _envelope_id(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return f"re_{hashlib.sha256(canonical).hexdigest()}"


def research_code_identity() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    return {
        "feature_contract": _file_hash(root / "src" / "research_lab" / "feature_packet.py"),
        "simulator": _file_hash(root / "src" / "research_lab" / "experiment.py"),
        "strategy_registry": _file_hash(root / "src" / "research_lab" / "strategy_registry.py"),
        "cost_model": _file_hash(root / "src" / "research_lab" / "trade_math.py"),
    }


@dataclass(frozen=True)
class ResearchEnvelope:
    research_envelope_id: str
    stage: str
    source_event_id: str
    scanner_event_id: str
    symbol: str
    instrument: str
    timeframe: str
    identities: dict[str, str]
    evidence: dict[str, Any]
    code_identity: dict[str, str]
    evidence_status: str
    invalid_reasons: tuple[str, ...] = ()
    parent_envelope_id: str = ""
    created_at: str = field(default_factory=utc_now)
    paper_only: bool = True
    execution_allowed: bool = False
    schema: str = SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_decision_envelope(
    scanner_event: ScannerEvent,
    data_packet: MarketDataPacket,
    decision_packet: FeaturePacket,
    *,
    setup_candidate_id: str = "",
    sweep_run_id: str = "",
    validation_id: str = "",
    paper_signal_id: str = "",
) -> ResearchEnvelope:
    reasons: list[str] = []
    if not scanner_event.source_event_id:
        reasons.append("missing_source_event_id")
    if not scanner_event.source_content_hash:
        reasons.append("missing_source_content_hash")
    if not data_packet.content_hash:
        reasons.append("missing_data_content_hash")
    if not data_packet.snapshot_manifest_id:
        reasons.append("missing_data_snapshot_manifest_id")
    if data_packet.snapshot_manifest.get("provenance_status") != "complete":
        reasons.append("data_availability_provenance_unknown")
    if not decision_packet.no_lookahead:
        reasons.append("decision_packet_not_causal")
    identities = {
        "data_packet_id": data_packet.data_packet_id,
        "data_snapshot_manifest_id": data_packet.snapshot_manifest_id,
        "decision_packet_id": decision_packet.feature_packet_id,
        "setup_candidate_id": str(setup_candidate_id),
        "sweep_run_id": str(sweep_run_id),
        "validation_id": str(validation_id),
        "paper_signal_id": str(paper_signal_id),
    }
    evidence = {
        "source_available_at": scanner_event.source_available_at,
        "source_content_hash": scanner_event.source_content_hash,
        "data_content_hash": data_packet.content_hash,
        "data_as_of_ts": data_packet.as_of_ts,
        "data_available_at": data_packet.available_at,
        "data_snapshot_manifest": dict(data_packet.snapshot_manifest),
        "decision_as_of_ts": decision_packet.data_quality.get("as_of_ts"),
        "outcome_packet_id": "",
    }
    code_identity = research_code_identity()
    identity_payload = {
        "stage": "decision",
        "source_event_id": scanner_event.source_event_id,
        "scanner_event_id": scanner_event.scanner_event_id,
        "identities": identities,
        "evidence": evidence,
        "code_identity": code_identity,
        "invalid_reasons": reasons,
    }
    return ResearchEnvelope(
        research_envelope_id=_envelope_id(identity_payload),
        stage="decision",
        source_event_id=scanner_event.source_event_id,
        scanner_event_id=scanner_event.scanner_event_id,
        symbol=data_packet.symbol,
        instrument=data_packet.instrument,
        timeframe=data_packet.timeframe,
        identities=identities,
        evidence=evidence,
        code_identity=code_identity,
        evidence_status="valid" if not reasons else "invalid_evidence",
        invalid_reasons=tuple(reasons),
    )


def extend_with_outcome(
    parent: ResearchEnvelope, outcome_packet: OutcomeFeaturePacket,
) -> ResearchEnvelope:
    reasons = list(parent.invalid_reasons)
    if outcome_packet.decision_packet_id != parent.identities.get("decision_packet_id"):
        reasons.append("outcome_decision_identity_mismatch")
    identities = {**parent.identities, "outcome_packet_id": outcome_packet.outcome_packet_id}
    evidence = {
        **parent.evidence,
        "outcome_packet_id": outcome_packet.outcome_packet_id,
        "future_evidence_id": outcome_packet.future_evidence_id,
        "outcome_temporal_provenance": dict(outcome_packet.temporal_provenance),
        "outcome_label_quality": dict(outcome_packet.label_quality),
    }
    payload = {
        "stage": "outcome", "parent": parent.research_envelope_id,
        "identities": identities, "evidence": evidence,
        "invalid_reasons": sorted(set(reasons)),
    }
    return ResearchEnvelope(
        research_envelope_id=_envelope_id(payload),
        stage="outcome",
        source_event_id=parent.source_event_id,
        scanner_event_id=parent.scanner_event_id,
        symbol=parent.symbol,
        instrument=parent.instrument,
        timeframe=parent.timeframe,
        identities=identities,
        evidence=evidence,
        code_identity=dict(parent.code_identity),
        evidence_status="valid" if not reasons else "invalid_evidence",
        invalid_reasons=tuple(sorted(set(reasons))),
        parent_envelope_id=parent.research_envelope_id,
    )


def write_research_envelope(private_root: str | Path, envelope: ResearchEnvelope) -> Path:
    path = envelope_dir(private_root) / f"{envelope.research_envelope_id}.json"
    payload = json.dumps(envelope.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != payload:
            raise ValueError("immutable research envelope id collision")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)
    append_jsonl(
        envelope_index_path(private_root),
        {
            "schema": "ResearchEnvelopeIndex.v1",
            "research_envelope_id": envelope.research_envelope_id,
            "parent_envelope_id": envelope.parent_envelope_id,
            "stage": envelope.stage,
            "source_event_id": envelope.source_event_id,
            "scanner_event_id": envelope.scanner_event_id,
            "symbol": envelope.symbol,
            "timeframe": envelope.timeframe,
            "evidence_status": envelope.evidence_status,
            "path": str(path),
            "paper_only": True,
            "execution_allowed": False,
        },
    )
    return path
