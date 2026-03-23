"""Check actual outcomes for all ENTRY/WAIT signals against OKX candles."""
import json, os, glob, sys, asyncio, datetime
sys.stdout.reconfigure(encoding="utf-8")

ROOT = "C:/Users/krivo/trading-bot-v2"
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

import os
from src.exchange.okx_client import OKXClient

client = OKXClient(
    api_key=os.getenv("OKX_API_KEY", ""),
    secret_key=os.getenv("OKX_SECRET_KEY", ""),
    passphrase=os.getenv("OKX_PASSPHRASE", ""),
)


def load_signals():
    rows = []
    root = f"{ROOT}/logs/users"
    for uid in os.listdir(root):
        an = os.path.join(root, uid, "analyses")
        if not os.path.isdir(an): continue
        for folder in os.listdir(an):
            g = glob.glob(os.path.join(an, folder, "*_snapshot.json"))
            if not g: continue
            d = json.load(open(g[0], encoding="utf-8"))
            ctx = d.get("llm_context", {})
            sig = ctx.get("entry_signal", "?")
            if sig not in ("ENTRY", "WAIT"): continue

            sl    = ctx.get("sl_price")
            tp1   = ctx.get("tp1_price")
            tp2   = ctx.get("tp2_price")
            side  = ctx.get("side")          # buy/sell — may be None in old snaps

            # Infer side from SL position: SHORT → sl > close, LONG → sl < close
            # Use close from snapshot 1h data
            close_1h = d.get("1h", {}).get("close") or d.get("15m", {}).get("close")
            if not side and sl and close_1h:
                side = "sell" if float(sl) > float(close_1h) else "buy"

            entry = float(close_1h) if close_1h else None

            date = folder[:10]
            tm   = folder[11:16].replace("-", ":")
            sym  = "-".join(folder.split("_")[2:])
            cap  = d.get("captured_at", f"{date}T{tm.replace(':','-')}:00Z")

            rows.append({
                "uid": uid, "date": date, "time": tm,
                "sym": sym, "sig": sig, "side": side,
                "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2,
                "captured_at": cap,
            })
    rows.sort(key=lambda r: (r["date"], r["time"]))
    return rows


async def get_candles_after(sym, captured_at_iso, limit=200):
    """Fetch 15m candles that opened AFTER captured_at_iso."""
    dt  = datetime.datetime.fromisoformat(captured_at_iso.replace("Z", "+00:00"))
    ts  = int(dt.timestamp() * 1000)
    # before=ts  → returns candles NEWER than ts (OKX pagination)
    candles = await client.get_history_candles(sym, bar="15m", limit=limit, before=ts)
    # OKX returns newest first — reverse for chronological order
    return list(reversed(candles))


def check_outcome(candles, side, sl, tp1, tp2):
    if not candles or not sl or not tp1 or not side:
        return "NO_DATA"
    sl, tp1 = float(sl), float(tp1)
    tp2 = float(tp2) if tp2 else None
    for c in candles:
        try:
            high = float(c[2])
            low  = float(c[3])
        except:
            continue
        if side == "buy":
            if low  <= sl:  return "SL"
            if high >= tp1:
                if tp2 and high >= tp2: return "TP2"
                return "TP1"
        else:  # sell / short
            if high >= sl:  return "SL"
            if low  <= tp1:
                if tp2 and low <= tp2: return "TP2"
                return "TP1"
    return "OPEN"


async def main():
    signals = load_signals()
    wins = losses = open_ = 0

    print(f"\n{'USER':<14} {'DATE':<11} {'TIME':<6} {'PAIR':<12} {'SIG':<7} {'DIR':<6} {'ENTRY':>10} {'SL':>10} {'TP1':>10}  OUTCOME")
    print("-" * 105)

    for r in signals:
        try:
            candles = await get_candles_after(r["sym"], r["captured_at"])
            await asyncio.sleep(0.15)
        except Exception as e:
            candles = []

        outcome = check_outcome(candles, r["side"], r["sl"], r["tp1"], r["tp2"])

        side_str  = "SHORT" if r["side"] == "sell" else ("LONG" if r["side"] == "buy" else "?")
        entry_s   = f"{r['entry']:.4f}" if r["entry"] else "-"
        sl_s      = f"{r['sl']:.4f}"    if r["sl"]    else "-"
        tp1_s     = f"{r['tp1']:.4f}"   if r["tp1"]   else "-"
        emoji     = {"TP1": "✅", "TP2": "✅✅", "SL": "❌", "OPEN": "⏳", "NO_DATA": "❓"}.get(outcome, "?")

        if outcome in ("TP1", "TP2"): wins   += 1
        elif outcome == "SL":         losses += 1
        elif outcome == "OPEN":       open_  += 1

        print(f"{r['uid']:<14} {r['date']:<11} {r['time']:<6} {r['sym']:<12} {r['sig']:<7} {side_str:<6} {entry_s:>10} {sl_s:>10} {tp1_s:>10}  {emoji} {outcome}")

    total = wins + losses
    wr = round(wins / total * 100) if total else 0
    print()
    print(f"  ИТОГО: ✅ {wins} побед  ❌ {losses} стопов  ⏳ {open_} открыты  |  Winrate: {wr}%  (из {total} закрытых)")

asyncio.run(main())
