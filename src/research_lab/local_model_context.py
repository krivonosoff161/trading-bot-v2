"""Versioned public-safe tools and deterministic retrieval for the local advisor."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

CONTEXT_SCHEMA = "LocalModelContext.v1"
TOOLSET_VERSION = "calculator_advisory_tools.v1"
RAG_VERSION = "calculator_public_rag.v1"
MAX_CHUNKS = 3
MAX_CHARS_PER_CHUNK = 900

PUBLIC_RAG_PATHS = (
    "docs/llm_proposal_contract.md",
    "docs/adaptive-research-center-contract.md",
    "docs/local_calculator_swarm_2026-07-10.md",
)

ADVISORY_TOOLS = (
    {
        "name": "read_decision_features",
        "input": "DecisionFeaturePacket.v1",
        "effect": "read_only",
    },
    {
        "name": "suggest_bounded_dimension",
        "input": "one allowed sweep dimension",
        "effect": "proposal_only",
    },
    {
        "name": "report_missing_evidence",
        "input": "bounded missing-data labels",
        "effect": "proposal_only",
    },
)


def _sha256(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-zА-Яа-я0-9_]{4,}", text)}


def build_local_model_context(role_id: str, query: str = "") -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    wanted = _tokens(f"{role_id} {query}")
    candidates: list[tuple[int, str, str]] = []
    documents: list[dict[str, str]] = []
    for relative in PUBLIC_RAG_PATHS:
        path = root / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        document_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        documents.append({"path": relative, "sha256": document_hash})
        for index, paragraph in enumerate(re.split(r"\n\s*\n", text)):
            compact = " ".join(paragraph.split())[:MAX_CHARS_PER_CHUNK]
            if not compact:
                continue
            score = len(wanted & _tokens(compact))
            chunk_id = f"rag_{_sha256({'path': relative, 'index': index, 'text': compact})}"
            candidates.append((score, chunk_id, compact))
    candidates.sort(key=lambda row: (-row[0], row[1]))
    chunks = [
        {"chunk_id": chunk_id, "text": text}
        for _score, chunk_id, text in candidates[:MAX_CHUNKS]
    ]
    manifest = {
        "rag_version": RAG_VERSION,
        "documents": sorted(documents, key=lambda row: row["path"]),
        "chunk_ids": [row["chunk_id"] for row in chunks],
    }
    return {
        "schema": CONTEXT_SCHEMA,
        "role_id": role_id,
        "toolset_version": TOOLSET_VERSION,
        "toolset_hash": _sha256(ADVISORY_TOOLS),
        "tools": list(ADVISORY_TOOLS),
        "rag_version": RAG_VERSION,
        "rag_manifest_hash": _sha256(manifest),
        "retrieved_chunks": chunks,
        "forbidden_effects": [
            "change_trade_numbers",
            "set_validator_verdict",
            "execute_order",
            "control_process",
        ],
        "paper_only": True,
        "execution_allowed": False,
    }
