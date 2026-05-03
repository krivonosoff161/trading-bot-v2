"""
cleanup_logs.py — log rotation and archiving for all 4 processes.

Policy:
  logs/scanner/       — keep 60 days, archive older to logs_archive/scanner/YYYY-MM/
  logs/users/*/analyses/ — keep 90 days, archive older to logs_archive/users/{id}/YYYY-MM/
  logs/signals/       — never delete; monthly backup copy to logs_archive/signals/
  logs/pump/          — monthly split: pump_signals_YYYY-MM.jsonl to logs_archive/pump/
  logs/fade/          — dead (old scanner), move to logs_archive/fade/ once

Run: python scripts/cleanup_logs.py
Called from: update_journal.bat
"""
import json
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT         = Path(__file__).resolve().parent.parent
LOGS         = ROOT / "logs"
ARCHIVE      = ROOT / "logs_archive"
NOW          = datetime.now(tz=timezone.utc)


def _log(msg: str) -> None:
    print(f"[cleanup {NOW.strftime('%H:%M:%S')}] {msg}")


def _archive_old_folders(src_dir: Path, archive_dir: Path, keep_days: int) -> None:
    """Move dated folders (name starts with YYYY-MM-DD) older than keep_days to archive."""
    if not src_dir.exists():
        return
    cutoff = NOW - timedelta(days=keep_days)
    moved = 0
    for folder in sorted(src_dir.iterdir()):
        if not folder.is_dir():
            continue
        date_str = folder.name[:10]
        try:
            folder_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if folder_dt >= cutoff:
            continue
        month_str = folder_dt.strftime("%Y-%m")
        dest = archive_dir / month_str / folder.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folder), str(dest))
        moved += 1
    if moved:
        _log(f"{src_dir.name}/: archived {moved} folders older than {keep_days}d -> {archive_dir.name}/")


def _backup_signals() -> None:
    """Monthly backup copy of signal_log.jsonl — only if this month's backup doesn't exist yet."""
    sig_file = LOGS / "signals" / "signal_log.jsonl"
    if not sig_file.exists():
        return
    month_str = NOW.strftime("%Y-%m")
    backup_dir = ARCHIVE / "signals"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"signal_log_{month_str}.jsonl"
    if not backup_path.exists():
        shutil.copy2(str(sig_file), str(backup_path))
        _log(f"signals/: monthly backup -> logs_archive/signals/signal_log_{month_str}.jsonl")


def _split_pump_signals() -> None:
    """Monthly split of pump_signals.jsonl — move previous month's entries to archive."""
    pump_file = LOGS / "pump" / "pump_signals.jsonl"
    if not pump_file.exists():
        return
    current_month = NOW.strftime("%Y-%m")
    prev_month = (NOW.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    archive_dir = ARCHIVE / "pump"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_file = archive_dir / f"pump_signals_{prev_month}.jsonl"
    if archive_file.exists():
        return  # already split this month
    keep_lines = []
    archive_lines = []
    with open(pump_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ts = json.loads(line).get("ts_utc", "") or json.loads(line).get("ts_wall", "")
                if ts[:7] == current_month:
                    keep_lines.append(line)
                else:
                    archive_lines.append(line)
            except Exception:
                keep_lines.append(line)
    if archive_lines:
        with open(archive_file, "w", encoding="utf-8") as f:
            f.write("\n".join(archive_lines) + "\n")
        with open(pump_file, "w", encoding="utf-8") as f:
            f.write("\n".join(keep_lines) + ("\n" if keep_lines else ""))
        _log(f"pump/: split {len(archive_lines)} entries -> logs_archive/pump/pump_signals_{prev_month}.jsonl")


def _migrate_fade() -> None:
    """One-time migration: move logs/fade/ to logs_archive/fade/."""
    fade_dir = LOGS / "fade"
    if not fade_dir.exists():
        return
    dest = ARCHIVE / "fade"
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(fade_dir), str(dest))
    _log("fade/: migrated dead BB FADE scanner logs -> logs_archive/fade/")


def main() -> None:
    _log("starting cleanup")
    ARCHIVE.mkdir(parents=True, exist_ok=True)

    _migrate_fade()
    _archive_old_folders(LOGS / "scanner",  ARCHIVE / "scanner",  keep_days=60)

    users_dir = LOGS / "users"
    if users_dir.exists():
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir():
                _archive_old_folders(
                    user_dir / "analyses",
                    ARCHIVE / "users" / user_dir.name,
                    keep_days=90,
                )

    _backup_signals()
    _split_pump_signals()
    _log("cleanup done")


if __name__ == "__main__":
    main()
