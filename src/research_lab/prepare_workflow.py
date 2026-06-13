# -*- coding: utf-8 -*-
"""Operator-workflow config for the optional 1m prepare step (env-driven, safe).

This only *reads* env flags and reports what the start workflow WOULD do. It never
fetches and never writes. The actual prepare work is done by
`scripts/strategy_lab/prepare_1m_data.py`; `bat/strategy_lab_start.bat` runs that
step only when these flags opt in. Default is fully safe: disabled, null provider,
no network.

Flags:
- STRATEGY_LAB_PREPARE_1M=0/1            include a prepare step in start (default 0)
- STRATEGY_LAB_PREPARE_1M_APPLY=0/1      apply (fetch+write) vs dry-run (default 0)
- STRATEGY_LAB_MARKET_DATA_PROVIDER      null | okx-public | synthetic (default null)
- STRATEGY_LAB_PREPARE_1M_MAX_SYMBOLS    optional, clamped to policy by the CLI
- STRATEGY_LAB_PREPARE_1M_MAX_WINDOWS    optional, clamped to policy by the CLI
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

ENV_ENABLED = "STRATEGY_LAB_PREPARE_1M"
ENV_APPLY = "STRATEGY_LAB_PREPARE_1M_APPLY"
ENV_PROVIDER = "STRATEGY_LAB_MARKET_DATA_PROVIDER"
ENV_MAX_SYMBOLS = "STRATEGY_LAB_PREPARE_1M_MAX_SYMBOLS"
ENV_MAX_WINDOWS = "STRATEGY_LAB_PREPARE_1M_MAX_WINDOWS"

VALID_PROVIDERS = ("null", "okx-public", "synthetic")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}
_NETWORK_PROVIDERS = {"okx-public"}  # synthetic is offline; null never fetches


@dataclass(frozen=True)
class PrepareWorkflowConfig:
    enabled: bool
    apply: bool
    provider: str
    max_symbols: int | None
    max_windows: int | None
    warnings: tuple[str, ...] = ()

    @property
    def mode(self) -> str:
        if not self.enabled:
            return "disabled"
        return "apply" if self.apply else "dry_run"

    @property
    def will_fetch_network(self) -> bool:
        """True only when start would make a real network fetch."""
        return self.enabled and self.apply and self.provider in _NETWORK_PROVIDERS

    def to_summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "provider": self.provider,
            "apply": self.apply,
            "max_symbols": self.max_symbols,
            "max_windows": self.max_windows,
            "will_fetch_network": self.will_fetch_network,
            "note": "auto network fetch disabled by default; apply needs explicit env + a real provider",
            "warnings": list(self.warnings),
        }


def _parse_bool(raw: str, default: bool, *, key: str, warnings: list[str]) -> bool:
    token = str(raw).strip().lower()
    if token in _TRUE:
        return True
    if token in _FALSE:
        return False
    warnings.append(f"{key}={raw!r} not understood -> using {default}")
    return default


def _parse_opt_int(raw: str, *, key: str, warnings: list[str]) -> int | None:
    token = str(raw).strip()
    if not token:
        return None
    try:
        value = int(token)
    except ValueError:
        warnings.append(f"{key}={raw!r} is not an integer -> ignored")
        return None
    if value < 0:
        warnings.append(f"{key}={raw!r} is negative -> ignored")
        return None
    return value


def load_prepare_workflow_config(environ: Mapping[str, str] | None = None) -> PrepareWorkflowConfig:
    """Parse the prepare-workflow env flags with safe fallbacks and warnings."""
    env = environ if environ is not None else os.environ
    warnings: list[str] = []
    enabled = _parse_bool(env.get(ENV_ENABLED, ""), False, key=ENV_ENABLED, warnings=warnings)
    apply = _parse_bool(env.get(ENV_APPLY, ""), False, key=ENV_APPLY, warnings=warnings)
    provider = str(env.get(ENV_PROVIDER, "") or "null").strip().lower()
    if provider not in VALID_PROVIDERS:
        warnings.append(f"{ENV_PROVIDER}={provider!r} not a known provider -> null")
        provider = "null"
    max_symbols = _parse_opt_int(env.get(ENV_MAX_SYMBOLS, ""), key=ENV_MAX_SYMBOLS, warnings=warnings)
    max_windows = _parse_opt_int(env.get(ENV_MAX_WINDOWS, ""), key=ENV_MAX_WINDOWS, warnings=warnings)
    return PrepareWorkflowConfig(
        enabled=enabled, apply=apply, provider=provider,
        max_symbols=max_symbols, max_windows=max_windows, warnings=tuple(warnings),
    )
