"""
Fast operator snapshot for the trading-bot-v2 workspace.

Run:
    python scripts/project_snapshot.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent


def git_status() -> tuple[str, bool, str]:
    try:
        head = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=False,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=False,
        ).stdout.strip()
        return head, bool(dirty), dirty
    except Exception:
        return "?", False, ""


def _json_records(raw: str) -> list[dict[str, Any]]:
    raw = raw.strip()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def _windows_python_processes() -> list[dict[str, Any]]:
    script = (
        "Get-CimInstance Win32_Process "
        "-Filter \"Name = 'python.exe' OR Name = 'pythonw.exe'\" | "
        "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    try:
        return _json_records(result.stdout)
    except Exception:
        return []


def _posix_python_processes() -> list[dict[str, Any]]:
    result = subprocess.run(
        ["ps", "-eo", "pid=,comm=,args="],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if "python" not in line.lower():
            continue
        parts = line.strip().split(maxsplit=2)
        if len(parts) < 3:
            continue
        rows.append({"ProcessId": parts[0], "Name": parts[1], "CommandLine": parts[2]})
    return rows


def python_processes() -> list[dict[str, Any]]:
    if sys.platform.startswith("win"):
        return _windows_python_processes()
    return _posix_python_processes()


def classify_process(command_line: str) -> str | None:
    cmd = (command_line or "").lower().replace("/", "\\")
    if "scripts.strategy_lab.farm_loop" in cmd and "--run-paper-signals" in cmd:
        return "canonical_farm_paper_loop"
    if "scripts.strategy_lab.farm_loop" in cmd:
        return "farm_loop_partial"
    if "scripts.strategy_lab.paper_signals_run" in cmd:
        return "paper_signals_runner"
    if "\\main.py" in cmd or cmd.endswith(" main.py") or " main.py " in cmd:
        return "main_engine"
    if "scanner_runtime" in cmd or "news_scanner" in cmd or "scanner_status" in cmd:
        return "scanner"
    if "telegram" in cmd and ("bot" in cmd or "send" in cmd or "scanner" in cmd):
        return "telegram_surface"
    return None


def bot_status(processes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = python_processes() if processes is None else processes
    relevant: list[dict[str, Any]] = []
    ignored = 0
    by_kind: dict[str, int] = defaultdict(int)
    for row in rows:
        cmd = str(row.get("CommandLine") or "")
        kind = classify_process(cmd)
        if kind is None:
            ignored += 1
            continue
        by_kind[kind] += 1
        relevant.append(
            {
                "pid": row.get("ProcessId"),
                "kind": kind,
                "command": cmd[:180],
            }
        )
    return {
        "relevant": relevant,
        "ignored_python": ignored,
        "by_kind": dict(sorted(by_kind.items())),
    }


def last_log_line() -> str:
    logs_dir = ROOT / "logs"
    try:
        log_files = list(logs_dir.rglob("*.log"))
        if not log_files:
            return "no log files"
        newest = max(log_files, key=lambda p: p.stat().st_mtime)
        with newest.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4096))
            tail = f.read().decode("utf-8", errors="ignore")
        lines = [line for line in tail.splitlines() if line.strip()]
        last = lines[-1][:120] if lines else "empty"
        return f"{newest.name}: {last}"
    except Exception:
        return "log read error"


def _ts_ms(ts_str: str) -> int:
    try:
        return int(datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return 0


def signal_stats(days: int = 7) -> dict[str, Any]:
    sig_file = ROOT / "logs" / "signals" / "main_signals.jsonl"
    lbl_file = ROOT / "logs" / "signals" / "main_signals_labels.jsonl"

    signals: dict[str, dict[str, Any]] = {}
    try:
        with sig_file.open(encoding="utf-8") as f:
            for line in f:
                try:
                    signal = json.loads(line)
                    sid = signal.get("id") or signal.get("signal_id")
                    if sid:
                        signal["_ts_ms"] = _ts_ms(signal.get("ts", ""))
                        signals[sid] = signal
                except Exception:
                    pass
    except FileNotFoundError:
        pass

    labels: dict[str, dict[str, Any]] = {}
    try:
        with lbl_file.open(encoding="utf-8") as f:
            for line in f:
                try:
                    label = json.loads(line)
                    labels[label["signal_id"]] = label
                except Exception:
                    pass
    except FileNotFoundError:
        pass

    cutoff_ms = int((datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    recent = {sid: s for sid, s in signals.items() if s["_ts_ms"] >= cutoff_ms}
    pending = [sid for sid in recent if sid not in labels]

    tp = sl = te = invalid = 0
    by_pair: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "sl": 0, "te": 0})

    for sid, signal in recent.items():
        label = labels.get(sid)
        if not label:
            continue
        if label.get("valid") is False:
            invalid += 1
            continue
        outcome = label.get("outcome", "")
        symbol = signal.get("symbol", "?")
        if outcome.startswith("TP"):
            tp += 1
            by_pair[symbol]["tp"] += 1
        elif outcome == "SL":
            sl += 1
            by_pair[symbol]["sl"] += 1
        elif outcome == "TIME":
            te += 1
            by_pair[symbol]["te"] += 1

    decisive = tp + sl
    wr = tp / decisive * 100 if decisive else 0

    last = None
    if signals:
        last_sid = max(signals, key=lambda x: signals[x]["_ts_ms"])
        last = signals[last_sid]

    return {
        "total": tp + sl + te,
        "tp": tp,
        "sl": sl,
        "te": te,
        "wr": wr,
        "pending": len(pending),
        "invalid": invalid,
        "by_pair": dict(by_pair),
        "last": last,
    }


def _print_process_status() -> None:
    report = bot_status()
    relevant = report["relevant"]
    if relevant:
        kinds = ", ".join(f"{k}={v}" for k, v in report["by_kind"].items())
        print(f" BOT:  RUNNING relevant={len(relevant)} ({kinds})")
        for row in relevant[:5]:
            print(f"       pid={row['pid']} kind={row['kind']} cmd={row['command']}")
    else:
        ignored = report["ignored_python"]
        suffix = f" (ignored unrelated python={ignored})" if ignored else ""
        print(f" BOT:  no relevant trading process found{suffix}")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'=' * 58}")
    print(f"  PROJECT SNAPSHOT - {now}")
    print(f"{'=' * 58}")

    head, dirty, dirty_files = git_status()
    dirty_mark = " [DIRTY]" if dirty else ""
    print(f"\n GIT:  {head}{dirty_mark}")
    if dirty:
        for line in dirty_files.splitlines()[:5]:
            print(f"       {line}")

    _print_process_status()
    print(f" LOG:  {last_log_line()}")

    st = signal_stats(days=7)
    inv_mark = f"  |  invalid: {st['invalid']}" if st["invalid"] else ""
    print(f"\n{'-' * 58}")
    print(f" MAIN SIGNALS (7d): {st['total']} closed  |  pending: {st['pending']}{inv_mark}")
    if st["tp"] + st["sl"] > 0:
        print(f" WR decisive: {st['wr']:.0f}%  |  TP={st['tp']}  SL={st['sl']}  TIME={st['te']}")
        print("\n By pair:")
        for sym, row in sorted(st["by_pair"].items()):
            n = row["tp"] + row["sl"] + row["te"]
            w = row["tp"] / (row["tp"] + row["sl"]) * 100 if (row["tp"] + row["sl"]) else 0
            bar = "+" * row["tp"] + "-" * row["sl"] + "." * row["te"]
            print(f"   {sym.replace('-USDT-SWAP', ''):10} n={n:3}  WR={w:3.0f}%  {bar}")

    last = st["last"]
    if last:
        ts = datetime.utcfromtimestamp(last["_ts_ms"] / 1000).strftime("%m-%d %H:%M")
        print(
            f"\n LAST SIGNAL: {ts} UTC | {last.get('symbol')} | "
            f"{last.get('side')} | {last.get('regime')} | {last.get('trade_style')}"
        )

    print(f"{'=' * 58}\n")


if __name__ == "__main__":
    main()
