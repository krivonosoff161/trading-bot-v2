"""
Operator launcher for the chart analyzer.

Workflow:
- find latest non-annotated image in "обучение проба"
- ask operator for symbol from menu
- suggest UTC time from file mtime
- optionally send client summary to Telegram
- call analyze_chart.run() once
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
STUDY_DIR = ROOT / "обучение проба"

sys.path.insert(0, str(ROOT))

from scripts.analyze_chart import run  # noqa: E402


ALLOW_AUTO_EXECUTE_ENV = "RUN_LATEST_ANALYSIS_ALLOW_AUTO_EXECUTE"

SYMBOL_MAP = {
    "1": "BTC-USDT",
    "2": "ETH-USDT",
    "3": "SOL-USDT",
    "4": "DOGE-USDT",
    "5": "XRP-USDT",
}


def _latest_source_image() -> Path | None:
    if not STUDY_DIR.exists():
        return None
    candidates = [
        p
        for p in STUDY_DIR.iterdir()
        if p.is_file()
        and p.suffix.lower() in {".jpg", ".jpeg", ".png"}
        and "_annotated" not in p.name.lower()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _choose_symbol() -> str:
    print("Выбери пару:")
    print("  1) BTC-USDT")
    print("  2) ETH-USDT")
    print("  3) SOL-USDT")
    print("  4) DOGE-USDT")
    print("  5) XRP-USDT")
    print("  M) Ввести вручную")
    choice = input("Номер пары [1-5/M]: ").strip()
    if choice in SYMBOL_MAP:
        return SYMBOL_MAP[choice]
    if choice.lower() == "m":
        symbol = input("Символ вручную (например BTC-USDT): ").strip().upper()
        if symbol:
            return symbol
    raise ValueError("Не выбрана пара.")


def _suggest_captured_at(image_path: Path) -> str:
    dt = datetime.fromtimestamp(image_path.stat().st_mtime, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ask_captured_at(default_utc: str) -> str:
    print()
    print(f"Автовремя из файла скрина: {default_utc}")
    confirm = input("Использовать это время? [Y/n]: ").strip().lower()
    if confirm in {"", "y", "yes", "д", "да"}:
        return default_utc
    manual = input("Время скрина UTC (например 2026-03-09T11:42:35Z): ").strip()
    if not manual:
        raise ValueError("Не указано время скрина.")
    return manual


def _ask_send_telegram() -> bool:
    choice = input("Отправить client summary в Telegram? [y/N]: ").strip().lower()
    return choice in {"y", "yes", "д", "да"}


def main() -> int:
    print("================================================")
    print("  CHART ANALYZER - последний скрин из обучение проба/")
    print("================================================")
    print()

    latest_img = _latest_source_image()
    if latest_img is None:
        print(f"Скрин не найден в папке: {STUDY_DIR}")
        print("Положи .jpg/.jpeg/.png файл (не _annotated) и запусти снова.")
        return 1

    print(f"Найден скрин: {latest_img.name}")
    print()

    try:
        symbol = _choose_symbol()
        captured_at = _ask_captured_at(_suggest_captured_at(latest_img))
        send_telegram = _ask_send_telegram()
    except ValueError as exc:
        print(exc)
        return 1

    print()
    print(f"Запускаю анализ для {symbol} ...")
    print()

    result = asyncio.run(
        run(
            symbol=symbol,
            captured_at_iso=captured_at,
            limit=100,
            image_path=str(latest_img),
            send_telegram=send_telegram,
        )
    )

    # Auto-execute if ENTRY signal and AUTO_TRADE enabled
    if result and result.get("entry_signal") == "ENTRY":
        allow_auto_execute = os.getenv(ALLOW_AUTO_EXECUTE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
        if allow_auto_execute:
            from scripts.auto_execute import AUTO_TRADE, execute_signal
        else:
            AUTO_TRADE = False
            execute_signal = None
        if AUTO_TRADE:
            print()
            print(f"AUTO_TRADE включён — открываю позицию {symbol} ...")
            asyncio.run(execute_signal(result))
        elif not allow_auto_execute:
            print()
            print(f"Auto-execute blocked: set {ALLOW_AUTO_EXECUTE_ENV}=1 only for explicit manual execution tests.")

    print()
    print("Готово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
