# -*- coding: utf-8 -*-
"""
entry_regime.py — разрез эджа тайминга ПО РЕЖИМАМ рынка (read-only).

Интуиция трейдера: «вход на развороте отката» — приём для ТРЕНДА; в RANGING/DRIFT может не работать.
Режем NET-бэк (тейп) по regime (и regime×style), плюс проверяем, объясняет ли состав режимов
лумпи-флаг из OOS (распределение режимов 1-я половина vs 2-я).
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from collections import Counter
from entry_full_backtest import build, run, DEF_STOP_K, DEF_TP_K, mean, wr

def line(tag,data):
    if not data: return
    r=run(data,DEF_STOP_K,DEF_TP_K)
    fn,rn=r["fn"],r["rn"]
    mf,mr=r["mf"],r["mr"]
    print(f"  {tag:20} n={len(data):3} | FIRE {mean(fn):+6.2f}% WR{wr(fn):3.0f}%  ||  "
          f"RULE {mean(rn):+6.2f}% (сум {sum(rn):+6.1f}) WR{wr(rn):3.0f}% сделок {len(rn):2}  | "
          f"matched FIRE{mean(mf):+.2f}→RULE{mean(mr):+.2f}")

def main():
    print("строю тейп-бэк...",flush=True)
    data,skip=build()
    data=sorted(data,key=lambda d:d["ts0"]); n=len(data)
    print(f"сигналов {n} | пропущено {skip}\n")

    print("1) NET ПО РЕЖИМАМ (FIRE = наш вход, RULE = правило):")
    regs=sorted({d["regime"] for d in data},key=lambda r:-sum(1 for d in data if d["regime"]==r))
    for rg in regs:
        line(str(rg), [d for d in data if d["regime"]==rg])

    print("\n2) NET ПО РЕЖИМ×СТИЛЬ:")
    cells=sorted({(d["regime"],d["style"]) for d in data},
                 key=lambda k:-sum(1 for d in data if (d["regime"],d["style"])==k))
    for rg,st in cells:
        sub=[d for d in data if (d["regime"],d["style"])==(rg,st)]
        if len(sub)>=5: line(f"{str(rg)[:7]}/{str(st)[:5]}",sub)

    print("\n3) СОСТАВ РЕЖИМОВ: объясняет ли он лумпи-флаг OOS?")
    mid=n//2
    print(f"   1-я половина ({data[0]['day']}..{data[mid-1]['day']}): {dict(Counter(d['regime'] for d in data[:mid]))}")
    print(f"   2-я половина ({data[mid]['day']}..{data[-1]['day']}): {dict(Counter(d['regime'] for d in data[mid:]))}")

if __name__=="__main__":
    main()
