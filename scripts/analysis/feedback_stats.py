"""View feedback statistics from feedback_log.jsonl.

Usage:
    python scripts/feedback_stats.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.feedback import load_entries

entries = load_entries()

if not entries:
    print("Нет записей в feedback_log.jsonl")
    sys.exit(0)

total     = len(entries)
entered   = [e for e in entries if e.get("entered") is True]
skipped   = [e for e in entries if e.get("result") == "skipped"]
no_answer = [e for e in entries if e.get("entered") is None]
with_result = [e for e in entered if e.get("result") is not None]

wins   = [e for e in with_result if e["result"] in ("tp1", "tp2")]
losses = [e for e in with_result if e["result"] == "sl"]
manual = [e for e in with_result if e["result"] == "manual"]
open_  = [e for e in entered    if e.get("result") is None]

print("=" * 60)
print("FEEDBACK СТАТИСТИКА")
print("=" * 60)
print(f"Сигналов отправлено:  {total}")
print(f"  Вошли в сделку:     {len(entered)} ({len(entered)*100//total}%)")
print(f"  Пропустили:         {len(skipped)}")
print(f"  Без ответа:         {len(no_answer)}")
print()

if with_result:
    wr = len(wins) * 100 // len(with_result)
    print(f"Закрытых сделок:      {len(with_result)}")
    print(f"  ✅ TP1/TP2:          {len(wins)}")
    print(f"  ❌ STOP:             {len(losses)}")
    print(f"  🔧 Вручную:          {len(manual)}")
    print(f"  Winrate (пользователи): {wr}%")
    print()

if open_:
    print(f"Открытых (ждут результат): {len(open_)}")
    for e in open_:
        print(f"  {e['created_at'][:16]}  {e['symbol']:12}  {e.get('style',''):8}  {e.get('side','')}")
    print()

print("=" * 60)
print("ПО ПАРЕ:")
from collections import Counter
pair_wins   = Counter(e["symbol"] for e in wins)
pair_losses = Counter(e["symbol"] for e in losses)
all_pairs   = sorted(set(pair_wins) | set(pair_losses))
fmt = "{:<14} {:>4} {:>4} {:>8}"
print(fmt.format("Пара", "✅", "❌", "Winrate"))
print("-" * 35)
for sym in all_pairs:
    w = pair_wins.get(sym, 0)
    l = pair_losses.get(sym, 0)
    total_p = w + l
    wr_p = f"{w*100//total_p}%" if total_p else "—"
    print(fmt.format(sym, w, l, wr_p))

print()
print("ПО СТИЛЮ:")
style_wins   = Counter(e.get("style","") for e in wins)
style_losses = Counter(e.get("style","") for e in losses)
all_styles   = sorted(set(style_wins) | set(style_losses))
for s in all_styles:
    w = style_wins.get(s, 0)
    l = style_losses.get(s, 0)
    total_s = w + l
    wr_s = f"{w*100//total_s}%" if total_s else "—"
    print(f"  {s:<10} ✅{w}  ❌{l}  WR {wr_s}")
