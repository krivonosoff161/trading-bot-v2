# -*- coding: utf-8 -*-
"""
entry_oos.py — проверка, что эдж тайминга НЕ подогнан под выборку (out-of-sample). Read-only.

Импортирует тейп-бэк (entry_full_backtest) и режет по ДАТАМ:
  1) хронологический 2-fold: train (первая половина) vs holdout (вторая) — правило ФИКСИРОВАНО.
  2) расширяющийся walk-forward (3 окна) — растёт train, тестим следующий кусок.
  3) sweep порога ВЕТО (vol_ratio) — это обрыв (оверфит) или плато (робастно)?
Если RULE плюсует на holdout и порог = плато → эдж не подгонка.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from entry_full_backtest import build, run, DEF_STOP_K, DEF_TP_K, VETO_VOL, mean, wr

def summ(tag,data):
    r=run(data,DEF_STOP_K,DEF_TP_K)
    fn,rn=r["fn"],r["rn"]
    print(f"  {tag:22} n={len(data):3} | FIRE {mean(fn):+.2f}% (сум {sum(fn):+.1f}) WR{wr(fn):.0f}%"
          f"  ||  RULE {mean(rn):+.2f}% (сум {sum(rn):+.1f}) WR{wr(rn):.0f}% сделок {len(rn)}")
    return mean(rn),sum(rn)

def main():
    print("строю тейп-бэк...",flush=True)
    data,skip=build()
    data=sorted(data,key=lambda d:d["ts0"])
    n=len(data)
    days=sorted({d["day"] for d in data})
    print(f"сигналов {n} | даты {days[0]}..{days[-1]} | пропущено {skip}\n")

    print("1) ХРОНОЛОГИЧЕСКИЙ 2-FOLD (правило фиксировано, holdout = не виден при выборе правила):")
    mid=n//2
    summ("TRAIN (1-я половина)",data[:mid])
    summ("HOLDOUT (2-я половина)",data[mid:])

    print("\n2) WALK-FORWARD (расширяющийся train → тестим следующий кусок OOS):")
    cuts=[int(n*0.4),int(n*0.6),int(n*0.8)]
    prev=0
    for i,c in enumerate(cuts):
        seg=data[c:cuts[i+1]] if i+1<len(cuts) else data[c:]
        if seg: summ(f"OOS окно {i+1} [{data[c]['day']}..{seg[-1]['day']}]",seg)

    print("\n3) SWEEP ПОРОГА ВЕТО (плато = робастно, обрыв = подгонка):")
    print(f"  {'veto vol>':>10} {'RULE ср.NET':>12} {'RULE сум':>10} {'сделок':>8} {'WR':>6}")
    for vv in (2.5,3.0,3.4,4.0,5.0,99.0):
        r=run(data,DEF_STOP_K,DEF_TP_K,veto_vol=vv)
        rn=r["rn"]
        tag="(нет вето)" if vv>90 else ""
        print(f"  {vv:>10.1f} {mean(rn):>12.2f} {sum(rn):>10.1f} {len(rn):>8} {wr(rn):>5.0f}% {tag}")

    print("\n  ИТОГ: эдж устойчив, если RULE>0 на HOLDOUT и порог вето на плато (а не одиночный пик 3.4).")

if __name__=="__main__":
    main()
