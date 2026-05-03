"""
project_snapshot.py - bystryy srez sostoyaniya proyekta dlya nachala sessii.
Zapusk: python scripts/project_snapshot.py
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import json
import subprocess
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent

# ── 1. Git ──────────────────────────────────────────────────────────────────
def git_status():
    try:
        head = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            capture_output=True, text=True, cwd=ROOT
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=ROOT
        ).stdout.strip()
        return head, bool(dirty), dirty
    except Exception:
        return "?", False, ""

# ── 2. Bot process ───────────────────────────────────────────────────────────
def bot_status():
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV"],
            capture_output=True, text=True
        )
        lines = [l for l in result.stdout.splitlines() if "python" in l.lower()]
        return len(lines)
    except Exception:
        return 0

# ── 3. Last scanner log entry ────────────────────────────────────────────────
def last_log_line():
    log = ROOT / "logs" / "scanner.log"
    if not log.exists():
        return "нет файла"
    try:
        with open(log, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4096))
            tail = f.read().decode("utf-8", errors="ignore")
        lines = [l for l in tail.splitlines() if l.strip()]
        return lines[-1][:120] if lines else "пусто"
    except Exception:
        return "ошибка чтения"

# ── 4. Signal stats ──────────────────────────────────────────────────────────
def signal_stats(days=7):
    sig_file = ROOT / "logs" / "signals" / "signal_log.jsonl"
    lbl_file = ROOT / "logs" / "signals" / "signal_labels.jsonl"

    signals = {}
    try:
        with open(sig_file) as f:
            for line in f:
                try:
                    s = json.loads(line)
                    signals[s["signal_id"]] = s
                except Exception:
                    pass
    except FileNotFoundError:
        pass

    labels = {}
    try:
        with open(lbl_file) as f:
            for line in f:
                try:
                    l = json.loads(line)
                    labels[l["signal_id"]] = l
                except Exception:
                    pass
    except FileNotFoundError:
        pass

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    cutoff_ms = int(cutoff.timestamp() * 1000)

    recent_sigs = {sid: s for sid, s in signals.items() if s.get("ts_ms", 0) >= cutoff_ms}
    pending = [sid for sid in recent_sigs if sid not in labels]

    tp = sl = te = 0
    by_pair = defaultdict(lambda: {"tp": 0, "sl": 0, "te": 0})
    total_r = []

    for sid, lbl in labels.items():
        s = signals.get(sid)
        if not s or s.get("ts_ms", 0) < cutoff_ms:
            continue
        out = lbl.get("outcome", "")
        sym = s.get("symbol", "?")
        r = lbl.get("exit_r", 0) or 0
        if out.startswith("TP"):
            tp += 1
            by_pair[sym]["tp"] += 1
            total_r.append(r)
        elif out == "STOP":
            sl += 1
            by_pair[sym]["sl"] += 1
            total_r.append(r)
        elif out == "TIME_EXIT":
            te += 1
            by_pair[sym]["te"] += 1
            total_r.append(r)

    total = tp + sl + te
    wr = tp / total * 100 if total else 0
    avg_r = sum(total_r) / len(total_r) if total_r else 0

    # Last signal
    last = None
    if signals:
        last_sid = max(signals, key=lambda x: signals[x].get("ts_ms", 0))
        last = signals[last_sid]

    return {
        "total": total, "tp": tp, "sl": sl, "te": te,
        "wr": wr, "avg_r": avg_r,
        "pending": len(pending),
        "by_pair": dict(by_pair),
        "last": last,
    }

# ── 5. Main ──────────────────────────────────────────────────────────────────
def main():
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*58}")
    print(f"  PROJECT SNAPSHOT — {now}")
    print(f"{'='*58}")

    # Git
    head, dirty, dirty_files = git_status()
    dirty_mark = " [DIRTY]" if dirty else ""
    print(f"\n GIT:  {head}{dirty_mark}")
    if dirty:
        for line in dirty_files.splitlines()[:5]:
            print(f"       {line}")

    # Bot
    procs = bot_status()
    status = f"ЗАПУЩЕН ({procs} процессов)" if procs >= 2 else "⚠ не найден"
    print(f" БОТ:  {status}")

    # Last log
    last_log = last_log_line()
    print(f" ЛОГ:  {last_log}")

    # Signal stats 7d
    st = signal_stats(days=7)
    total = st["total"]
    wr = st["wr"]
    tp, sl, te = st["tp"], st["sl"], st["te"]
    avg_r = st["avg_r"]
    pending = st["pending"]

    print(f"\n{'-'*58}")
    print(f" СИГНАЛЫ (7 дней): {total} закрытых  |  pending: {pending}")
    if total > 0:
        print(f" WR: {wr:.0f}%  |  TP={tp}  SL={sl}  TIME={te}  |  avg_R={avg_r:+.2f}R")
        print()
        print(f" По парам:")
        for sym, s in sorted(st["by_pair"].items()):
            n = s["tp"] + s["sl"] + s["te"]
            w = s["tp"] / n * 100 if n else 0
            bar = "▓" * s["tp"] + "░" * s["sl"] + "·" * s["te"]
            print(f"   {sym:14} n={n:3}  WR={w:3.0f}%  {bar}")

    # Last signal
    last = st["last"]
    if last:
        ts = datetime.utcfromtimestamp(last["ts_ms"] / 1000).strftime("%m-%d %H:%M")
        print(f"\n ПОСЛЕДНИЙ СИГНАЛ: {ts} UTC | {last.get('symbol')} | "
              f"{last.get('side')} | {last.get('regime')} | {last.get('source')}")

    print(f"{'='*58}\n")

if __name__ == "__main__":
    main()
