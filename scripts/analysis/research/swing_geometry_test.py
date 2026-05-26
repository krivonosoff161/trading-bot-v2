# -*- coding: utf-8 -*-
"""
swing_geometry_test.py — проверка гипотезы трейдера: у SWING стоп скальповый (узкий, 15m), тесен для холда 5ч.
Read-only. Тик-точный тейп (май). Вход = FIRE (наш), холд по стилю. Свип ширины стопа в единицах ATR_15m
+ R:R. Смотрим NET и расклад SL/TP/TIME. Если у SWING оптимум в ШИРОКОМ стопе (сейчас ~2.16×ATR_15m),
а доля SL/ранних выбиваний падает с расширением — гипотеза верна.
"""
import sys, io, os, json
import numpy as np
from entry_early_retest_v1 import day_ticks, tape_window, tape_entry_fill, atr_at, fnum  # wraps stdout utf-8 on import
ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SNAP=os.path.join(ROOT,"logs","signals","signal_snapshot.jsonl")
WARMUP=6*3600*1000
COMM=0.10

def bars_bucket(ts,pr,bucket):
    if len(ts)==0: return []
    bs=(ts//bucket)*bucket; out=[]; cur=None
    for k in range(len(ts)):
        b=int(bs[k]); p=float(pr[k])
        if cur is None or b!=cur["t"]:
            if cur is not None: out.append(cur)
            cur={"t":b,"o":p,"h":p,"l":p,"c":p}
        else:
            if p>cur["h"]:cur["h"]=p
            if p<cur["l"]:cur["l"]=p
            cur["c"]=p
    if cur is not None: out.append(cur)
    return out

def bar_idx(bars,ts0):
    idx=None
    for i in range(len(bars)):
        if bars[i]["t"]<=ts0: idx=i
        else: break
    return idx

def sim(ts,pr,e_ts,e_px,stop,tp,sgn,deadline):
    i=int(np.searchsorted(ts,e_ts,side="right")); last=e_px
    for k in range(i,len(ts)):
        if ts[k]>deadline: return "TIME",sgn*(last-e_px)/e_px*100-COMM
        p=float(pr[k]); last=p
        if (p<=stop) if sgn>0 else (p>=stop): return "SL",sgn*(p-e_px)/e_px*100-COMM
        if (p>=tp) if sgn>0 else (p<=tp): return "TP",sgn*(tp-e_px)/e_px*100-COMM
    return "TIME",sgn*(last-e_px)/e_px*100-COMM

def load():
    sigs=[json.loads(x) for x in open(SNAP,encoding="utf-8") if x.strip() and json.loads(x).get("entry_signal")=="ENTRY"]
    data=[]
    for s in sigs:
        style=s.get("trade_style")
        if style not in ("FAST","SWING"): continue
        sym=s["symbol"]; sgn=1 if s["side"] in("buy","long") else -1; ts0=int(s["ts_ms"])
        hold=300 if style=="SWING" else int(s.get("hold_min",90))
        w=tape_window(sym,ts0-WARMUP,ts0+hold*60000+900000)
        if w is None or len(w[0])<200: continue
        ts,pr,sd,sz=w
        b15=bars_bucket(ts,pr,900000); b60=bars_bucket(ts,pr,3600000)
        i15=bar_idx(b15,ts0); i60=bar_idx(b60,ts0)
        if i15 is None or i15<14: continue
        a15=atr_at(b15,i15)
        if not a15: continue
        a60=atr_at(b60,i60) if (i60 is not None and i60>=3) else None   # только для справочного ratio
        # вход = FIRE (рыночный, по тейпу)
        dts=((ts0//300000)*300000)+300000
        e_ts,e_px=tape_entry_fill(ts,pr,sd,dts,sgn)
        if e_px is None: continue
        data.append(dict(style=style,sgn=sgn,ts=ts,pr=pr,e_ts=e_ts,e_px=e_px,
                         a15=a15,a60=a60,deadline=dts+hold*60000,ratio=a60/a15 if a15 else None))
    return data

def block(data,style):
    sub=[d for d in data if d["style"]==style]
    if not sub:
        print(f"\n[{style}] нет данных"); return
    ratios=[d["ratio"] for d in sub if d["ratio"]]
    print(f"\n=== {style}  (n={len(sub)})   ATR_1H/ATR_15m ≈ {np.mean(ratios):.2f} ===")
    print(f"  {'стоп(×ATR15)':>13} {'R:R':>4} {'ср.NET':>8} {'WR':>5} {'%SL':>5} {'%TP':>5} {'%TIME':>6}")
    for sk in (1.5,2.0,2.5,3.0,3.5,4.0):
        for rr in (1.5,2.5):
            nets=[];oc=[]
            for d in sub:
                sd_=sk*d["a15"]; sgn=d["sgn"]
                stop=d["e_px"]-sgn*sd_; tp=d["e_px"]+sgn*rr*sd_
                o,n=sim(d["ts"],d["pr"],d["e_ts"],d["e_px"],stop,tp,sgn,d["deadline"])
                nets.append(n);oc.append(o)
            wr=100*sum(1 for x in nets if x>0)/len(nets)
            sl=100*oc.count("SL")/len(oc); tp_=100*oc.count("TP")/len(oc); tm=100*oc.count("TIME")/len(oc)
            mark=" ← ~текущий" if (style=="SWING" and sk==2.0) or (style=="FAST" and sk==2.0) else ""
            print(f"  {sk:>13.1f} {rr:>4.1f} {np.mean(nets):>+8.2f} {wr:>4.0f}% {sl:>4.0f}% {tp_:>4.0f}% {tm:>5.0f}%{mark}")

def block_fixed(data,style):
    """ТВОЯ формулировка: тейк ФИКСИРОВАН (1H ATR), меняем только ширину стопа (15m)."""
    sub=[d for d in data if d["style"]==style and d["a60"]]
    if not sub:
        print(f"\n[{style} fixed-1H-target] нет данных"); return
    print(f"\n=== {style}: фикс. тейк (×ATR_1H), варьируем стоп (×ATR_15m)  n={len(sub)} ===")
    print(f"  {'стоп×ATR15':>10} {'тейк×ATR1H':>11} {'ср.NET':>8} {'WR':>5} {'%SL':>5} {'%TP':>5} {'%TIME':>6}")
    for sk in (1.5,2.5,3.5,4.5):
        for tm in (0.5,1.0,1.5):
            nets=[];oc=[]
            for d in sub:
                sgn=d["sgn"]; stop=d["e_px"]-sgn*sk*d["a15"]; tp=d["e_px"]+sgn*tm*d["a60"]
                o,n=sim(d["ts"],d["pr"],d["e_ts"],d["e_px"],stop,tp,sgn,d["deadline"])
                nets.append(n);oc.append(o)
            wr=100*sum(1 for x in nets if x>0)/len(nets)
            sl=100*oc.count("SL")/len(oc); tp_=100*oc.count("TP")/len(oc); tm_=100*oc.count("TIME")/len(oc)
            print(f"  {sk:>10.1f} {tm:>11.1f} {np.mean(nets):>+8.2f} {wr:>4.0f}% {sl:>4.0f}% {tp_:>4.0f}% {tm_:>5.0f}%")

def main():
    print("грузлю тейп по SWING/FAST сигналам...",flush=True)
    data=load()
    print(f"сигналов с тейпом: {len(data)} (SWING {sum(1 for d in data if d['style']=='SWING')}, FAST {sum(1 for d in data if d['style']=='FAST')})")
    print("Гипотеза: если у SWING NET растёт с ШИРИНОЙ стопа, а %SL падает — стоп 15m тесен для холда 5ч.")
    block(data,"SWING")
    block(data,"FAST")
    block_fixed(data,"SWING")
    print("\n  Текущий бот: SWING стоп ~2.16×ATR_15m (sl_k 1.8×1.2); FAST ~1.7×ATR_15m. ATR_1H-стоп ≈ ratio×тот же k.")

if __name__=="__main__":
    main()
