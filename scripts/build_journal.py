"""
build_journal.py — trading journal Excel (6 sheets).

Sheets: Скринер | Симулятор | Main WS | Памп | Ручные | Дашборд

Run via update_journal.bat after labelers.
"""
import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

try:
    import openpyxl
    from openpyxl.styles import PatternFill, Font, Alignment
    from openpyxl.utils import get_column_letter
except ImportError:
    print("openpyxl не установлен: pip install openpyxl")
    sys.exit(1)

from src.exchange.okx_client import OKXClient

_ROOT               = Path(__file__).resolve().parent.parent
SIGNAL_LOG          = _ROOT / "logs" / "signals" / "signal_log.jsonl"
SIGNAL_LABELS       = _ROOT / "logs" / "signals" / "signal_labels.jsonl"
PUMP_SIGNALS_LOG    = _ROOT / "logs" / "pump" / "pump_signals.jsonl"
PUMP_LABELS_LOG     = _ROOT / "logs" / "pump" / "pump_labels.jsonl"
MAIN_SIGNALS_LOG    = _ROOT / "logs" / "signals" / "main_signals.jsonl"
MAIN_SIGNALS_LABELS = _ROOT / "logs" / "signals" / "main_signals_labels.jsonl"
JOURNAL_PATH        = Path(__file__).parent / "journal.xlsx"

# ── Palette ──────────────────────────────────────────────────────────────────
def _fill(hex6: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex6)

F_HDR    = _fill("1F4E79")   # dark blue   — data sheet headers
F_INPUT  = _fill("FFF2CC")   # yellow      — user-editable cells
F_TP     = _fill("C6EFCE")   # green
F_SL     = _fill("FFC7CE")   # red
F_TIME   = _fill("FFEB9C")   # amber
F_SUM    = _fill("D9E1F2")   # light blue  — summary rows
F_DASH_T = _fill("2E4057")   # dark navy   — dashboard section titles
F_DASH_A = _fill("4472C4")   # blue        — dashboard column headers
F_STRIPE = _fill("F2F2F2")   # light grey  — alternating rows

FONT_HDR   = Font(color="FFFFFF", bold=True, size=10)
FONT_BOLD  = Font(bold=True)
FONT_DTIT  = Font(color="FFFFFF", bold=True, size=11)
FONT_DHDR  = Font(color="FFFFFF", bold=True, size=10)
ALIGN_C    = Alignment(horizontal="center")

OUTCOME_FILL = {
    "TP": F_TP, "TP1": F_TP, "TP2": F_TP,
    "SL": F_SL, "STOP": F_SL,
    "TIME": F_TIME, "TIME_EXIT": F_TIME,
}


# ── Generic helpers ───────────────────────────────────────────────────────────

def _load_jsonl(path: Path) -> list:
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


def _fmt_dt(ts_ms) -> str:
    if not ts_ms:
        return ""
    return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).strftime("%d.%m %H:%M")


def _set_widths(ws, widths: list) -> None:
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = w


def _hdr(ws, row: int, cols: list, fill=None, font=None) -> None:
    for c, h in enumerate(cols, 1):
        cell = ws.cell(row=row, column=c, value=h)
        cell.fill  = fill or F_HDR
        cell.font  = font or FONT_HDR
        cell.alignment = ALIGN_C


def _dash_title(ws, row: int, col: int, text: str, span: int) -> None:
    cell = ws.cell(row=row, column=col, value=text)
    cell.fill = F_DASH_T
    cell.font = FONT_DTIT
    cell.alignment = ALIGN_C
    if span > 1:
        ws.merge_cells(
            start_row=row, start_column=col,
            end_row=row,   end_column=col + span - 1,
        )


def _dash_hdr(ws, row: int, col_start: int, cols: list) -> None:
    for i, h in enumerate(cols):
        cell = ws.cell(row=row, column=col_start + i, value=h)
        cell.fill = F_DASH_A
        cell.font = FONT_DHDR
        cell.alignment = ALIGN_C


def _dw(ws, row: int, col: int, value, fill=None, font=None) -> None:
    cell = ws.cell(row=row, column=col, value=value)
    if fill: cell.fill = fill
    if font: cell.font = font


# ── Data loaders ─────────────────────────────────────────────────────────────

def _load_screener() -> list:
    labels = {l["signal_id"]: l for l in _load_jsonl(SIGNAL_LABELS)}
    out = []
    for sig in _load_jsonl(SIGNAL_LOG):
        lab   = labels.get(sig.get("signal_id", ""), {})
        style = sig.get("style") or ("FADE" if sig.get("source") == "bb_fade" else "FAST")
        out.append({
            "dt":       _fmt_dt(sig.get("ts_ms")),
            "pair":     sig.get("symbol", ""),
            "style":    style,
            "regime":   sig.get("regime", ""),
            "side":     "LONG" if sig.get("side") == "buy" else "SHORT",
            "entry":    sig.get("close"),
            "sl":       sig.get("sl"),
            "tp":       sig.get("tp"),
            "hold_min": sig.get("max_hold_min"),
            "outcome":  lab.get("outcome", ""),
            "R":        lab.get("exit_r"),
            "MFE_R":    lab.get("mfe_r"),
            "MAE_R":    lab.get("mae_r"),
            "adx_1h":   sig.get("adx_1h"),
            "slope_1h": sig.get("slope_1h"),
            "slope_15m":sig.get("slope_15m"),
            "funding":  sig.get("funding") or 0.0,
        })
    return out


def _load_main_ws() -> list:
    signals = {
        (s.get("id") or s.get("signal_id", "")): s
        for s in _load_jsonl(MAIN_SIGNALS_LOG)
    }
    labels = {r["signal_id"]: r for r in _load_jsonl(MAIN_SIGNALS_LABELS)}
    out = []
    for sig_id, sig in signals.items():
        lab = labels.get(sig_id, {})
        ts  = sig.get("ts", "")
        out.append({
            "dt":         ts[5:16].replace("T", " ") if ts else "",
            "pair":       sig.get("symbol", ""),
            "regime":     sig.get("regime", ""),
            "side":       "LONG" if sig.get("side") == "buy" else "SHORT",
            "entry":      sig.get("entry"),
            "sl":         sig.get("sl"),
            "tp1":        sig.get("tp1"),
            "tp2":        sig.get("tp2"),
            "hold_min":   sig.get("hold_min"),
            "outcome":    lab.get("outcome", ""),
            "exit_price": lab.get("exit_price"),
        })
    out.sort(key=lambda x: x["dt"], reverse=True)
    return out


def _load_pump() -> list:
    entries = {
        r["signal_id"]: r
        for r in _load_jsonl(PUMP_SIGNALS_LOG)
        if r.get("type") == "ENTRY"
    }
    out = []
    for rec in _load_jsonl(PUMP_LABELS_LOG):
        if rec.get("type") != "EXIT":
            continue
        entry  = entries.get(rec.get("signal_id", ""), {})
        opened = rec.get("opened_at", "")
        sl_raw = entry.get("paper_sl")
        tp_raw = entry.get("paper_tp")
        out.append({
            "dt":      opened[:16].replace("T", " ") if opened else "",
            "pair":    rec.get("sym", "").replace("-USDT-SWAP", "").replace("-SWAP", ""),
            "entry":   rec.get("entry_price"),
            "sl":      float(sl_raw) if sl_raw else None,
            "tp":      float(tp_raw) if tp_raw else None,
            "outcome": rec.get("exit_reason", ""),
            "hold":    rec.get("hold_min"),
            "hour":    entry.get("hour_utc"),
            "net_pnl": rec.get("net_pnl_pct"),
        })
    out.sort(key=lambda x: x["dt"], reverse=True)
    return out


async def _fetch_positions_async() -> list:
    api_key    = os.getenv("OKX_API_KEY", "")
    secret_key = os.getenv("OKX_SECRET_KEY", "")
    passphrase = os.getenv("OKX_PASSPHRASE", "")
    is_demo    = os.getenv("OKX_IS_DEMO", "1") == "1"
    if not api_key:
        return []
    client = OKXClient(api_key, secret_key, passphrase, is_demo=is_demo)
    try:
        inst_ids: set = set()
        after = None
        for _ in range(20):
            params: dict = {"instType": "SWAP", "limit": "100"}
            if after:
                params["after"] = after
            data  = await client._get("/api/v5/trade/fills-history", params)
            batch = data.get("data") or []
            for f in batch:
                iid = f.get("instId", "")
                if iid.endswith("-SWAP"):
                    inst_ids.add(iid)
            if len(batch) < 100:
                break
            after = batch[-1]["tradeId"]

        positions: list = []
        for iid in sorted(inst_ids):
            data = await client._get(
                "/api/v5/account/positions-history",
                {"instId": iid, "limit": "100"},
            )
            positions.extend(data.get("data") or [])
        return positions
    finally:
        await client.close()


def _load_real() -> list:
    try:
        positions = asyncio.run(_fetch_positions_async())
    except Exception as e:
        print(f"Ошибка загрузки реальных сделок: {e}")
        positions = []

    out = []
    for pos in positions:
        open_ms  = int(pos.get("cTime") or 0)
        close_ms = int(pos.get("uTime") or 0)
        symbol   = pos.get("instId", "").replace("-USDT-SWAP", "").replace("-SWAP", "")
        realized = float(pos.get("realizedPnl") or 0)
        funding  = float(pos.get("fundingFee")  or 0)
        net_pnl  = realized + funding
        pnl_pct  = float(pos.get("pnlRatio") or 0) * 100
        lever    = int(float(pos.get("lever") or 1))
        hold_min = round((close_ms - open_ms) / 60_000) if close_ms > open_ms else 0
        out.append({
            "dt":       _fmt_dt(close_ms),
            "pair":     symbol,
            "side":     (pos.get("direction") or "long").upper(),
            "entry":    float(pos.get("openAvgPx")  or 0) or None,
            "exit":     float(pos.get("closeAvgPx") or 0) or None,
            "lever":    lever,
            "net_pnl":  round(net_pnl, 4),
            "pnl_pct":  round(pnl_pct, 2),
            "hold_min": hold_min,
            "open_ms":  open_ms,
        })
    out.sort(key=lambda x: x["open_ms"], reverse=True)
    n_win = sum(1 for r in out if r["net_pnl"] > 0)
    print(f"Ручные: {len(out)} позиций, {n_win} прибыльных, avg win {sum(r['hold_min'] for r in out if r['net_pnl']>0)/n_win:.0f} мин" if n_win else f"Ручные: {len(out)} позиций")
    return out


# ── Sheet 1: Скринер ─────────────────────────────────────────────────────────
# A=date B=pair C=style D=regime E=side F=entry G=sl H=tp I=hold_min
# J=outcome K=R L=MFE_R M=MAE_R N=ADX_1H O=Slope_1H P=Slope_15m

def _build_screener(wb, rows: list) -> None:
    ws = wb.active
    ws.title = "Скринер"
    _hdr(ws, 1, [
        "Дата", "Пара", "Стиль", "Режим", "Сторона",
        "Вход", "SL", "TP", "Hold(мин)", "Исход",
        "R", "MFE_R", "MAE_R", "ADX_1H", "Slope_1H°", "Slope_15m°",
    ])
    for r, row in enumerate(rows, 2):
        vals = [
            row["dt"], row["pair"], row["style"], row["regime"], row["side"],
            row["entry"], row["sl"], row["tp"], row["hold_min"], row["outcome"],
            row["R"], row["MFE_R"], row["MAE_R"],
            round(row["adx_1h"], 1)   if row["adx_1h"]   is not None else "",
            round(row["slope_1h"], 1) if row["slope_1h"]  is not None else "",
            round(row["slope_15m"], 1) if row["slope_15m"] is not None else "",
        ]
        for c, val in enumerate(vals, 1):
            cell = ws.cell(row=r, column=c, value=val)
            if c == 10 and row["outcome"] in OUTCOME_FILL:
                cell.fill = OUTCOME_FILL[row["outcome"]]
    _set_widths(ws, [13, 15, 7, 10, 8, 10, 10, 10, 9, 11, 7, 7, 7, 8, 10, 10])
    ws.freeze_panes = "A2"


# ── Sheet 2: Симулятор ───────────────────────────────────────────────────────
# A=Капитал$ B=Плечо C=date D=pair E=style F=side
# G=entry H=sl I=tp J=outcome K=R L=MAE_R
# M=Позиция$ N=Цена ликв O=Реал.исход P=P&L$ Q=P&L% R=Комис$ S=Финанс$ T=Чист P&L$

def _build_simulator(wb, rows: list) -> None:
    ws = wb.create_sheet("Симулятор")
    _hdr(ws, 1, [
        "Капитал$", "Плечо",
        "Дата", "Пара", "Стиль", "Сторона",
        "Вход", "SL", "TP", "Исход", "R", "MAE_R",
        "Позиция$", "Цена ликв.", "Реал. исход",
        "P&L$", "P&L%", "Комиссия$", "Финанс.$", "Чист. P&L$",
    ])
    # Highlight input column headers
    for c in (1, 2):
        ws.cell(row=1, column=c).fill = _fill("F4B942")

    for r, row in enumerate(rows, 2):
        ws.cell(row=r, column=1, value=100).fill = F_INPUT
        ws.cell(row=r, column=2, value=10).fill  = F_INPUT

        data = {
            3: row["dt"],     4: row["pair"],    5: row["style"],
            6: row["side"],   7: row["entry"],   8: row["sl"],
            9: row["tp"],     10: row["outcome"] or "",
            11: row["R"],     12: row["MAE_R"],
        }
        for c, val in data.items():
            cell = ws.cell(row=r, column=c, value=val)
            if c == 10 and row["outcome"] in OUTCOME_FILL:
                cell.fill = OUTCOME_FILL[row["outcome"]]

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
        ws.cell(row=r, column=18, value=(
            f'=IF(M{r}="","",-M{r}*0.001)'
        ))
        ws.cell(row=r, column=19, value=round((row["funding"] or 0) * 100, 6))
        ws.cell(row=r, column=20, value=(
            f'=IF(P{r}="","",P{r}+R{r}+S{r})'
        ))

    _set_widths(ws, [9, 6, 13, 15, 7, 8, 10, 10, 10, 10, 7, 7, 10, 12, 13, 9, 7, 10, 9, 12])
    ws.freeze_panes = "C2"


# ── Sheet 3: Main WS ─────────────────────────────────────────────────────────
# A=date B=pair C=regime D=side E=entry F=sl G=tp1 H=tp2 I=hold_min
# J=outcome K=exit_price

def _build_main_ws(wb, rows: list) -> None:
    ws = wb.create_sheet("Main WS")
    _hdr(ws, 1, [
        "Дата", "Пара", "Режим", "Сторона",
        "Вход", "SL", "TP1", "TP2", "Hold(мин)",
        "Исход", "Выход",
    ])
    for r, row in enumerate(rows, 2):
        vals = [
            row["dt"], row["pair"], row["regime"], row["side"],
            row["entry"], row["sl"], row["tp1"], row["tp2"], row["hold_min"],
            row["outcome"], row["exit_price"],
        ]
        for c, val in enumerate(vals, 1):
            cell = ws.cell(row=r, column=c, value=val)
            if c == 10 and row["outcome"] in OUTCOME_FILL:
                cell.fill = OUTCOME_FILL[row["outcome"]]

    labeled  = [r for r in rows if r["outcome"]]
    n_tp     = sum(1 for r in labeled if r["outcome"] in ("TP1", "TP2"))
    n_sl     = sum(1 for r in labeled if r["outcome"] == "SL")
    n_time   = sum(1 for r in labeled if r["outcome"] == "TIME")
    decisive = n_tp + n_sl
    wr_str   = f"{n_tp}/{decisive} = {n_tp/decisive*100:.0f}%" if decisive else "—"

    sr = len(rows) + 3
    for i, (lbl, val) in enumerate([
        ("Всего сигналов", len(rows)),
        ("Лейблировано",   len(labeled)),
        ("WR (vs SL)",     wr_str),
        ("TP1+TP2",        n_tp),
        ("SL",             n_sl),
        ("TIME",           n_time),
    ]):
        ws.cell(row=sr + i, column=1, value=lbl).fill = F_SUM
        ws.cell(row=sr + i, column=1).font = FONT_BOLD
        ws.cell(row=sr + i, column=2, value=val).fill = F_SUM

    _set_widths(ws, [13, 16, 10, 8, 10, 10, 10, 10, 9, 8, 10])
    ws.freeze_panes = "A2"
    print(f"Main WS: {len(rows)} сигналов, {len(labeled)} лейблировано, WR={wr_str}")


# ── Sheet 4: Памп ────────────────────────────────────────────────────────────
# A=date B=pair C=entry D=sl E=tp F=outcome G=hold_min H=signal_hour I=net_pnl_pct

def _build_pump(wb, rows: list) -> None:
    ws = wb.create_sheet("Памп")
    _hdr(ws, 1, [
        "Дата", "Пара", "Вход", "SL", "TP",
        "Исход", "Hold(мин)", "Час UTC", "NET P&L%",
    ])
    for r, row in enumerate(rows, 2):
        vals = [
            row["dt"], row["pair"], row["entry"], row["sl"], row["tp"],
            row["outcome"], row["hold"], row["hour"], row["net_pnl"],
        ]
        for c, val in enumerate(vals, 1):
            cell = ws.cell(row=r, column=c, value=val)
            if c == 6 and row["outcome"] in OUTCOME_FILL:
                cell.fill = OUTCOME_FILL[row["outcome"]]

    n = len(rows)
    if n:
        n_tp   = sum(1 for r in rows if r["outcome"] == "TP")
        n_sl   = n - n_tp
        wr     = n_tp / n * 100
        wins   = [r["net_pnl"] for r in rows if r["outcome"] == "TP"  and r["net_pnl"] is not None]
        losses = [abs(r["net_pnl"]) for r in rows if r["outcome"] == "SL" and r["net_pnl"] is not None]
        pf     = sum(wins) / sum(losses) if losses else 999.0
        avg    = sum(r["net_pnl"] for r in rows if r["net_pnl"] is not None) / n

        sr = n + 3
        for i, (lbl, val) in enumerate([
            ("Всего сделок",  n),
            ("WR",            f"{n_tp}/{n} = {wr:.0f}%"),
            ("Profit Factor", f"{pf:.2f}"),
            ("Avg NET P&L%",  f"{avg:+.3f}%"),
        ]):
            ws.cell(row=sr + i, column=1, value=lbl).fill = F_SUM
            ws.cell(row=sr + i, column=1).font = FONT_BOLD
            ws.cell(row=sr + i, column=2, value=val).fill = F_SUM

    _set_widths(ws, [13, 12, 11, 11, 11, 8, 9, 8, 10])
    ws.freeze_panes = "A2"
    print(f"Памп: {n} сделок")


# ── Sheet 5: Ручные ──────────────────────────────────────────────────────────
# A=date B=pair C=side D=entry E=exit F=lever G=net_pnl$ H=pnl_pct% I=hold_min

def _build_real(wb, rows: list) -> None:
    ws = wb.create_sheet("Ручные")
    _hdr(ws, 1, [
        "Дата закр.", "Пара", "Сторона",
        "Вход", "Выход", "Плечо",
        "NET P&L$", "P&L%", "Hold(мин)",
    ])
    for r, row in enumerate(rows, 2):
        vals = [
            row["dt"], row["pair"], row["side"],
            row["entry"], row["exit"], row["lever"],
            row["net_pnl"], row["pnl_pct"], row["hold_min"],
        ]
        for c, val in enumerate(vals, 1):
            cell = ws.cell(row=r, column=c, value=val)
            if c == 7:
                cell.fill = F_TP if (row["net_pnl"] or 0) >= 0 else F_SL

    if not rows:
        ws.cell(row=2, column=1, value="Нет данных (OKX не вернул позиции)")

    _set_widths(ws, [13, 12, 8, 11, 11, 7, 11, 8, 10])
    ws.freeze_panes = "A2"


# ── Sheet 6: Дашборд ─────────────────────────────────────────────────────────

def _build_dashboard(wb) -> None:
    ws = wb.create_sheet("Дашборд")
    ws.sheet_view.showGridLines = False

    # ── Блок A: Итоги по системам (A1:F7) ────────────────────────────────────
    _dash_title(ws, 1, 1, "ИТОГИ ПО СИСТЕМАМ", span=6)
    _dash_hdr(ws, 2, 1, ["Система", "TP", "SL", "TIME", "Всего", "WR%"])

    systems = [
        ("Скринер",
         '=COUNTIF(Скринер!J2:J10000,"TP")',
         '=COUNTIF(Скринер!J2:J10000,"SL")',
         '=COUNTIF(Скринер!J2:J10000,"TIME_EXIT")',
         ),
        ("Main WS",
         '=COUNTIF(\'Main WS\'!J2:J10000,"TP1")+COUNTIF(\'Main WS\'!J2:J10000,"TP2")',
         '=COUNTIF(\'Main WS\'!J2:J10000,"SL")',
         '=COUNTIF(\'Main WS\'!J2:J10000,"TIME")',
         ),
        ("Памп",
         '=COUNTIF(Памп!F2:F10000,"TP")',
         '=COUNTIF(Памп!F2:F10000,"SL")',
         "0",
         ),
        ("Ручные",
         '=COUNTIF(Ручные!G2:G10000,">0")',
         '=COUNTIF(Ручные!G2:G10000,"<0")',
         "0",
         ),
    ]
    for i, (name, tp_f, sl_f, t_f) in enumerate(systems):
        r    = 3 + i
        fill = F_STRIPE if i % 2 == 0 else None
        _dw(ws, r, 1, name, fill=fill, font=FONT_BOLD)
        _dw(ws, r, 2, tp_f,  fill=fill)
        _dw(ws, r, 3, sl_f,  fill=fill)
        _dw(ws, r, 4, t_f,   fill=fill)
        _dw(ws, r, 5, f"=B{r}+C{r}+D{r}", fill=fill)
        _dw(ws, r, 6, f'=IF(E{r}=0,"",ROUND(B{r}/E{r}*100,1))', fill=fill)

    # ── Блок B: Скринер по стилю (A9:F13) ────────────────────────────────────
    _dash_title(ws, 9, 1, "СКРИНЕР — ПО СТИЛЮ", span=6)
    _dash_hdr(ws, 10, 1, ["Стиль", "TP", "SL", "TIME", "Всего", "WR%"])
    for i, style in enumerate(["FAST", "SWING", "FADE"]):
        r    = 11 + i
        fill = F_STRIPE if i % 2 == 0 else None
        _dw(ws, r, 1, style, fill=fill, font=FONT_BOLD)
        _dw(ws, r, 2, f'=COUNTIFS(Скринер!C2:C10000,A{r},Скринер!J2:J10000,"TP")', fill=fill)
        _dw(ws, r, 3, f'=COUNTIFS(Скринер!C2:C10000,A{r},Скринер!J2:J10000,"SL")', fill=fill)
        _dw(ws, r, 4, f'=COUNTIFS(Скринер!C2:C10000,A{r},Скринер!J2:J10000,"TIME_EXIT")', fill=fill)
        _dw(ws, r, 5, f"=B{r}+C{r}+D{r}", fill=fill)
        _dw(ws, r, 6, f'=IF(E{r}=0,"",ROUND(B{r}/E{r}*100,1))', fill=fill)

    # ── Блок C: Скринер по режиму (A15:F19) ──────────────────────────────────
    _dash_title(ws, 15, 1, "СКРИНЕР — ПО РЕЖИМУ", span=6)
    _dash_hdr(ws, 16, 1, ["Режим", "TP", "SL", "TIME", "Всего", "WR%"])
    for i, regime in enumerate(["DRIFT", "TRENDING", "RANGING"]):
        r    = 17 + i
        fill = F_STRIPE if i % 2 == 0 else None
        _dw(ws, r, 1, regime, fill=fill, font=FONT_BOLD)
        _dw(ws, r, 2, f'=COUNTIFS(Скринер!D2:D10000,A{r},Скринер!J2:J10000,"TP")', fill=fill)
        _dw(ws, r, 3, f'=COUNTIFS(Скринер!D2:D10000,A{r},Скринер!J2:J10000,"SL")', fill=fill)
        _dw(ws, r, 4, f'=COUNTIFS(Скринер!D2:D10000,A{r},Скринер!J2:J10000,"TIME_EXIT")', fill=fill)
        _dw(ws, r, 5, f"=B{r}+C{r}+D{r}", fill=fill)
        _dw(ws, r, 6, f'=IF(E{r}=0,"",ROUND(B{r}/E{r}*100,1))', fill=fill)

    # ── Блок D: Main WS по режиму (H1:M6) ────────────────────────────────────
    _dash_title(ws, 1, 8, "MAIN WS — ПО РЕЖИМУ", span=6)
    _dash_hdr(ws, 2, 8, ["Режим", "TP1", "TP2", "SL", "TIME", "WR%"])
    for i, regime in enumerate(["DRIFT", "TRENDING", "RANGING"]):
        r    = 3 + i
        fill = F_STRIPE if i % 2 == 0 else None
        _dw(ws, r, 8,  regime, fill=fill, font=FONT_BOLD)
        _dw(ws, r, 9,  f'=COUNTIFS(\'Main WS\'!C2:C10000,H{r},\'Main WS\'!J2:J10000,"TP1")', fill=fill)
        _dw(ws, r, 10, f'=COUNTIFS(\'Main WS\'!C2:C10000,H{r},\'Main WS\'!J2:J10000,"TP2")', fill=fill)
        _dw(ws, r, 11, f'=COUNTIFS(\'Main WS\'!C2:C10000,H{r},\'Main WS\'!J2:J10000,"SL")', fill=fill)
        _dw(ws, r, 12, f'=COUNTIFS(\'Main WS\'!C2:C10000,H{r},\'Main WS\'!J2:J10000,"TIME")', fill=fill)
        _dw(ws, r, 13,
            f'=IF(I{r}+J{r}+K{r}+L{r}=0,"",'
            f'ROUND((I{r}+J{r})/(I{r}+J{r}+K{r}+L{r})*100,1))',
            fill=fill)

    # ── Блок E: P&L сводка (H9:M14) ──────────────────────────────────────────
    _dash_title(ws, 9, 8, "P&L СВОДКА", span=6)
    _dash_hdr(ws, 10, 8, ["Источник", "Побед", "Потерь", "Net", "Прибыль", "Убыток"])
    pnl_data = [
        ("Скринер (R)",
         '=COUNTIF(Скринер!K2:K10000,">0")',
         '=COUNTIF(Скринер!K2:K10000,"<0")',
         '=ROUND(SUM(Скринер!K2:K10000),2)',
         '=ROUND(SUMIF(Скринер!K2:K10000,">0",Скринер!K2:K10000),2)',
         '=ROUND(SUMIF(Скринер!K2:K10000,"<0",Скринер!K2:K10000),2)',
         ),
        ("Памп (%)",
         '=COUNTIF(Памп!I2:I10000,">0")',
         '=COUNTIF(Памп!I2:I10000,"<0")',
         '=ROUND(SUM(Памп!I2:I10000),2)',
         '=ROUND(SUMIF(Памп!I2:I10000,">0",Памп!I2:I10000),2)',
         '=ROUND(SUMIF(Памп!I2:I10000,"<0",Памп!I2:I10000),2)',
         ),
        ("Ручные ($)",
         '=COUNTIF(Ручные!G2:G10000,">0")',
         '=COUNTIF(Ручные!G2:G10000,"<0")',
         '=ROUND(SUM(Ручные!G2:G10000),2)',
         '=ROUND(SUMIF(Ручные!G2:G10000,">0",Ручные!G2:G10000),2)',
         '=ROUND(SUMIF(Ручные!G2:G10000,"<0",Ручные!G2:G10000),2)',
         ),
        ("Симулятор ($)",
         '=COUNTIF(Симулятор!T2:T10000,">0")',
         '=COUNTIF(Симулятор!T2:T10000,"<0")',
         '=ROUND(SUM(Симулятор!T2:T10000),2)',
         '=ROUND(SUMIF(Симулятор!T2:T10000,">0",Симулятор!T2:T10000),2)',
         '=ROUND(SUMIF(Симулятор!T2:T10000,"<0",Симулятор!T2:T10000),2)',
         ),
    ]
    for i, (src, wins, losses, net, profit, loss) in enumerate(pnl_data):
        r    = 11 + i
        fill = F_STRIPE if i % 2 == 0 else None
        _dw(ws, r, 8,  src,    fill=fill, font=FONT_BOLD)
        _dw(ws, r, 9,  wins,   fill=fill)
        _dw(ws, r, 10, losses, fill=fill)
        _dw(ws, r, 11, net,    fill=fill)
        _dw(ws, r, 12, profit, fill=_fill("C6EFCE"))
        _dw(ws, r, 13, loss,   fill=_fill("FFC7CE"))

    # ── Блок F: Памп по часам UTC (A21:F46) ──────────────────────────────────
    _dash_title(ws, 21, 1, "ПАМП — ПО ЧАСАМ UTC", span=6)
    _dash_hdr(ws, 22, 1, ["Час", "TP", "SL", "Всего", "WR%", "NET P&L% сумм"])
    for hour in range(24):
        r    = 23 + hour
        fill = F_STRIPE if hour % 2 == 0 else None
        _dw(ws, r, 1, hour, fill=fill)
        _dw(ws, r, 2, f'=COUNTIFS(Памп!H2:H10000,A{r},Памп!F2:F10000,"TP")', fill=fill)
        _dw(ws, r, 3, f'=COUNTIFS(Памп!H2:H10000,A{r},Памп!F2:F10000,"SL")', fill=fill)
        _dw(ws, r, 4, f"=B{r}+C{r}", fill=fill)
        _dw(ws, r, 5, f'=IF(D{r}=0,"",ROUND(B{r}/D{r}*100,1))', fill=fill)
        _dw(ws, r, 6, f"=ROUND(SUMIF(Памп!H2:H10000,A{r},Памп!I2:I10000),2)", fill=fill)

    # ── Блок G: Памп по режиму (H16:M20) — placeholder ───────────────────────
    # Pump has no regime data; reserved for future
    _dash_title(ws, 16, 8, "ПАМП — ДОПОЛНИТЕЛЬНО", span=6)
    ws.cell(row=17, column=8, value="Данные по режиму для памп-системы")
    ws.cell(row=18, column=8, value="появятся когда скринер начнёт")
    ws.cell(row=19, column=8, value="передавать режим в pump_signals")

    _set_widths(ws, [12, 9, 9, 9, 8, 15, 3, 16, 9, 9, 11, 12, 12])


# ── Entry point ───────────────────────────────────────────────────────────────

def build() -> None:
    screener = _load_screener()
    main_ws  = _load_main_ws()
    pump     = _load_pump()
    real     = _load_real()

    wb = openpyxl.Workbook()
    _build_screener(wb, screener)
    _build_simulator(wb, screener)
    _build_main_ws(wb, main_ws)
    _build_pump(wb, pump)
    _build_real(wb, real)
    _build_dashboard(wb)

    wb.save(JOURNAL_PATH)
    n_labeled = sum(1 for r in screener if r["outcome"])
    print(f"Журнал готов: {JOURNAL_PATH} | Скринер {len(screener)} ({n_labeled} закрыто) | Памп {len(pump)} | Main WS {len(main_ws)}")


if __name__ == "__main__":
    build()
