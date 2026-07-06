from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
STATE_DIR = ROOT / "logs" / "scout" / "public_channel"
SENT_KEYS = STATE_DIR / "sent_keys.json"
AUDIT_LOG = STATE_DIR / "publisher_audit.jsonl"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def sent_keys(path: Path = SENT_KEYS) -> set[str]:
    data = _read_json(path)
    return {str(x) for x in data.get("sent_keys", []) if str(x)}


def was_sent(key: str, path: Path = SENT_KEYS) -> bool:
    return key in sent_keys(path)


def mark_sent(key: str, path: Path = SENT_KEYS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = sent_keys(path)
    keys.add(str(key))
    path.write_text(json.dumps({"sent_keys": sorted(keys)}, ensure_ascii=False, indent=2), encoding="utf-8")


def append_audit(row: dict[str, Any], path: Path = AUDIT_LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

