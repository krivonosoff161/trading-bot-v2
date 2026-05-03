"""Backtest all ENTRY signals against real candles."""
import asyncio
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
from src.exchange.okx_client import OKXClient

BASE = os.path.join(os.path.dirname(__file__), "analysis_output")


def get_entries():
    entries = []
    for root, dirs, files in os.walk(BASE):
        for f in files:
            if not f.endswith("_snapshot.json"):
                continue
            with open(os.path.join(root, f)) as fp:
                d = json.load(fp)
            ctx = d.get("llm_context", {})
            if ctx.get("entry_signal") != "ENTRY":
                continue
            h15 = d.get("15m", {})
            close = h15.get("close")
            sl = ctx.get("sl_price")
            tp1 = ctx.get("tp1_price")
            tp2 = ctx.get("tp2_price")
            if not all([close, sl, tp1]):
                continue
            side = "buy" if sl < close else "sell"
            ts_str = os.path.basename(root)
            try:
                dt = datetime.strptime(ts_str[:16], "%Y-%m-%d_%H-%M")
                ts_ms = int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
            except Exception:
                continue
            entries.append({
                "ts_str": ts_str,
                "pair": f.replace("_snapshot.json", ""),
                "style": ctx.get("trade_style_hint", ""),
                "side": side,
                "entry": close,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "ts_ms": ts_ms,
            })
    return sorted(entries, key=lambda x: x["ts_ms"])


async def backtest():
    client = OKXClient(
        api_key=os.getenv("OKX_API_KEY", ""),
        secret_key=os.getenv("OKX_SECRET_KEY", ""),
        passphrase=os.getenv("OKX_PASSPHRASE", ""),
        is_demo=False,
    )
    entries = get_entries()
    print(f"Проверяем {len(entries)} ENTRY сигналов...\n")
    fmt = "{:<20} {:<12} {:<8} {:<5} {:<10} {:<10} {:<10} {:<12} {}"
    print(fmt.format("Время", "Пара", "Стиль", "Side", "Вход", "SL", "TP1", "Результат", "Время до"))
    print("-" * 100)

    wins, losses, open_ = 0, 0, 0

    for e in entries:
        symbol = e["pair"]  # e.g. SOL-USDT, client appends -SWAP internally
        # fetch 5m candles: window from signal time + 24 hours (288 candles)
        window_end = e["ts_ms"] + 24 * 60 * 60 * 1000
        raw = await client.get_history_candles(symbol, "5m", after=window_end, limit=288)
        if not raw:
            print(fmt.format(e["ts_str"][:16], e["pair"], e["style"], e["side"],
                             str(e["entry"]), str(round(e["sl"], 4)), str(round(e["tp1"], 4)),
                             "НЕТ ДАННЫХ", ""))
            continue

        result = "ОТКРЫТА"
        hit_time = ""
        # raw candles newest-first — reverse for chronological
        for i, c in enumerate(reversed(raw)):
            ts_c = int(c[0])
            if ts_c < e["ts_ms"]:
                continue
            high = float(c[2])
            low = float(c[3])
            minutes = (ts_c - e["ts_ms"]) // 60000

            if e["side"] == "buy":
                if low <= e["sl"]:
                    result = "СТОП ❌"
                    hit_time = f"{minutes}m"
                    losses += 1
                    break
                if e["tp2"] and high >= e["tp2"]:
                    result = "TP2 ✅✅"
                    hit_time = f"{minutes}m"
                    wins += 1
                    break
                if high >= e["tp1"]:
                    result = "TP1 ✅"
                    hit_time = f"{minutes}m"
                    wins += 1
                    break
            else:
                if high >= e["sl"]:
                    result = "СТОП ❌"
                    hit_time = f"{minutes}m"
                    losses += 1
                    break
                if e["tp2"] and low <= e["tp2"]:
                    result = "TP2 ✅✅"
                    hit_time = f"{minutes}m"
                    wins += 1
                    break
                if low <= e["tp1"]:
                    result = "TP1 ✅"
                    hit_time = f"{minutes}m"
                    wins += 1
                    break
        else:
            open_ += 1

        print(fmt.format(
            e["ts_str"][:16], e["pair"], e["style"], e["side"],
            str(e["entry"]), str(round(e["sl"], 4)), str(round(e["tp1"], 4)),
            result, hit_time,
        ))

    print()
    print(f"Итого: ✅ {wins} побед  ❌ {losses} стопов  ? {open_} открытых/нет данных")
    total = wins + losses
    if total:
        print(f"Winrate: {wins * 100 // total}%  ({wins}/{total})")

    await client.close()


asyncio.run(backtest())
