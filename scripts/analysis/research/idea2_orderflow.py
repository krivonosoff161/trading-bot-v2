# -*- coding: utf-8 -*-
"""
idea2_orderflow.py — КАМПАНИЯ идея №2: order-flow / тейп-микроструктура. Read-only.

Принципиально другой источник: не цена-паттерн, а РЕАЛЬНЫЙ ПОТОК (агрессор buy/sell из тейпа).
Гипотеза: CVD-тренд / поглощение / дельта-дивергенция предсказывают будущий ход там, где price-only слеп.
Итерация 1 = предсказательность фич к forward-return (train 11-18 / holdout 19-26). Потоковая агрегация
тиков в 5m (память бережём). Гейт: фича робастно (один знак + |r| заметный) предсказывает в ОБА окна.
"""
import sys, io, os, gzip, csv
from datetime import datetime
import numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
TAPE=r"E:\trading-data\ticks"
SYMS=["BTC","ETH","SOL","XRP","DOGE","ADA","BSB","EDEN","AI","TRUTH","PUMP","SAHARA","BILL","CHZ","NOT","TURBO"]
DAYS=[f"2026-05-{d:02d}" for d in range(11,27)]
TRAIN_END="2026-05-18"; H=12   # forward 1ч

def bars_stream(path):
    """потоково: тик-файл -> 5m бары с buy/sell объёмом (без хранения тиков)."""
    op=gzip.open if path.endswith(".gz") else open
    out=[]; cur=None
    with op(path,"rt",encoding="utf-8",errors="ignore") as fh:
        r=csv.reader(fh); next(r,None)
        for row in r:
            if len(row)<6: continue
            try: t=int(row[0]); side=row[3]; p=float(row[4]); z=float(row[5])
            except: continue
            b=(t//300000)*300000
            if cur is None or b!=cur["t"]:
                if cur is not None: out.append(cur)
                cur={"t":b,"o":p,"h":p,"l":p,"c":p,"buy":0.0,"sell":0.0}
            if p>cur["h"]:cur["h"]=p
            if p<cur["l"]:cur["l"]=p
            cur["c"]=p
            if side=="buy": cur["buy"]+=z
            else: cur["sell"]+=z
    if cur is not None: out.append(cur)
    return out

def feats(bars):
    """причинные order-flow фичи на каждом баре + forward return."""
    n=len(bars)
    c=[b["c"] for b in bars]; hi=[b["h"] for b in bars]; lo=[b["l"] for b in bars]
    vol=[b["buy"]+b["sell"] for b in bars]; delta=[b["buy"]-b["sell"] for b in bars]
    cvd=np.cumsum(delta)
    rows=[]
    for i in range(7,n-H):
        if not c[i] or vol[i]<=0: continue
        ret_i=(c[i]-c[i-1])/c[i-1]*100 if c[i-1] else 0
        of_delta = delta[i]/vol[i]                              # net агрессор бара [-1..1]
        avgvol=np.mean(vol[i-6:i]) or 1e-9
        of_cvd_slope = (cvd[i]-cvd[i-6])/avgvol                 # тренд потока за 30м
        of_absorb = vol[i]/(abs(ret_i)+0.05)                    # объём/|ход| = поглощение
        # бычья дивергенция: цена ниже мин за 6, а cvd выше cvd[-6]
        pl=min(lo[i-6:i+1]); ph=max(hi[i-6:i+1])
        div = 0.0
        if lo[i]<=pl and cvd[i]>cvd[i-6]: div=1.0
        elif hi[i]>=ph and cvd[i]<cvd[i-6]: div=-1.0
        fwd=(c[i+H]-c[i])/c[i]*100
        rows.append((bars[i]["t"], of_delta, of_cvd_slope, of_absorb, div, fwd))
    return rows

def corr(x,y):
    x=np.array(x,float); y=np.array(y,float)
    m=np.isfinite(x)&np.isfinite(y)
    if m.sum()<20: return None
    x,y=x[m],y[m]
    if x.std()==0 or y.std()==0: return None
    return float(np.corrcoef(x,y)[0,1])

def main():
    train=[]; hold=[]; loaded=0
    for sym in SYMS:
        inst=sym+"-USDT-SWAP"
        for day in DAYS:
            p=os.path.join(TAPE,inst,day+".csv.gz")
            if not os.path.exists(p): continue
            try: bars=bars_stream(p)
            except Exception: continue
            if len(bars)<30: continue
            for rec in feats(bars):
                (train if day<=TRAIN_END else hold).append(rec)
            loaded+=1
        print(f"  ...{sym} готов",flush=True)
    print(f"\nбаров-точек: train {len(train)}, holdout {len(hold)} (файлов {loaded})")
    names=["of_delta","of_cvd_slope","of_absorb","div"]
    print(f"\n=== ИДЕЯ №2 order-flow: корреляция фич с forward-return (1ч) ===")
    print(f"  {'фича':14}{'r TRAIN':>10}{'r HOLDOUT':>11}  вердикт")
    def col(rows,k): return [r[1+k] for r in rows]
    fwd_tr=[r[5] for r in train]; fwd_ho=[r[5] for r in hold]
    any_signal=False
    for k,nm in enumerate(names):
        rt=corr(col(train,k),fwd_tr); rh=corr(col(hold,k),fwd_ho)
        if rt is None or rh is None:
            print(f"  {nm:14}{'—':>10}{'—':>11}  нет данных"); continue
        robust = (abs(rt)>=0.04 and abs(rh)>=0.04 and (rt>0)==(rh>0))
        if robust: any_signal=True
        print(f"  {nm:14}{rt:+10.3f}{rh:+11.3f}  {'РОБАСТНО' if robust else 'шум/нестабильно'}")
    print(f"\n  ВЕРДИКТ: {'есть робастная фича потока → строить вход (итер 2)' if any_signal else 'order-flow НЕ предсказывает forward на холдауте → CLOSED'}")

if __name__=="__main__":
    main()
