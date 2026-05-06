import asyncio, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()
from scripts.build_journal import _load_real_trades, _fmt_dt

trades = _load_real_trades()
trades.sort(key=lambda x: int(x.get("cTime") or 0))
total_net = 0
for t in trades:
    net = float(t.get("realizedPnl") or 0) + float(t.get("fundingFee") or 0)
    total_net += net
    inst = t.get("instId", "")
    dr = (t.get("direction") or "?").upper()
    dt = _fmt_dt(int(t.get("cTime", 0)))
    print(f"{dt} | {inst:<22} | {dr:<5} | net={net:+.4f}")
print(f"\nИТОГО NET: {total_net:+.4f} USDT  ({len(trades)} позиций)")
