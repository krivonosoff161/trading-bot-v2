"""
build_journal.py — generate trading journal Excel from signal_log.jsonl + signal_labels.jsonl.

Run once a day after label_outcomes.py:
    python scripts/label_outcomes.py
    python scripts/build_journal.py

Output: scripts/journal.xlsx  (three sheets: Журнал + Симулятор + Реальные сделки)
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

from dotenv import load_dotenv
load_dotenv()

try:
    import openpyxl
    from openpyxl.styles import PatternFill, Font, Alignment
    from openpyxl.utils import get_column_letter
except ImportError:
    print("openpyxl не установлен. Запустите: pip install openpyxl")
    sys.exit(1)

from src.exchange.okx_client import OKXClient

_ROOT         = Path(__file__).resolve().parent.parent
SIGNAL_LOG    = _ROOT / "logs" / "signals" / "signal_log.jsonl"
SIGNAL_LABELS = _ROOT / "logs" / "signals" / "signal_labels.jsonl"
JOURNAL_PATH  = Path(__file__).parent / "journal.xlsx"
CONFIG_PATH   = Path(__file__).parent.parent / "config.yaml"

# Excel fill colors
FILL_HEADER   = PatternFill("solid", fgColor="1F4E79")
FILL_INPUT    = PatternFill("solid", fgColor="FFF2CC")   # yellow — user editable
FILL_TP       = PatternFill("solid", fgColor="C6EFCE")
FILL_STOP     = PatternFill("solid", fgColor="FFC7CE")
FILL_TIME     = PatternFill("solid", fgColor="FFEB9C")
FILL_FADE     = PatternFill("solid", fgColor="E2EFDA")
FILL_SUMMARY  = PatternFill("solid", fgColor="D9E1F2")
FILL_PROFIT   = PatternFill("solid", fgColor="C6EFCE")
FILL_LOSS     = PatternFill("solid", fgColor="FFC7CE")
FILL_SIGNAL   = PatternFill("solid", fgColor="D9EAD3")

FONT_HEADER   = Font(color="FFFFFF", bold=True)
FONT_BOLD     = Font(bold=True)

_SIGNAL_MATCH_WINDOW_MS = 30 * 60 * 1000  # ±30 min


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


def _load_config_symbols() -> list:
    if yaml is None or not CONFIG_PATH.exists():
        return []
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("real_trades_symbols") or []
    except Exception:
        return []


def _set_col_widths(ws, widths: list) -> None:
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = w


# ---------------------------------------------------------------------------
# Real trades — OKX fetch + helpers
# ---------------------------------------------------------------------------

async def _discover_inst_ids(client: OKXClient) -> set:
    """Discover all SWAP instIds traded via fills-history (works without instId filter)."""
    inst_ids: set = set()
    after: str | None = None
    for _ in range(20):  # max 2000 fills = covers ~3 months for active traders
        params: dict = {"instType": "SWAP", "limit": "100"}
        if after:
            params["after"] = after
        data = await client._get("/api/v5/trade/fills-history", params)
        if data.get("code") != "0":
            break
        batch = data.get("data") or []
        for f in batch:
            inst_id = f.get("instId", "")
            if inst_id.endswith("-SWAP"):
                inst_ids.add(inst_id)
        if len(batch) < 100:
            break
        after = batch[-1]["tradeId"]
    return inst_ids


async def _fetch_positions_for_inst(client: OKXClient, inst_id: str) -> list:
    """Fetch all closed positions for one instId."""
    positions: list = []
    after: str | None = None
    while True:
        params: dict = {"instId": inst_id, "limit": "100"}
        if after:
            params["after"] = after
        data = await client._get("/api/v5/account/positions-history", params)
        batch = data.get("data") or []
        positions.extend(batch)
        if len(batch) < 100:
            break
        after = batch[-1]["posId"]
    return positions


async def _fetch_real_trades_async() -> list:
    api_key    = os.getenv("OKX_API_KEY", "")
    secret_key = os.getenv("OKX_SECRET_KEY", "")
    passphrase = os.getenv("OKX_PASSPHRASE", "")
    is_demo    = os.getenv("OKX_IS_DEMO", "1") == "1"

    if not api_key:
        print("OKX credentials not found — пропускаем вкладку Реальные сделки")
        return []

    client = OKXClient(api_key, secret_key, passphrase, is_demo=is_demo)
    try:
        inst_ids = await _discover_inst_ids(client)
        if not inst_ids:
            print("Нет сделок за последний период")
            return []
        print(f"Найдено пар: {len(inst_ids)} — {', '.join(sorted(inst_ids))}")
        all_positions: list = []
        for inst_id in sorted(inst_ids):
            positions = await _fetch_positions_for_inst(client, inst_id)
            all_positions.extend(positions)
        return all_positions
    finally:
        await client.close()


def _load_real_trades() -> list:
    try:
        return asyncio.run(_fetch_real_trades_async())
    except Exception as e:
        print(f"Ошибка загрузки реальных сделок: {e}")
        return []


def _match_signal(signals: list, symbol: str, open_ms: int) -> str:
    for sig in signals:
        if sig.get("symbol") != symbol:
            continue
        if abs(sig.get("ts_ms", 0) - open_ms) <= _SIGNAL_MATCH_WINDOW_MS:
            dt   = datetime.fromtimestamp(sig["ts_ms"] / 1000, tz=timezone.utc).strftime("%H:%M")
            side = "BUY" if sig.get("side") == "buy" else "SELL"
            return f"✓ {dt} {side}"
    return "—"


# ---------------------------------------------------------------------------
# build()
# ---------------------------------------------------------------------------

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

    real_trades    = _load_real_trades()

    wb = openpyxl.Workbook()
    _build_sheet1(wb, rows)
    _build_sheet2(wb, rows)
    _build_sheet3(wb, real_trades, signals)
    wb.save(JOURNAL_PATH)
    print(f"Журнал: {JOURNAL_PATH}  ({len(rows)} сигналов, {sum(1 for r in rows if r['outcome'])} закрыто)")


# ---------------------------------------------------------------------------
# Sheet 1 — Журнал
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Sheet 2 — Симулятор
# ---------------------------------------------------------------------------

def _build_sheet2(wb, rows: list) -> None:
    ws = wb.create_sheet("Симулятор")

    headers = [
        "Капитал$", "Плечо",                             # A, B — user input
        "Дата", "Пара", "Канал", "Сторона",              # C-F — data
        "Вход", "SL", "TP", "Исход", "R", "MAE_R",      # G-L — data
        "Позиция$", "Цена ликв.", "Реальный исход",      # M-O — formula
        "P&L$", "P&L%", "Комиссия$", "Финанс.$", "Чистый P&L$",  # P-T — formula
    ]

    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = FILL_HEADER
        cell.font = FONT_HEADER
        cell.alignment = Alignment(horizontal="center")

    for col in (1, 2):
        ws.cell(row=1, column=col).fill = PatternFill("solid", fgColor="F4B942")

    outcome_fill = {"TP": FILL_TP, "STOP": FILL_STOP, "TIME_EXIT": FILL_TIME}

    for r, row in enumerate(rows, 2):
        ws.cell(row=r, column=1, value=100).fill  = FILL_INPUT
        ws.cell(row=r, column=2, value=10).fill   = FILL_INPUT

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

        ws.cell(row=r, column=13, value=f"=A{r}*B{r}")
        ws.cell(row=r, column=14, value=(
            f'=IF(F{r}="LONG",'
            f'G{r}*(1-1/B{r}+0.0055),'
            f'G{r}*(1+1/B{r}-0.0055))'
        ))
        ws.cell(row=r, column=15, value=(
            f'=IF(J{r}="","",IF(AND(L{r}<>"",F{r}="LONG",'
            f'G{r}-L{r}*ABS(G{r}-H{r})<=N{r}),"ЛИКВИДАЦИЯ",'
            f'IF(AND(L{r}<>"",F{r}="SHORT",'
            f'G{r}+L{r}*ABS(H{r}-G{r})>=N{r}),"ЛИКВИДАЦИЯ",'
            f'J{r})))'
        ))
        ws.cell(row=r, column=16, value=(
            f'=IF(OR(O{r}="",K{r}=""),"",IF(O{r}="ЛИКВИДАЦИЯ",-A{r},'
            f'M{r}*ABS(G{r}-H{r})/G{r}*K{r}))'
        ))
        ws.cell(row=r, column=17, value=(
            f'=IF(OR(P{r}="",A{r}=0),"",P{r}/A{r}*100)'
        ))
        ws.cell(row=r, column=18, value=f'=IF(M{r}="","",M{r}*0.001)')
        funding = row["funding"] or 0.0
        ws.cell(row=r, column=19, value=f'=IF(M{r}="","",M{r}*{funding})')
        ws.cell(row=r, column=20, value=f'=IF(P{r}="","",P{r}-R{r}-S{r})')

    last_r   = len(rows) + 1
    sum_r    = last_r + 2
    n_closed = sum(1 for r in rows if r["outcome"] in ("TP", "STOP", "TIME_EXIT"))
    n_tp     = sum(1 for r in rows if r["outcome"] == "TP")
    wr_str   = f"{n_tp}/{n_closed} = {n_tp/n_closed*100:.0f}%" if n_closed else "—"

    summary = [
        ("Всего сигналов", len(rows), None),
        ("Закрыто", n_closed, None),
        ("WR", wr_str, None),
        ("P&L$ (брутто)", None, 16),
        ("Чистый P&L$",  None, 20),
    ]
    for i, (label, val, sum_col) in enumerate(summary):
        cell_l = ws.cell(row=sum_r + i, column=1, value=label)
        cell_l.font = FONT_BOLD
        cell_l.fill = FILL_SUMMARY
        cell_v = ws.cell(
            row=sum_r + i, column=2,
            value=val if sum_col is None else f"=SUM({get_column_letter(sum_col)}2:{get_column_letter(sum_col)}{last_r})",
        )
        cell_v.fill = FILL_SUMMARY

    _set_col_widths(ws, [9, 7, 14, 12, 9, 8, 10, 10, 10, 10, 7, 7, 11, 12, 14, 9, 7, 10, 10, 12])
    ws.freeze_panes = "C2"


# ---------------------------------------------------------------------------
# Sheet 3 — Реальные сделки
# ---------------------------------------------------------------------------

def _build_sheet3(wb, real_trades: list, signals: list) -> None:
    ws = wb.create_sheet("Реальные сделки")

    headers = [
        "Откр.", "Закр.", "Пара", "Направл.", "Плечо",
        "Вход", "Выход", "Hold(мин)",
        "P&L брутто", "Комиссия", "Финансир.", "NET P&L", "P&L%",
        "Сигнал бота",
    ]

    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = FILL_HEADER
        cell.font = FONT_HEADER
        cell.alignment = Alignment(horizontal="center")

    if not real_trades:
        ws.cell(row=2, column=1, value="Нет данных (OKX не вернул позиции)")
        _set_col_widths(ws, [13, 13, 12, 9, 6, 10, 10, 9, 11, 10, 10, 11, 7, 16])
        return

    processed = []
    for pos in real_trades:
        open_ms  = int(pos.get("cTime") or 0)
        close_ms = int(pos.get("uTime") or 0)
        inst_id  = pos.get("instId", "")
        symbol   = inst_id.replace("-SWAP", "")

        open_px      = float(pos.get("openAvgPx")   or 0)
        close_px     = float(pos.get("closeAvgPx")  or 0)
        gross_pnl    = float(pos.get("pnl")         or 0)   # price-move P&L before fees
        fee          = float(pos.get("fee")          or 0)   # commission (negative)
        funding      = float(pos.get("fundingFee")   or 0)
        realized_pnl = float(pos.get("realizedPnl") or 0)   # gross_pnl + fee
        net_pnl      = realized_pnl + funding                # true net
        pnl_pct      = float(pos.get("pnlRatio")    or 0) * 100
        lever        = int(float(pos.get("lever")    or 1))
        hold_min     = round((close_ms - open_ms) / 60_000) if close_ms > open_ms else 0

        direction    = (pos.get("direction") or "long").upper()
        signal_match = _match_signal(signals, symbol, open_ms)

        processed.append({
            "open_ms":   open_ms,
            "open_dt":   _fmt_dt(open_ms),
            "close_dt":  _fmt_dt(close_ms),
            "symbol":    symbol,
            "direction": direction,
            "lever":     lever,
            "open_px":   open_px,
            "close_px":  close_px,
            "hold_min":  hold_min,
            "pnl":       round(gross_pnl, 4),
            "fee":       round(fee, 4),
            "funding":   round(funding, 4),
            "net_pnl":   round(net_pnl, 4),
            "pnl_pct":   round(pnl_pct, 2),
            "signal":    signal_match,
        })

    processed.sort(key=lambda x: x["open_ms"], reverse=True)

    for r, row in enumerate(processed, 2):
        vals = [
            row["open_dt"], row["close_dt"], row["symbol"], row["direction"],
            row["lever"], row["open_px"], row["close_px"], row["hold_min"],
            row["pnl"], row["fee"], row["funding"], row["net_pnl"],
            row["pnl_pct"], row["signal"],
        ]
        for col, val in enumerate(vals, 1):
            cell = ws.cell(row=r, column=col, value=val)
            if col == 12:  # NET P&L
                cell.fill = FILL_PROFIT if row["net_pnl"] >= 0 else FILL_LOSS
            if col == 14 and row["signal"] != "—":
                cell.fill = FILL_SIGNAL

    n_matched = sum(1 for r in processed if r["signal"] != "—")
    _set_col_widths(ws, [13, 13, 12, 9, 6, 10, 10, 9, 11, 10, 10, 11, 7, 16])
    ws.freeze_panes = "A2"

    print(f"Реальные сделки: {len(processed)} позиций, {n_matched} совпали с сигналами бота")


if __name__ == "__main__":
    build()
