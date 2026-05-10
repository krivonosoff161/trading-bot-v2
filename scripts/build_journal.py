"""
build_journal.py — generate trading journal Excel from signal_log.jsonl + signal_labels.jsonl.

Run once a day after label_outcomes.py:
    python scripts/label_outcomes.py
    python scripts/build_journal.py

Output: scripts/journal.xlsx  (four sheets: Журнал + Симулятор + Реальные сделки + Памп)
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

_ROOT            = Path(__file__).resolve().parent.parent
SIGNAL_LOG          = _ROOT / "logs" / "signals" / "signal_log.jsonl"
SIGNAL_LABELS       = _ROOT / "logs" / "signals" / "signal_labels.jsonl"
PUMP_SIGNALS_LOG    = _ROOT / "logs" / "pump" / "pump_signals.jsonl"
PUMP_LABELS_LOG     = _ROOT / "logs" / "pump" / "pump_labels.jsonl"
MAIN_SIGNALS_LOG    = _ROOT / "logs" / "signals" / "main_signals.jsonl"
MAIN_SIGNALS_LABELS = _ROOT / "logs" / "signals" / "main_signals_labels.jsonl"
JOURNAL_PATH     = Path(__file__).parent / "journal.xlsx"
CONFIG_PATH      = Path(__file__).parent.parent / "config.yaml"

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


def _load_pump_trades() -> list:
    entries = {r["signal_id"]: r for r in _load_jsonl(PUMP_SIGNALS_LOG) if r.get("type") == "ENTRY"}
    out = []
    for rec in _load_jsonl(PUMP_LABELS_LOG):
        if rec.get("type") != "EXIT":
            continue
        entry = entries.get(rec.get("signal_id", ""), {})
        opened = rec.get("opened_at", "")
        out.append({
            "dt":        opened[:16].replace("T", " ") if opened else "",
            "sym":       rec.get("sym", ""),
            "tier":      entry.get("screener_tier", ""),
            "outcome":   rec.get("exit_reason", ""),
            "entry_px":  rec.get("entry_price"),
            "exit_px":   rec.get("exit_price"),
            "hold_min":  rec.get("hold_min"),
            "gross_pnl": rec.get("gross_pnl_pct"),
            "fee_pct":   rec.get("fee_pct"),
            "net_pnl":   rec.get("net_pnl_pct"),
            "vol_ratio": entry.get("vol_ratio"),
        })
    out.sort(key=lambda x: x["dt"], reverse=True)
    return out


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
    pump_trades    = _load_pump_trades()
    main_ws_trades = _load_main_ws_trades()

    wb = openpyxl.Workbook()
    _build_sheet1(wb, rows)
    _build_sheet2(wb, rows)
    _build_sheet3(wb, real_trades, signals)
    _build_sheet4(wb, pump_trades)
    _build_sheet5(wb, main_ws_trades)
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


# ---------------------------------------------------------------------------
# Sheet 4 — Памп (pump paper trades)
# ---------------------------------------------------------------------------

def _build_sheet4(wb, pump_trades: list) -> None:
    ws = wb.create_sheet("Памп")

    headers = ["Дата", "Пара", "Тир", "Исход", "Вход", "Выход",
               "Hold(мин)", "P&L% брутто", "Комиссия%", "NET P&L%", "Vol ratio"]

    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = FILL_HEADER
        cell.font = FONT_HEADER
        cell.alignment = Alignment(horizontal="center")

    for r, row in enumerate(pump_trades, 2):
        vals = [
            row["dt"], row["sym"], row["tier"], row["outcome"],
            row["entry_px"], row["exit_px"], row["hold_min"],
            row["gross_pnl"], row["fee_pct"], row["net_pnl"],
            round(row["vol_ratio"], 2) if row["vol_ratio"] else "",
        ]
        for col, val in enumerate(vals, 1):
            cell = ws.cell(row=r, column=col, value=val)
            if col == 4:
                if row["outcome"] == "TP":
                    cell.fill = FILL_TP
                elif row["outcome"] == "SL":
                    cell.fill = FILL_STOP

    if pump_trades:
        n_tp  = sum(1 for t in pump_trades if t["outcome"] == "TP")
        n     = len(pump_trades)
        wr    = n_tp / n * 100
        wins  = [t["net_pnl"] for t in pump_trades if t["outcome"] == "TP"  and t["net_pnl"] is not None]
        losses = [abs(t["net_pnl"]) for t in pump_trades if t["outcome"] == "SL" and t["net_pnl"] is not None]
        pf    = sum(wins) / sum(losses) if losses else float("inf")
        avg   = sum(t["net_pnl"] for t in pump_trades if t["net_pnl"] is not None) / n

        sum_row = n + 3
        for i, (label, val) in enumerate([
            ("Всего сделок", n),
            ("WR",           f"{n_tp}/{n} = {wr:.0f}%"),
            ("Profit Factor", f"{pf:.2f}"),
            ("Avg NET P&L%", f"{avg:+.3f}%"),
        ]):
            ws.cell(row=sum_row + i, column=1, value=label).fill = FILL_SUMMARY
            ws.cell(row=sum_row + i, column=1).font = FONT_BOLD
            ws.cell(row=sum_row + i, column=2, value=val).fill = FILL_SUMMARY

    _set_col_widths(ws, [16, 12, 6, 8, 12, 12, 9, 11, 10, 10, 10])
    ws.freeze_panes = "A2"
    print(f"Памп: {len(pump_trades)} сделок")


# ---------------------------------------------------------------------------
# Sheet 5 — Main WS (ws_main_screener signals)
# ---------------------------------------------------------------------------

def _load_main_ws_trades() -> list:
    signals = {
        (s.get("id") or s.get("signal_id", "")): s
        for s in _load_jsonl(MAIN_SIGNALS_LOG)
    }
    labels = {
        r["signal_id"]: r
        for r in _load_jsonl(MAIN_SIGNALS_LABELS)
    }
    out = []
    for sig_id, sig in signals.items():
        lab = labels.get(sig_id, {})
        ts = sig.get("ts", "")
        out.append({
            "dt":        ts[5:16].replace("T", " ") if ts else "",
            "symbol":    sig.get("symbol", ""),
            "regime":    sig.get("regime", ""),
            "style":     sig.get("trade_style", ""),
            "side":      "LONG" if sig.get("side") == "buy" else "SHORT",
            "entry":     sig.get("entry"),
            "sl":        sig.get("sl"),
            "tp1":       sig.get("tp1"),
            "tp2":       sig.get("tp2"),
            "hold_min":  sig.get("hold_min"),
            "outcome":   lab.get("outcome", ""),
            "exit_price": lab.get("exit_price"),
            "hold_actual": lab.get("hold_min"),
            "tp2_hit":   lab.get("tp2_hit", False),
            "vol_ratio": sig.get("vol_ratio"),
            "adx_1h":    sig.get("adx_1h"),
            "slope_15m": sig.get("slope_15m"),
            "fvg":       sig.get("fvg_confirmed", False),
        })
    out.sort(key=lambda x: x["dt"], reverse=True)
    return out


def _build_sheet5(wb, rows: list) -> None:
    ws = wb.create_sheet("Main WS")

    headers = [
        "Дата", "Пара", "Режим", "Стиль", "Сторона",
        "Вход", "SL", "TP1", "TP2", "Hold(мин)",
        "Исход", "Выход", "Hold факт.", "TP2?",
        "Vol ratio", "ADX_1H", "Slope_15m", "FVG",
    ]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = FILL_HEADER
        cell.font = FONT_HEADER
        cell.alignment = Alignment(horizontal="center")

    outcome_fill = {
        "TP1": FILL_TP, "TP2": FILL_TP,
        "SL":  FILL_STOP,
        "TIME": FILL_TIME,
    }

    for r, row in enumerate(rows, 2):
        vals = [
            row["dt"], row["symbol"], row["regime"], row["style"], row["side"],
            row["entry"], row["sl"], row["tp1"], row["tp2"], row["hold_min"],
            row["outcome"], row["exit_price"], row["hold_actual"],
            "да" if row["tp2_hit"] else "",
            round(row["vol_ratio"], 2) if row["vol_ratio"] else "",
            round(row["adx_1h"], 1) if row["adx_1h"] else "",
            round(row["slope_15m"], 1) if row["slope_15m"] is not None else "",
            "да" if row["fvg"] else "",
        ]
        for col, val in enumerate(vals, 1):
            cell = ws.cell(row=r, column=col, value=val)
            if col == 11 and row["outcome"] in outcome_fill:
                cell.fill = outcome_fill[row["outcome"]]

    # Summary по режимам
    labeled = [r for r in rows if r["outcome"]]
    n_total  = len(rows)
    n_labeled = len(labeled)
    n_tp  = sum(1 for r in labeled if r["outcome"] in ("TP1", "TP2"))
    n_sl  = sum(1 for r in labeled if r["outcome"] == "SL")
    n_time = sum(1 for r in labeled if r["outcome"] == "TIME")
    wr_str = f"{n_tp}/{n_tp+n_sl} = {n_tp/(n_tp+n_sl)*100:.0f}%" if (n_tp + n_sl) else "—"

    sum_row = len(rows) + 3
    summary = [
        ("Всего сигналов", n_total),
        ("Размечено",      n_labeled),
        ("TP1+TP2",        n_tp),
        ("SL",             n_sl),
        ("TIME",           n_time),
        ("WR (TP vs SL)",  wr_str),
    ]
    for i, (label, val) in enumerate(summary):
        ws.cell(row=sum_row + i, column=1, value=label).fill = FILL_SUMMARY
        ws.cell(row=sum_row + i, column=1).font = FONT_BOLD
        ws.cell(row=sum_row + i, column=2, value=val).fill = FILL_SUMMARY

    # WR по режимам
    from collections import defaultdict
    by_regime: dict = defaultdict(lambda: {"tp": 0, "sl": 0, "time": 0})
    for r in labeled:
        reg = r["regime"] or "?"
        if r["outcome"] in ("TP1", "TP2"):
            by_regime[reg]["tp"] += 1
        elif r["outcome"] == "SL":
            by_regime[reg]["sl"] += 1
        else:
            by_regime[reg]["time"] += 1

    ws.cell(row=sum_row, column=4, value="Режим").fill = FILL_SUMMARY
    ws.cell(row=sum_row, column=4).font = FONT_BOLD
    ws.cell(row=sum_row, column=5, value="TP").fill = FILL_SUMMARY
    ws.cell(row=sum_row, column=6, value="SL").fill = FILL_SUMMARY
    ws.cell(row=sum_row, column=7, value="TIME").fill = FILL_SUMMARY
    ws.cell(row=sum_row, column=8, value="WR%").fill = FILL_SUMMARY
    for j, (reg, counts) in enumerate(sorted(by_regime.items()), 1):
        tp, sl = counts["tp"], counts["sl"]
        wr = f"{tp/(tp+sl)*100:.0f}%" if (tp + sl) else "—"
        ws.cell(row=sum_row + j, column=4, value=reg)
        ws.cell(row=sum_row + j, column=5, value=tp)
        ws.cell(row=sum_row + j, column=6, value=sl)
        ws.cell(row=sum_row + j, column=7, value=counts["time"])
        ws.cell(row=sum_row + j, column=8, value=wr)

    _set_col_widths(ws, [14, 14, 10, 7, 7, 10, 10, 10, 10, 9, 7, 10, 10, 5, 9, 8, 10, 5])
    ws.freeze_panes = "A2"
    print(f"Main WS: {n_total} сигналов, {n_labeled} размечено, WR={wr_str}")


if __name__ == "__main__":
    build()
