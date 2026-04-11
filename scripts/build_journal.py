"""
build_journal.py — generate trading journal Excel from signal_log.jsonl + signal_labels.jsonl.

Run once a day after label_outcomes.py:
    python scripts/label_outcomes.py
    python scripts/build_journal.py

Output: scripts/journal.xlsx  (two sheets: Журнал + Симулятор)
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import openpyxl
    from openpyxl.styles import PatternFill, Font, Alignment
    from openpyxl.utils import get_column_letter
except ImportError:
    print("openpyxl не установлен. Запустите: pip install openpyxl")
    sys.exit(1)

SIGNAL_LOG    = Path(__file__).parent / "signal_log.jsonl"
SIGNAL_LABELS = Path(__file__).parent / "signal_labels.jsonl"
JOURNAL_PATH  = Path(__file__).parent / "journal.xlsx"

# Excel fill colors
FILL_HEADER   = PatternFill("solid", fgColor="1F4E79")
FILL_INPUT    = PatternFill("solid", fgColor="FFF2CC")   # yellow — user editable
FILL_TP       = PatternFill("solid", fgColor="C6EFCE")
FILL_STOP     = PatternFill("solid", fgColor="FFC7CE")
FILL_TIME     = PatternFill("solid", fgColor="FFEB9C")
FILL_FADE     = PatternFill("solid", fgColor="E2EFDA")
FILL_SUMMARY  = PatternFill("solid", fgColor="D9E1F2")

FONT_HEADER   = Font(color="FFFFFF", bold=True)
FONT_BOLD     = Font(bold=True)


def _load_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def _fmt_dt(ts_ms) -> str:
    if not ts_ms:
        return ""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%d.%m %H:%M")


def _set_col_widths(ws, widths: list) -> None:
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = w


def build() -> None:
    signals = _load_jsonl(SIGNAL_LOG)
    labels  = {l["signal_id"]: l for l in _load_jsonl(SIGNAL_LABELS)}

    rows = []
    for sig in signals:
        lab      = labels.get(sig.get("signal_id"), {})
        side_str = "LONG" if sig.get("side") == "buy" else "SHORT"
        channel  = "BB FADE" if sig.get("source") == "bb_fade" else "ENTRY"
        rows.append({
            "dt":       _fmt_dt(sig.get("ts_ms")),
            "symbol":   sig.get("symbol", ""),
            "channel":  channel,
            "regime":   sig.get("regime", ""),
            "style":    sig.get("style", ""),
            "side":     side_str,
            "entry":    sig.get("close"),
            "sl":       sig.get("sl"),
            "tp":       sig.get("tp"),
            "hold_min": sig.get("max_hold_min"),
            "outcome":  lab.get("outcome", ""),
            "exit_r":   lab.get("exit_r"),
            "mfe_r":    lab.get("mfe_r"),
            "mae_r":    lab.get("mae_r"),
            "adx_1h":    sig.get("adx_1h"),
            "slope_1h":  sig.get("slope_1h"),
            "slope_15m": sig.get("slope_15m"),
            "funding":   sig.get("funding") or 0.0,
        })

    wb = openpyxl.Workbook()
    _build_sheet1(wb, rows)
    _build_sheet2(wb, rows)
    wb.save(JOURNAL_PATH)
    print(f"Журнал: {JOURNAL_PATH}  ({len(rows)} сигналов, {sum(1 for r in rows if r['outcome'])} закрыто)")


def _build_sheet1(wb, rows: list) -> None:
    ws = wb.active
    ws.title = "Журнал"

    headers = ["Дата", "Пара", "Канал", "Режим", "Стиль", "Сторона",
               "Вход", "SL", "TP", "Hold(мин)", "Исход", "R", "MFE_R", "MAE_R",
               "ADX_1H", "Slope_1H°", "Slope_15m°", "Финанс."]

    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = FILL_HEADER
        cell.font = FONT_HEADER
        cell.alignment = Alignment(horizontal="center")

    outcome_fill = {"TP": FILL_TP, "STOP": FILL_STOP, "TIME_EXIT": FILL_TIME}

    for r, row in enumerate(rows, 2):
        vals = [
            row["dt"], row["symbol"], row["channel"], row["regime"], row["style"],
            row["side"], row["entry"], row["sl"], row["tp"], row["hold_min"],
            row["outcome"], row["exit_r"], row["mfe_r"], row["mae_r"],
            round(row["adx_1h"], 1) if row["adx_1h"] else "",
            round(row["slope_1h"], 1) if row["slope_1h"] is not None else "",
            round(row["slope_15m"], 1) if row["slope_15m"] is not None else "",
            row["funding"],
        ]
        for col, val in enumerate(vals, 1):
            cell = ws.cell(row=r, column=col, value=val)
            if col == 11 and row["outcome"] in outcome_fill:
                cell.fill = outcome_fill[row["outcome"]]
            if col == 3 and row["channel"] == "BB FADE":
                cell.fill = FILL_FADE

    _set_col_widths(ws, [14, 12, 9, 10, 7, 8, 10, 10, 10, 10, 10, 7, 7, 7, 8, 9, 10, 10])
    ws.freeze_panes = "A2"


def _build_sheet2(wb, rows: list) -> None:
    ws = wb.create_sheet("Симулятор")

    headers = [
        "Нотионал$", "Плечо",                            # A, B — user input
        "Дата", "Пара", "Канал", "Сторона",              # C-F — data
        "Вход", "SL", "TP", "Исход", "R", "MAE_R",      # G-L — data
        "Маржа$", "Цена ликв.", "Реальный исход",        # M-O — formula
        "P&L$", "P&L%", "Комиссия$", "Финанс.$", "Чистый P&L$",  # P-T — formula
    ]

    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = FILL_HEADER
        cell.font = FONT_HEADER
        cell.alignment = Alignment(horizontal="center")

    # Mark input column headers
    for col in (1, 2):
        ws.cell(row=1, column=col).fill = PatternFill("solid", fgColor="F4B942")

    outcome_fill = {"TP": FILL_TP, "STOP": FILL_STOP, "TIME_EXIT": FILL_TIME}

    for r, row in enumerate(rows, 2):
        # Input columns — yellow, prefilled with defaults
        ws.cell(row=r, column=1, value=1000).fill = FILL_INPUT  # Нотионал$
        ws.cell(row=r, column=2, value=10).fill   = FILL_INPUT  # Плечо

        # Data columns C-L
        data = {
            3:  row["dt"],       4: row["symbol"],  5: row["channel"],
            6:  row["side"],     7: row["entry"],   8: row["sl"],
            9:  row["tp"],       10: row["outcome"] or "",
            11: row["exit_r"],   12: row["mae_r"],
        }
        for col, val in data.items():
            cell = ws.cell(row=r, column=col, value=val)
            if col == 10 and row["outcome"] in outcome_fill:
                cell.fill = outcome_fill[row["outcome"]]

        if not (row["entry"] and row["sl"] and row["tp"]):
            continue

        # M: Маржа = Нотионал / Плечо (блокируется на счёте)
        ws.cell(row=r, column=13, value=f"=A{r}/B{r}")

        # N: Цена ликвидации (isolated margin, MMR=0.5% + taker fee 0.05% = 0.0055)
        # LONG:  entry * (1 - 1/leverage + 0.0055)
        # SHORT: entry * (1 + 1/leverage - 0.0055)
        ws.cell(row=r, column=14, value=(
            f'=IF(F{r}="LONG",'
            f'G{r}*(1-1/B{r}+0.0055),'
            f'G{r}*(1+1/B{r}-0.0055))'
        ))

        # O: Реальный исход — проверяем ликвидацию по MAE
        # LONG ликвидация: цена упала ниже liq_price = entry - mae*sl_dist <= N
        # SHORT ликвидация: цена выросла выше liq_price = entry + mae*sl_dist >= N
        ws.cell(row=r, column=15, value=(
            f'=IF(J{r}="","",IF(AND(L{r}<>"",F{r}="LONG",'
            f'G{r}-L{r}*ABS(G{r}-H{r})<=N{r}),"ЛИКВИДАЦИЯ",'
            f'IF(AND(L{r}<>"",F{r}="SHORT",'
            f'G{r}+L{r}*ABS(H{r}-G{r})>=N{r}),"ЛИКВИДАЦИЯ",'
            f'J{r})))'
        ))

        # P: P&L$ = если ликвидация → -Маржа(M), иначе нотионал(A) * sl_pct * exit_R
        ws.cell(row=r, column=16, value=(
            f'=IF(OR(O{r}="",K{r}=""),"",IF(O{r}="ЛИКВИДАЦИЯ",-M{r},'
            f'A{r}*ABS(G{r}-H{r})/G{r}*K{r}))'
        ))

        # Q: P&L% = P&L$ / Маржа(M) * 100  (% от заблокированного капитала)
        ws.cell(row=r, column=17, value=(
            f'=IF(OR(P{r}="",M{r}=0),"",P{r}/M{r}*100)'
        ))

        # R: Комиссия$ = нотионал(A) * 0.1% (тейкер вход + выход, 0.05%×2)
        ws.cell(row=r, column=18, value=f'=IF(M{r}="","",A{r}*0.001)')

        # S: Финансирование$ = нотионал(A) * ставка_финансирования
        funding = row["funding"] or 0.0
        ws.cell(row=r, column=19, value=f'=IF(M{r}="","",A{r}*{funding})')

        # T: Чистый P&L$ = P&L - Комиссия - Финансирование
        ws.cell(row=r, column=20, value=f'=IF(P{r}="","",P{r}-R{r}-S{r})')

    # Summary box
    last_r    = len(rows) + 1
    sum_r     = last_r + 2
    n_closed  = sum(1 for r in rows if r["outcome"] in ("TP", "STOP", "TIME_EXIT"))
    n_tp      = sum(1 for r in rows if r["outcome"] == "TP")
    wr_str    = f"{n_tp}/{n_closed} = {n_tp/n_closed*100:.0f}%" if n_closed else "—"

    summary = [
        ("Всего сигналов", len(rows), None),
        ("Закрыто", n_closed, None),
        ("WR", wr_str, None),
        ("P&L$ (брутто)", f"=SUM(P2:P{last_r})", 16),
        ("Чистый P&L$", f"=SUM(T2:T{last_r})", 20),
    ]
    for i, (label, val, sum_col) in enumerate(summary):
        cell_l = ws.cell(row=sum_r + i, column=1, value=label)
        cell_l.font = FONT_BOLD
        cell_l.fill = FILL_SUMMARY
        cell_v = ws.cell(row=sum_r + i, column=2,
                         value=val if sum_col is None else f"=SUM({get_column_letter(sum_col)}2:{get_column_letter(sum_col)}{last_r})")
        cell_v.fill = FILL_SUMMARY

    _set_col_widths(ws, [9, 7, 14, 12, 9, 8, 10, 10, 10, 10, 7, 7, 11, 12, 14, 9, 7, 10, 10, 12])
    ws.freeze_panes = "C2"


if __name__ == "__main__":
    build()
