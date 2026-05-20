"""
flag_invalid_signals.py — mark main-screener labels with a data-quality flag.

Adds `valid` / `invalid_reason` to each record in main_signals_labels.jsonl by
joining on signal_id with main_signals.jsonl.

Current rule — `precision_bug_prefix`: SL/TP levels collapsed or on the wrong side
of entry. Caused by the pre-15.05 rounding bug (round(x, 4)) on sub-0.001 coins,
fixed in commit 57ec2df. Such labels are unusable for training and must be excluded.

Idempotent: re-running re-evaluates flags from scratch. A timestamped backup of the
labels file is written before the first overwrite.
"""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SIGNALS = ROOT / "logs" / "signals" / "main_signals.jsonl"
LABELS = ROOT / "logs" / "signals" / "main_signals_labels.jsonl"


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def price_broken(sig: dict) -> bool:
    """True when entry/SL/TP levels are degenerate (collapsed or wrong side)."""
    try:
        entry = float(sig["entry"])
        sl = float(sig["sl"])
        tp1 = float(sig["tp1"])
        tp2 = float(sig.get("tp2") or 0)
    except (KeyError, TypeError, ValueError):
        return True
    side = sig.get("side")
    if tp1 == sl or entry in (sl, tp1) or (tp2 and tp1 == tp2):
        return True
    if side == "buy" and not sl < entry < tp1:
        return True
    if side == "sell" and not tp1 < entry < sl:
        return True
    return False


def run() -> None:
    signals = {s.get("id") or s.get("signal_id"): s for s in _load(SIGNALS)}
    labels = _load(LABELS)
    if not labels:
        print("main_signals_labels.jsonl пустой — нечего метить.")
        return

    broken = {sid for sid, sig in signals.items() if price_broken(sig)}

    flagged = 0
    for lab in labels:
        sid = lab.get("signal_id")
        is_bad = sid in broken
        lab["valid"] = not is_bad
        if is_bad:
            lab["invalid_reason"] = "precision_bug_prefix"
            flagged += 1
        else:
            lab.pop("invalid_reason", None)

    backup = LABELS.with_suffix(f".jsonl.bak_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
    shutil.copy2(LABELS, backup)

    with open(LABELS, "w", encoding="utf-8") as f:
        for lab in labels:
            f.write(json.dumps(lab, ensure_ascii=False) + "\n")

    print(f"Помечено invalid: {flagged} из {len(labels)} меток (reason=precision_bug_prefix).")
    print(f"Бэкап: {backup.name}")


if __name__ == "__main__":
    run()
