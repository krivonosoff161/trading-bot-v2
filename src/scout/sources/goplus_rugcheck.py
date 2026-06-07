# -*- coding: utf-8 -*-
"""
goplus_rugcheck.py - conservative L2 risk veto source.

Current implementation uses GoPlus Token Security API as the first live
provider under the `goplus_rugcheck` source slot. It only emits events when a
token has hard or stacked risk flags. No event means "no strong red flag now",
not "safe".
"""
from __future__ import annotations

import requests

UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 scanner-goplus; keyless)"}
TIMEOUT = 15

EVM_CHAIN_IDS = {
    "ethereum": "1",
    "bsc": "56",
    "polygon": "137",
    "optimism": "10",
    "arbitrum": "42161",
    "avalanche": "43114",
    "base": "8453",
    "linea": "59144",
    "mantle": "5000",
    "fantom": "250",
}

EVM_HARD_FLAGS = {
    "is_honeypot": "honeypot",
    "cannot_sell_all": "cannot_sell_all",
    "cannot_buy": "cannot_buy",
    "is_blacklisted": "blacklist",
}
EVM_MEDIUM_FLAGS = {
    "hidden_owner": "hidden_owner",
    "slippage_modifiable": "slippage_modifiable",
    "personal_slippage_modifiable": "personal_slippage_modifiable",
    "owner_change_balance": "owner_can_change_balance",
    "selfdestruct": "selfdestruct",
    "can_take_back_ownership": "take_back_ownership",
    "is_mintable": "mintable",
    "anti_whale_modifiable": "anti_whale_modifiable",
    "trading_cooldown": "trading_cooldown",
    "external_call": "external_call",
}

SOLANA_HARD_FIELDS = {
    "non_transferable": "non_transferable",
}
SOLANA_AUTHORITY_FIELDS = {
    "mintable": "mintable",
    "freezable": "freezable",
    "closable": "closable",
    "transfer_fee_upgradable": "transfer_fee_upgradable",
    "balance_mutable_authority": "balance_mutable_authority",
    "default_account_state_upgradable": "default_state_upgradable",
}


def _is_true(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def _status_true(value) -> bool:
    if isinstance(value, dict):
        return _is_true(value.get("status"))
    return _is_true(value)


def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fetch_evm_security(chain_id: str, contract_address: str) -> dict | None:
    try:
        resp = requests.get(
            f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
            params={"contract_addresses": contract_address},
            headers=UA,
            timeout=TIMEOUT,
        )
        data = resp.json() or {}
    except Exception:
        return None
    result = (data.get("result") or {}).get(str(contract_address).lower())
    return result if isinstance(result, dict) else None


def _fetch_solana_security(contract_address: str) -> dict | None:
    try:
        resp = requests.get(
            "https://api.gopluslabs.io/api/v1/solana/token_security",
            params={"contract_addresses": contract_address},
            headers=UA,
            timeout=TIMEOUT,
        )
        data = resp.json() or {}
    except Exception:
        return None
    result = (data.get("result") or {}).get(contract_address)
    return result if isinstance(result, dict) else None


def _evm_risk_reasons(info: dict) -> list[str]:
    reasons: list[str] = []
    for field, label in EVM_HARD_FLAGS.items():
        if _is_true(info.get(field)):
            reasons.append(label)

    medium = [label for field, label in EVM_MEDIUM_FLAGS.items() if _is_true(info.get(field))]

    buy_tax = _safe_float(info.get("buy_tax")) or 0.0
    sell_tax = _safe_float(info.get("sell_tax")) or 0.0
    transfer_tax = _safe_float(info.get("transfer_tax")) or 0.0
    if max(buy_tax, sell_tax, transfer_tax) >= 15.0:
        medium.append(f"high_tax({max(buy_tax, sell_tax, transfer_tax):.0f}%)")

    if reasons:
        return reasons + medium
    if len(medium) >= 2:
        return medium
    return []


def _solana_risk_reasons(info: dict) -> list[str]:
    reasons = [label for field, label in SOLANA_HARD_FIELDS.items() if _status_true(info.get(field))]
    authority_flags = [
        label for field, label in SOLANA_AUTHORITY_FIELDS.items()
        if _status_true(info.get(field))
    ]
    if reasons:
        return reasons + authority_flags
    if len(authority_flags) >= 2:
        return authority_flags
    return []


def _build_event(candidate: dict, reasons: list[str], provider_note: str) -> dict:
    asset = candidate["asset"]
    chain = candidate.get("chain") or "unknown-chain"
    contract = candidate.get("contract_address") or "unknown-address"
    pair = candidate.get("pair_address") or ""
    title = f"{asset} token risk flag: {', '.join(reasons[:3])}"
    text = (
        f"{provider_note} flagged {asset} on {chain}. "
        f"Reasons: {', '.join(reasons)}. "
        f"Observed from DEX pair {pair or 'n/a'} / contract {contract}."
    )
    return {
        "title": title,
        "text": text,
        "url": candidate.get("url"),
        "time": candidate.get("time"),
        "source": "goplus_rugcheck",
        "source_class": "api",
        "lead_class": "LEADING",
        "asset": asset,
        "contract_address": contract,
        "okx_inst": candidate.get("okx_inst"),
        "layer": 2,
        "baseline": candidate.get("baseline"),
        "phase": "REALIZED",
        "event_type": "rug_flag",
        "trigger_type": "token_security_veto",
        "event_key": f"risk:{asset}:{contract}",
        "chain": chain,
        "risk_reasons": reasons,
    }


def fetch_token_risk_signals(candidates: list[dict], limit: int = 6) -> list[dict]:
    """Check current L2 candidates for hard token-security red flags."""
    out: list[dict] = []
    seen_contracts: set[tuple[str, str]] = set()

    for candidate in candidates:
        chain = str(candidate.get("chain") or "").lower()
        contract = str(candidate.get("contract_address") or "").strip()
        if not chain or not contract:
            continue
        key = (chain, contract.lower())
        if key in seen_contracts:
            continue
        seen_contracts.add(key)

        reasons: list[str] = []
        if chain == "solana":
            info = _fetch_solana_security(contract)
            if info:
                reasons = _solana_risk_reasons(info)
            provider = "GoPlus Solana token security"
        else:
            chain_id = EVM_CHAIN_IDS.get(chain)
            if not chain_id:
                continue
            info = _fetch_evm_security(chain_id, contract)
            if info:
                reasons = _evm_risk_reasons(info)
            provider = "GoPlus token security"

        if not reasons:
            continue
        out.append(_build_event(candidate, reasons, provider))
        if len(out) >= limit:
            break

    return out
