# -*- coding: utf-8 -*-
"""Layer 6 — forward: the only honest test.

Log a decision at decision time; add the outcome later. If it works on data you had
not seen when you decided, that is evidence. Everything above this line is just
'not obviously broken'.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ForwardLog:
    """Append-only forward / paper-trading log (JSONL: inspectable, replayable)."""

    def __init__(self, path: str = "forward_log.jsonl"):
        self.path = Path(path)

    def record(self, decision: dict) -> dict:
        row = {**decision, "logged_at": _now()}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def rows(self) -> list:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
