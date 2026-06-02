# -*- coding: utf-8 -*-
"""
test_scanner_isolation.py — страховка периметра инфо-эдж сканера.

Сканер = paper-фильтр. Эти тесты ловят регресс, если кто-то позже случайно
свяжет петлю с исполнением ордеров. Money-guard хук стережёт .env/AUTO_TRADE,
но НЕ импорт ордер-функции — это делает данный тест.

Запуск:  python tests/test_scanner_isolation.py      (или pytest)
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_FORBIDDEN = ("okx_client", "auto_execute", "place_market", "place_order")


def test_scanner_import_tree_has_no_order_funcs():
    """Дерево импортов scanner_v0 не должно содержать ордер-модулей."""
    import src.scout.scanner_v0  # noqa: F401
    bad = [m for m in sys.modules if any(f in m for f in _FORBIDDEN)]
    assert not bad, f"сканер дотянулся до ордер-модулей: {bad}"


def test_journal_validator_rejects_incomplete_card():
    """Журнал не должен писать карточку без обязательных полей (анти-survivorship)."""
    from src.scout import scanner_journal as J
    incomplete = {"card_id": "x", "ts_utc": J.now_iso(), "verdict": "GO"}  # нет horizon/source_ts
    assert J.write_row(incomplete) is None


def test_scout_card_never_emits_entry_signal():
    """Поля карточки не содержат торгового ключа entry_signal=='ENTRY' (висит auto_execute)."""
    from src.scout import scanner_journal as J
    row = J.build_row(
        source_url="https://example.com/x", source_ts="2026-06-02", layer=1,
        asset="BTC", trigger_type="rss_headline", headline="t", verdict="GO",
        horizon_hours=48, price_at_decision=1.0,
    )
    assert "entry_signal" not in row
    assert row["verdict"] in ("GO", "NO_GO", "WATCH")


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"[PASS] {name}")
            except AssertionError as e:
                failed += 1
                print(f"[FAIL] {name}: {e}")
    print(f"\n{'ВСЕ ТЕСТЫ ПРОШЛИ' if not failed else f'{failed} упало'}")
    sys.exit(1 if failed else 0)
