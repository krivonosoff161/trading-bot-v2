"""
Fast operator snapshot for the trading-bot-v2 workspace.

Run:
    python scripts/project_snapshot.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
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


def _private_root() -> Path:
    return Path(
        os.getenv(
            "TRADING_BOT_RESEARCH_ROOT",
            str(Path.home() / "github_projects" / "trading-bot-research" / "strategy-lab"),
        )
    )


def _farm_loop_status_snapshot(private_root: Path | None = None, *, now: float | None = None) -> dict[str, Any]:
    root = private_root or _private_root()
    status_path = root / "state" / "farm_loop_status.json"
    current = time.time() if now is None else now
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    updated_at = float(data.get("updated_at") or 0.0)
    cycle_started_at = float(data.get("cycle_started_at") or 0.0)
    return {
        "pid": int(data.get("pid") or 0),
        "stage": str(data.get("stage") or ""),
        "updated_age_seconds": max(0, int(current - updated_at)) if updated_at else 0,
        "cycle_age_seconds": max(0, int(current - cycle_started_at)) if cycle_started_at else 0,
        "loop": bool(data.get("loop")),
        "paper_only": bool(data.get("paper_only")),
        "execution_allowed": bool(data.get("execution_allowed")),
        "details": data.get("details") if isinstance(data.get("details"), dict) else {},
    }


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"if (Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue) {{ '1' }}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() == "1"
    try:
        os.kill(pid, 0)
    except (OSError, SystemError, ValueError):
        return False
    return True


def _farm_loop_status_fallback() -> list[dict[str, Any]]:
    """Recover the canonical farm loop from its heartbeat when WMI returns no rows."""
    data = _farm_loop_status_snapshot()
    if not data:
        return []

    pid = int(data.get("pid") or 0)
    if not pid or int(data.get("updated_age_seconds") or 0) > 900 or not _pid_exists(pid):
        return []

    return [{
        "ProcessId": pid,
        "Name": "python.exe",
        "CommandLine": (
            "python -m scripts.strategy_lab.farm_loop "
            "--loop --apply --run-paper-signals # recovered from farm_loop_status.json"
        ),
    }]


def classify_process(command_line: str) -> str | None:
    cmd = (command_line or "").lower().replace("/", "\\")
    if "pytest" in cmd or "scripts\\project_snapshot.py" in cmd or "scripts.project_snapshot" in cmd:
        return None
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
    if processes is None and not rows:
        rows = _farm_loop_status_fallback()
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
        "farm_status": _farm_loop_status_snapshot() if processes is None else {},
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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return rows
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _count_field(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "")
        if value:
            counts[value] = counts.get(value, 0) + 1
    return counts


def _top_counts(raw: Any, *, limit: int = 4) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    pairs = sorted(
        ((str(key), int(value or 0)) for key, value in raw.items()),
        key=lambda item: (-item[1], item[0]),
    )
    return dict(pairs[:limit])


def _sent_key_summary(private_root: Path) -> dict[str, int]:
    data = _read_json(private_root / "state" / "derived" / "paper_telegram_sent_keys.json")
    keys = [str(item) for item in data.get("sent_keys", []) if str(item)]
    preview_ids = {key.rsplit(":", 1)[0] for key in keys if ":" in key}
    recipient_hashes = {key.rsplit(":", 1)[1] for key in keys if ":" in key}
    return {
        "sent_key_count": len(keys),
        "sent_preview_count": len(preview_ids),
        "sent_recipient_count": len(recipient_hashes),
    }


def paper_product_status(private_root: Path | None = None) -> dict[str, Any]:
    """Small operator view over the current paper-product chain.

    This intentionally reads only aggregate snapshot fields from the private root.
    Signal text, raw model output, secrets, and private fills stay out of the public
    snapshot output.
    """
    root = private_root or _private_root()
    derived = root / "state" / "derived"

    paper = _read_json(derived / "paper_signals.json")
    bridge = _read_json(derived / "main_paper_instructions.json")
    consumer = _read_json(derived / "main_paper_consumed.json")
    queue = _read_json(derived / "main_paper_runtime_queue.json")
    observation = _read_json(derived / "main_paper_runtime_observation.json")
    trades = _read_json(derived / "main_paper_trades.json")
    product_trades = _read_json(derived / "paper_product_trades.json")
    preview = _read_json(derived / "paper_telegram_preview.json")
    delivery = _read_json(derived / "paper_telegram_delivery.json")
    training = _read_json(derived / "paper_signal_training.json")
    training_rows = _read_jsonl(derived / "paper_signal_training.jsonl")
    outcome_reviews = _read_jsonl(root / "state" / "llm_advice" / "outcome_reviews.jsonl")
    quality = _read_json(derived / "paper_product_quality_report.json")
    sent_keys = _sent_key_summary(root)
    accepted_reviews = [row for row in outcome_reviews if bool(row.get("accepted"))]
    linked_training = [row for row in training_rows if str(row.get("outcome_review_id") or "")]

    active = (
        int(paper.get("total") or 0) > 0
        or int(bridge.get("instructions") or 0) > 0
        or int(trades.get("trades") or 0) > 0
        or int(product_trades.get("trades") or 0) > 0
    )
    execution_allowed = any(
        bool(row.get("execution_allowed"))
        for row in (bridge, consumer, queue, observation, trades, product_trades, preview, delivery)
        if row
    )
    return {
        "active": active,
        "private_root": str(root),
        "paper_total": int(paper.get("total") or 0),
        "paper_by_status": paper.get("by_status") or {},
        "instructions": int(bridge.get("instructions") or 0),
        "skipped_unvalidated": int(bridge.get("skipped_unvalidated") or 0),
        "accepted": int(consumer.get("accepted") or 0),
        "rejected": int(consumer.get("rejected") or 0),
        "queued": int(queue.get("queued") or 0),
        "observed": int(observation.get("observed") or 0),
        "reviewed": int(observation.get("reviewed") or 0),
        "pending": int(observation.get("pending") or 0),
        "provider_error": int(observation.get("provider_error") or 0),
        "trades": int(trades.get("trades") or 0),
        "trade_status": trades.get("by_status") or {},
        "product_trades": int(product_trades.get("trades") or 0),
        "product_live_ready": int(product_trades.get("live_ready") or 0),
        "product_live_blocked": int(product_trades.get("live_blocked") or 0),
        "product_active_trades": int(product_trades.get("active_trades") or 0),
        "product_active_live_ready": int(product_trades.get("active_live_ready") or 0),
        "product_active_live_blocked": int(product_trades.get("active_live_blocked") or 0),
        "product_active_by_source": _top_counts(product_trades.get("active_by_source") or {}),
        "product_active_by_family": _top_counts(product_trades.get("active_by_family") or {}),
        "product_trade_status": product_trades.get("by_status") or {},
        "product_live_block": _top_counts(product_trades.get("by_live_block") or {}),
        "preview_rendered": int(preview.get("rendered") or 0),
        "preview_skipped_quality_gate": int(preview.get("skipped_quality_gate") or 0),
        "preview_quality_gate_reasons": _top_counts(preview.get("quality_gate_reasons") or {}),
        "delivery_eligible": int(delivery.get("eligible_cards", delivery.get("eligible") or 0) or 0),
        "delivery_sent": int(delivery.get("sent_messages", delivery.get("sent") or 0) or 0),
        "delivery_sent_cards": int(delivery.get("sent_cards") or 0),
        "delivery_duplicates": int(delivery.get("duplicate_messages", delivery.get("duplicates") or 0) or 0),
        "delivery_duplicate_cards": int(delivery.get("duplicate_cards") or 0),
        "delivery_errors": int(delivery.get("error_messages", delivery.get("errors") or 0) or 0),
        "delivery_error_cards": int(delivery.get("error_cards") or 0),
        "delivery_targets": int(delivery.get("target_recipients", delivery.get("targets") or 0) or 0),
        "delivery_dry_run": bool(delivery.get("dry_run", True)),
        "delivery_configured": bool(delivery.get("configured")),
        "sends_network": bool(delivery.get("sends_network")),
        "cumulative_sent_keys": sent_keys["sent_key_count"],
        "cumulative_sent_previews": sent_keys["sent_preview_count"],
        "cumulative_sent_recipients": sent_keys["sent_recipient_count"],
        "training_rows": int(training.get("rows") or 0),
        "training_by_result": _top_counts(training.get("by_result") or {}),
        "training_by_family": _top_counts(training.get("by_family") or {}),
        "training_by_diagnosis": _top_counts(training.get("by_diagnosis") or {}),
        "outcome_review_rows": len(outcome_reviews),
        "outcome_review_accepted": len(accepted_reviews),
        "outcome_review_rejected": max(0, len(outcome_reviews) - len(accepted_reviews)),
        "training_outcome_review_linked": len(linked_training),
        "training_learning_kind": _top_counts(_count_field(linked_training, "outcome_learning_review_kind")),
        "training_learning_bucket": _top_counts(_count_field(linked_training, "outcome_learning_bucket")),
        "quality_operator_action": str(quality.get("operator_action") or ""),
        "quality_labels": _top_counts(quality.get("quality_labels") or {}),
        "active_lifecycle": (
            quality.get("active_signal_lifecycle")
            if isinstance(quality.get("active_signal_lifecycle"), dict)
            else {}
        ),
        "pfr_funnel": quality.get("pfr_funnel") if isinstance(quality.get("pfr_funnel"), dict) else {},
        "quality_report_exists": bool(quality),
        "bridge_skip_reasons": _top_counts(bridge.get("skip_reasons") or {}),
        "execution_allowed": execution_allowed,
    }


def _print_process_status() -> None:
    report = bot_status()
    relevant = report["relevant"]
    farm_status = report.get("farm_status") or {}
    if relevant:
        kinds = ", ".join(f"{k}={v}" for k, v in report["by_kind"].items())
        print(f" BOT:  RUNNING relevant={len(relevant)} ({kinds})")
        for row in relevant[:5]:
            print(f"       pid={row['pid']} kind={row['kind']} cmd={row['command']}")
    else:
        ignored = report["ignored_python"]
        suffix = f" (ignored unrelated python={ignored})" if ignored else ""
        print(f" BOT:  no relevant trading process found{suffix}")
    if farm_status:
        details = farm_status.get("details") or {}
        sleep_suffix = f" sleep={details.get('sleep_seconds')}s" if details.get("sleep_seconds") else ""
        print(
            "       "
            f"farm_stage={farm_status.get('stage') or '?'} "
            f"status_updated_ago={farm_status.get('updated_age_seconds', 0)}s "
            f"cycle_age={farm_status.get('cycle_age_seconds', 0)}s "
            f"loop={farm_status.get('loop')} paper_only={farm_status.get('paper_only')} "
            f"execution_allowed={farm_status.get('execution_allowed')}{sleep_suffix}"
        )


def _print_paper_product_status() -> None:
    st = paper_product_status()
    if not st["active"]:
        print(" PAPER PRODUCT: no private paper artifacts found")
        return

    print(
        " PAPER PRODUCT: "
        f"paper={st['paper_total']} {st['paper_by_status']} | "
        f"main-paper instructions={st['instructions']} accepted={st['accepted']} "
        f"queued={st['queued']} observed={st['observed']} "
        f"strict_trades={st['trades']} {st['trade_status']} "
        f"product_trades={st['product_trades']} {st['product_trade_status']} | "
        f"tg preview={st['preview_rendered']} eligible_cards={st['delivery_eligible']} "
        f"last_sent_messages={st['delivery_sent']} last_sent_cards={st['delivery_sent_cards']} "
        f"sent_cards_total={st['cumulative_sent_previews']}"
    )
    print(
        "                "
        f"telegram={'send' if st['sends_network'] else 'dry-run'} "
        f"configured={st['delivery_configured']} "
        f"targets={st['delivery_targets']} "
        f"execution_allowed={st['execution_allowed']} "
        f"provider_error={st['provider_error']} "
        f"skipped_unvalidated={st['skipped_unvalidated']} "
        f"delivery_errors={st['delivery_errors']} error_cards={st['delivery_error_cards']} "
        f"duplicate_messages={st['delivery_duplicates']} duplicate_cards={st['delivery_duplicate_cards']}"
    )
    if st["preview_skipped_quality_gate"]:
        print(
            "                "
            f"preview_quality_skip={st['preview_skipped_quality_gate']} "
            f"quality_skip_reasons={st['preview_quality_gate_reasons']}"
        )
    print(
        "                "
        f"product_live_ready={st['product_live_ready']} "
        f"product_live_blocked={st['product_live_blocked']} "
        f"active={st['product_active_trades']} "
        f"active_live_ready={st['product_active_live_ready']} "
        f"active_live_blocked={st['product_active_live_blocked']} "
        f"live_block={st['product_live_block']}"
    )
    if st["product_active_by_source"]:
        print(
            "                "
            f"active_source={st['product_active_by_source']} "
            f"active_family={st['product_active_by_family']}"
        )
    print(
        "                "
        f"outcomes rows={st['training_rows']} result={st['training_by_result']} "
        f"families={st['training_by_family']}"
    )
    if st["bridge_skip_reasons"] or st["training_by_diagnosis"]:
        print(
            "                "
            f"bridge_skip={st['bridge_skip_reasons']} "
            f"diagnosis={st['training_by_diagnosis']}"
        )
    if st["outcome_review_rows"] or st["training_outcome_review_linked"]:
        print(
            "                "
            f"outcome_reviews rows={st['outcome_review_rows']} "
            f"accepted={st['outcome_review_accepted']} rejected={st['outcome_review_rejected']} "
            f"linked_training={st['training_outcome_review_linked']} "
            f"kind={st['training_learning_kind']} bucket={st['training_learning_bucket']}"
        )
    if st["quality_report_exists"]:
        pfr = st["pfr_funnel"]
        print(
            "                "
            f"quality_action={st['quality_operator_action']} "
            f"quality_labels={st['quality_labels']}"
        )
        lifecycle = st["active_lifecycle"]
        if lifecycle:
            print(
                "                "
                f"active_timing=oldest_h:{lifecycle.get('oldest_age_hours', 0)} "
                f"next_expiry_h:{lifecycle.get('next_expiry_hours')} "
                f"overdue:{lifecycle.get('overdue_expiry', 0)} "
                f"expiry:{lifecycle.get('expiry_buckets') or {}}"
            )
        if pfr:
            print(
                "                "
                f"pfr_ready={pfr.get('catalog_ready', 0)} "
                f"pfr_rejected={pfr.get('catalog_rejected_quality', 0)} "
                f"strict_instructions={pfr.get('bridge_instructions', 0)} "
                f"pfr_skip={pfr.get('bridge_skip_reasons') or {}} "
                f"last_pfr={pfr.get('last_cycle_pfr_counts') or {}}"
            )
            if pfr.get("near_trigger_counts") or pfr.get("cycle_resource_reasons"):
                print(
                    "                "
                    f"pfr_near={pfr.get('near_trigger_counts') or {}} "
                    f"cycle_blockers={pfr.get('cycle_resource_reasons') or {}}"
                )


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
    _print_paper_product_status()
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
