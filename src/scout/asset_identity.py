# -*- coding: utf-8 -*-
"""
asset_identity.py — deterministic asset identity resolver (0 LLM, 0 network).

Before routing to a layer, resolve WHAT the object IS:
  - ticker/text → asset_class, layer, baseline, confidence, trigger_role
  - determines requires_context and context_requirements
  - catches equity/pre-IPO/tokenized before they fall to L2

This is the "identity layer" between raw ticker extraction and layer routing.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

_CFG = Path(__file__).resolve().parent / "config" / "asset_identity.yaml"


@lru_cache(maxsize=1)
def _cfg() -> dict:
    return yaml.safe_load((_CFG).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _equity_names_index() -> dict[str, dict]:
    """Build lookup: uppercase alias → {sym, asset_class}."""
    idx: dict[str, dict] = {}
    for entry in _cfg().get("equity_names", []):
        for name in entry.get("names", []):
            idx[name.upper()] = {"sym": entry["sym"], "asset_class": entry["asset_class"]}
    return idx


@lru_cache(maxsize=1)
def _tokenized_index() -> dict[str, dict]:
    return _cfg().get("tokenized_tickers", {})


@lru_cache(maxsize=1)
def _equity_context_re() -> re.Pattern:
    keywords = _cfg().get("equity_context_keywords", [])
    if not keywords:
        return re.compile(r"$^", re.I)
    return re.compile(r"\b(" + "|".join(keywords) + r")\b", re.I)


def identify_asset(
    ticker: str,
    text: str = "",
    source_metadata: dict | None = None,
) -> dict:
    """Resolve asset identity from ticker + surrounding text + source metadata.

    Returns:
        symbol, asset_class, layer, baseline, okx_inst, confidence, reason,
        requires_context, context_requirements, trigger_role
    """
    cfg = _cfg()
    sym = (ticker or "").upper().strip()
    low_text = (text or "").lower()
    source_kind = str((source_metadata or {}).get("telegram_kind") or "").strip().lower()
    is_listing_source = source_kind == "listing"

    # 1) Check tokenized tickers first (SPCX, SPCXX, etc.)
    tokenized = _tokenized_index()
    if sym in tokenized:
        t = tokenized[sym]
        layer = int(t.get("layer") or 5)
        return {
            "symbol": sym,
            "asset_class": "tokenized_equity",
            "layer": layer,
            "baseline": cfg.get("baseline_by_layer", {}).get(layer),
            "okx_inst": f"{sym}-USDT-SWAP",
            "confidence": 0.95,
            "reason": f"known_tokenized_ticker:{sym}",
            "requires_context": True,
            "context_requirements": cfg.get("context_requirements", {}).get("tokenized_equity", []),
            "trigger_role": "needs_context",
        }

    # 2) Check equity name index (strong aliases like "spacex", "openai", "nvidia")
    equity_idx = _equity_names_index()
    # Check in the ticker itself and first 12 words of text
    search_zone = low_text[:500]
    for alias, info in equity_idx.items():
        if alias == sym or re.search(r"\b" + re.escape(alias) + r"\b", search_zone):
            asset_class = info["asset_class"]
            layer = cfg.get("layer_by_asset_class", {}).get(asset_class, 5)
            return {
                "symbol": info["sym"],
                "asset_class": asset_class,
                "layer": layer,
                "baseline": cfg.get("baseline_by_layer", {}).get(layer),
                "okx_inst": f"{info['sym']}-USDT-SWAP",
                "confidence": 0.90,
                "reason": f"equity_alias_match:{alias}",
                "requires_context": asset_class in ("pre_ipo_equity", "tokenized_equity"),
                "context_requirements": cfg.get("context_requirements", {}).get(asset_class, []),
                "trigger_role": "needs_context" if asset_class in ("pre_ipo_equity", "tokenized_equity") else "signal",
            }

    # 3) Check equity context keywords in text (unknown ticker + equity context → likely tokenized/equity)
    eq_re = _equity_context_re()
    if eq_re.search(low_text):
        layer = 5
        return {
            "symbol": sym,
            "asset_class": "tokenized_equity",
            "layer": layer,
            "baseline": cfg.get("baseline_by_layer", {}).get(layer),
            "okx_inst": f"{sym}-USDT-SWAP",
            "confidence": 0.70,
            "reason": "equity_context_keywords_in_text",
            "requires_context": True,
            "context_requirements": cfg.get("context_requirements", {}).get("tokenized_equity", []),
            "trigger_role": "needs_context",
        }

    # 4) Liquidation flow
    if source_kind == "liquidations":
        # Determine underlying asset from known major/alt tickers
        from src.scout.router import classify_layer
        layer = classify_layer(sym)
        asset_class = "liquidation_flow"
        return {
            "symbol": sym,
            "asset_class": asset_class,
            "layer": layer,
            "baseline": cfg.get("baseline_by_layer", {}).get(layer),
            "okx_inst": f"{sym}-USDT-SWAP",
            "confidence": 0.85,
            "reason": "liquidation_flow_source",
            "requires_context": True,
            "context_requirements": ["squeeze_confirmation", "liquidity_edge_evidence"],
            "trigger_role": "context_trigger",
        }

    # 5) Default: try router classify_layer for known crypto
    from src.scout.router import classify_layer, tracked_assets
    layer = classify_layer(sym)

    # Check if the symbol is explicitly in entities list OR in layer_map
    known_symbols = {a["sym"].upper() for a in tracked_assets()}
    # Also check layer_map directly (open universe: AVGO, TSM, NFLX, etc.)
    from src.scout.router import _entities as _ent_fn
    layer_map_symbols: set[str] = set()
    for _ln, _syms in (_ent_fn().get("layer_map") or {}).items():
        for _s in _syms:
            layer_map_symbols.add(str(_s).upper())
    is_explicitly_known = sym.upper() in known_symbols or sym.upper() in layer_map_symbols

    if layer == 1 and is_explicitly_known:
        return {
            "symbol": sym,
            "asset_class": "crypto_major",
            "layer": 1,
            "baseline": cfg.get("baseline_by_layer", {}).get(1),
            "okx_inst": f"{sym}-USDT-SWAP",
            "confidence": 0.85,
            "reason": "known_major_in_entities",
            "requires_context": False,
            "context_requirements": [],
            "trigger_role": "signal",
        }

    if layer == 2 and is_explicitly_known:
        return {
            "symbol": sym,
            "asset_class": "crypto_alt",
            "layer": 2,
            "baseline": cfg.get("baseline_by_layer", {}).get(2),
            "okx_inst": f"{sym}-USDT-SWAP",
            "confidence": 0.75,
            "reason": "known_alt_in_entities",
            "requires_context": False,
            "context_requirements": [],
            "trigger_role": "signal",
        }

    if layer == 3 and is_explicitly_known:
        return {
            "symbol": sym,
            "asset_class": "commodity",
            "layer": 3,
            "baseline": cfg.get("baseline_by_layer", {}).get(3),
            "okx_inst": f"{sym}-USDT-SWAP",
            "confidence": 0.80,
            "reason": "known_commodity_in_entities",
            "requires_context": False,
            "context_requirements": [],
            "trigger_role": "signal",
        }

    if layer == 4 and is_explicitly_known:
        return {
            "symbol": sym,
            "asset_class": "energy",
            "layer": 4,
            "baseline": cfg.get("baseline_by_layer", {}).get(4),
            "okx_inst": f"{sym}-USDT-SWAP",
            "confidence": 0.80,
            "reason": "known_energy_in_entities",
            "requires_context": False,
            "context_requirements": [],
            "trigger_role": "signal",
        }

    if layer == 5 and is_explicitly_known:
        return {
            "symbol": sym,
            "asset_class": "equity",
            "layer": 5,
            "baseline": cfg.get("baseline_by_layer", {}).get(5),
            "okx_inst": f"{sym}-USDT-SWAP",
            "confidence": 0.80,
            "reason": "known_equity_in_entities",
            "requires_context": False,
            "context_requirements": [],
            "trigger_role": "signal",
        }

    # 6) Unknown ticker — NOT defaulting to L2 anymore for non-listing sources.
    # For listing sources: unknown ticker without equity context → new crypto listing (L2).
    # For non-listing sources: unknown ticker needs context confirmation.
    if is_listing_source:
        return {
            "symbol": sym,
            "asset_class": "crypto_alt",
            "layer": 2,
            "baseline": cfg.get("baseline_by_layer", {}).get(2),
            "okx_inst": f"{sym}-USDT-SWAP",
            "confidence": 0.65,
            "reason": "new_listing_default_crypto_alt",
            "requires_context": False,
            "context_requirements": [],
            "trigger_role": "signal",
        }

    return {
        "symbol": sym,
        "asset_class": "unknown",
        "layer": None,
        "baseline": None,
        "okx_inst": None,
        "confidence": 0.30,
        "reason": "unknown_ticker_no_entity_match",
        "requires_context": True,
        "context_requirements": ["exchange_confirmation", "related_news", "price_data"],
        "trigger_role": "needs_context",
    }
