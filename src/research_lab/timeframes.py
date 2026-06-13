# -*- coding: utf-8 -*-
"""Timeframe profiles loader: research role and per-cycle limits per timeframe.

Profiles bound how much work a timeframe may schedule (symbols and variants) and
mark whether a timeframe is trigger-only (the 1m event microscope). Lookups are
case-insensitive so existing labels like "1D"/"1H" map onto the lowercase keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.research_lab.config_io import CONFIG_DIR, load_yaml_mapping, require

DEFAULT_PATH = CONFIG_DIR / "timeframe_profiles.yaml"
_REQUIRED = ("role", "default_use", "max_symbols_per_cycle", "max_variants_per_setup")


@dataclass(frozen=True)
class TimeframeProfile:
    timeframe: str
    role: str
    default_use: str
    max_symbols_per_cycle: int
    max_variants_per_setup: int
    trigger_only: bool = False
    max_window_hours: int | None = None
    max_event_windows: int | None = None
    comment: str = ""


@dataclass(frozen=True)
class TimeframeProfiles:
    profiles: dict[str, TimeframeProfile]

    def names(self) -> tuple[str, ...]:
        return tuple(self.profiles)

    def get(self, timeframe: str) -> TimeframeProfile:
        key = str(timeframe).lower()
        try:
            return self.profiles[key]
        except KeyError:
            known = ", ".join(self.profiles) or "<none>"
            raise ValueError(f"unknown timeframe '{timeframe}' (known: {known})") from None


def load_timeframe_profiles(path: str | Path = DEFAULT_PATH) -> TimeframeProfiles:
    data = load_yaml_mapping(path, what="timeframe_profiles")
    raw = require(data, "profiles", what="timeframe_profiles")
    if not isinstance(raw, dict) or not raw:
        raise ValueError("timeframe_profiles 'profiles' must be a non-empty mapping")
    profiles: dict[str, TimeframeProfile] = {}
    for name, body in raw.items():
        profiles[str(name).lower()] = _parse_profile(str(name).lower(), body)
    return TimeframeProfiles(profiles=profiles)


def _parse_profile(name: str, body: Any) -> TimeframeProfile:
    if not isinstance(body, dict):
        raise ValueError(f"timeframe profile '{name}' must be a mapping")
    for key in _REQUIRED:
        if key not in body:
            raise ValueError(f"timeframe profile '{name}' missing required key: {key}")
    window = body.get("max_window_hours")
    event_windows = body.get("max_event_windows")
    return TimeframeProfile(
        timeframe=name,
        role=str(body["role"]),
        default_use=str(body["default_use"]),
        max_symbols_per_cycle=int(body["max_symbols_per_cycle"]),
        max_variants_per_setup=int(body["max_variants_per_setup"]),
        trigger_only=bool(body.get("trigger_only", False)),
        max_window_hours=int(window) if window is not None else None,
        max_event_windows=int(event_windows) if event_windows is not None else None,
        comment=str(body.get("comment", "")),
    )
