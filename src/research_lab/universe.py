# -*- coding: utf-8 -*-
"""Universe loader: asset groups and relation hints (research metadata).

The universe describes which symbols the lab groups together and which symbols a
given symbol tends to track. It carries no signal and no profitability claim; it
only lets the event-cluster layer attach "related symbols" to a detected move.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.research_lab.config_io import CONFIG_DIR, load_yaml_mapping, require

DEFAULT_PATH = CONFIG_DIR / "universe.yaml"


def _norm(symbol: str) -> str:
    return symbol.replace("-", "_").replace("/", "_").upper()


@dataclass(frozen=True)
class Universe:
    groups: dict[str, tuple[str, ...]]
    relations: dict[str, dict[str, tuple[str, ...]]]

    def all_symbols(self) -> tuple[str, ...]:
        seen: list[str] = []
        for members in self.groups.values():
            for sym in members:
                if sym not in seen:
                    seen.append(sym)
        return tuple(seen)

    def group_of(self, symbol: str) -> str | None:
        target = _norm(symbol)
        for name, members in self.groups.items():
            if target in members:
                return name
        return None

    def symbols_in(self, group: str) -> tuple[str, ...]:
        return self.groups.get(group, ())

    def peers(self, symbol: str) -> tuple[str, ...]:
        target = _norm(symbol)
        group = self.group_of(target)
        if group is None:
            return ()
        return tuple(s for s in self.groups[group] if s != target)

    def related(
        self,
        symbol: str,
        *,
        relation: str | None = None,
        include_peers: bool = True,
    ) -> tuple[str, ...]:
        """Related symbols: relation hints (+ same-group peers), deduped, no self."""
        target = _norm(symbol)
        out: list[str] = []
        relation_types = [relation] if relation else list(self.relations)
        for rel in relation_types:
            for sym in self.relations.get(rel, {}).get(target, ()):  # type: ignore[arg-type]
                if sym != target and sym not in out:
                    out.append(sym)
        if include_peers:
            for sym in self.peers(target):
                if sym not in out:
                    out.append(sym)
        return tuple(out)


def load_universe(path: str | Path = DEFAULT_PATH) -> Universe:
    data = load_yaml_mapping(path, what="universe")
    raw_groups = require(data, "groups", what="universe")
    if not isinstance(raw_groups, dict) or not raw_groups:
        raise ValueError("universe config 'groups' must be a non-empty mapping")
    groups: dict[str, tuple[str, ...]] = {}
    for name, members in raw_groups.items():
        if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
            raise ValueError(f"universe group '{name}' must be a list of symbol strings")
        groups[str(name)] = tuple(_norm(m) for m in members)
    relations = _parse_relations(data.get("relations") or {})
    return Universe(groups=groups, relations=relations)


def _parse_relations(raw: Any) -> dict[str, dict[str, tuple[str, ...]]]:
    if not isinstance(raw, dict):
        raise ValueError("universe config 'relations' must be a mapping when present")
    relations: dict[str, dict[str, tuple[str, ...]]] = {}
    for rel_type, mapping in raw.items():
        if not isinstance(mapping, dict):
            raise ValueError(f"universe relation '{rel_type}' must be a mapping")
        parsed: dict[str, tuple[str, ...]] = {}
        for symbol, targets in mapping.items():
            if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
                raise ValueError(f"universe relation '{rel_type}' for '{symbol}' must be a list")
            parsed[_norm(str(symbol))] = tuple(_norm(t) for t in targets)
        relations[str(rel_type)] = parsed
    return relations
