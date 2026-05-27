# -*- coding: utf-8 -*-
"""
hunt_regime_mr.py — рычаг 3: regime-gated SHORT-biased mean-reversion (спека агента C). Read-only.

Медведь −58% → не грести против, а ПО течению: шортить ПЕРЕКУПЛЕННЫЕ отскоки в даунтренде.
Сигнал на 5m: тренд (close<EMA200=даунтренд) + перекупленность (RSI2 высок / выше верхней BB) + разворотный бар.
Цель = средняя (SMA20), стоп = +k·ATR, time-stop. Сравниваем SHORT-only / LONG-only / симметрично.
train(11-18)/holdout(19-26) + перебор порога RSI и стопа. Гейт: SHORT в + на holdout (не лонг — он в медведе минусит).
"""
import sys, io, os, gzip, csv
from datetime import datetime, timedelta
from statistics import mean
import numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
F5=os.path.join(ROOT,"logs","features","5m")
COMM=0.10; H=12; TRAIN_END="2026-05-18"

def fnum(x):
    try: return float(x)
    except: return None
def load_all(sym):
    d=datetime(2026,5,11); e=datetime(2026,5,26); rows=[]
    while d<=e:
        day=d.strftime("%Y-%m-%d")
        for ext,op in ((".csv.gz",gzip.open),(".csv",open)):
            p=os.path.join(F5,sym,day+ext)
            if os.path.exists(p):
                with op(p,"rt",encoding="utf-8",errors="ignore") as fh: rows+=list(csv.DictReader(fh))
                break
        d+=timedelta(days=1)
    seen=set(); out=[]
    for r in sorted(rows,key=lambda r:int(r["ts_ms"])):
        if r["ts_ms"] not in seen: seen.add(r["ts_ms"]); out.append(r)
    return out

def rsi(c,p=2):
    n=len(c); o=[None]*n
    if n<p+1: return o
    g=l=0.0
    for i in range(1,p+1): ch=c[i]-c[i-1]; g+=max(ch,0); l+=max(-ch,0)
    ag=g/p; al=l/p; o[p]=100-100/(1+ag/al) if al>0 else 100.0
    for i in range(p+1,n):
        ch=c[i]-c[i-1]; ag=(ag*(p-1)+max(ch,0))/p; al=(al*(p-1)+max(-ch,0))/p
        o[i]=100-100/(1+ag/al) if al>0 else 100.0
    return o
def ema(c,p):
    n=len(c);o=[None]*n
    if n<p: return o
    k=2/(p+1); s=sum(c[:p])/p; o[p-1]=s
    for i in range(p,n): s=c[i]*k+s*(1-k); o[i]=s
    return o
def atr_arr(h,l,c,p=14):
    n=len(c); tr=[0.0]*n
    for i in range(1,n): tr[i]=max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))
    o=[None]*n
    if n>p:
        a=sum(tr[1:p+1])/p; o[p]=a
        for i in range(p+1,n): a=(a*(p-1)+tr[i])/p; o[i]=a
    return o

def trades(rows, mode, rsi_ob, stop_k):
    c=[fnum(r["close"]) for r in rows]; h=[fnum(r["high"]) for r in rows]; l=[fnum(r["low"]) for r in rows]; op=[fnum(r["open"]) for r in rows]
    ts=[int(r["ts_ms"]) for r in rows]
    if any(x is None for x in c[:25]) or len(c)<210: return []
    e200=ema(c,200); r2=rsi(c,2)
    sma20=[None]*len(c); up=[None]*len(c); lo=[None]*len(c)
    for i in range(20,len(c)):
        w=c[i-20:i]; m=mean(w); sd=(sum((x-m)**2 for x in w)/20)**0.5
        sma20[i]=m; up[i]=m+2*sd; lo[i]=m-2*sd
    a=atr_arr(h,l,c); out=[]; i=205
    while i<len(c)-2:
        if None in (c[i],e200[i],r2[i],sma20[i],a[i],op[i]): i+=1; continue
        sgn=0
        down=c[i]<e200[i]; upt=c[i]>e200[i]
        ob = (r2[i]>=rsi_ob) or (up[i] and c[i]>=up[i])
        os_= (r2[i]<=100-rsi_ob) or (lo[i] and c[i]<=lo[i])
        rev_dn = c[i]<op[i] and c[i]<c[i-1]
        rev_up = c[i]>op[i] and c[i]>c[i-1]
        if mode in ("SHORT","BOTH") and down and ob and rev_dn: sgn=-1
        elif mode in ("LONG","BOTH") and upt and os_ and rev_up: sgn=1
        if sgn==0: i+=1; continue
        e=c[i]; tgt=sma20[i]; stop=e+sgn*stop_k*a[i] if sgn<0 else e-stop_k*a[i]
        stop=e - sgn*stop_k*a[i]   # стоп против направления
        oc="TIME"; exitp=c[min(i+H,len(c)-1)]
        for j in range(i+1,min(i+H+1,len(c))):
            adv=l[j] if sgn>0 else h[j]; fav=h[j] if sgn>0 else l[j]
            if adv is not None and ((adv<=stop) if sgn>0 else (adv>=stop)): oc="SL"; exitp=stop; break
            if fav is not None and ((fav>=tgt) if sgn>0 else (fav<=tgt)): oc="TP"; exitp=tgt; break
        net=sgn*(exitp-e)/e*100-COMM
        out.append((ts[i],net,oc)); i+=4
    return out

def main():
    syms=sorted(os.listdir(F5))
    print(f"=== РЫЧАГ 3: regime-gated MR, {len(syms)} символов 5m, цель=SMA20, time-stop {H} баров ===")
    print(f"(гейт: SHORT в + на HOLDOUT 19-26.05 — это медведь-логика; LONG для контраста должен минусить)\n")
    for rsi_ob in (90,95):
        for sk in (1.0,1.5):
            print(f"--- RSI2 OB>={rsi_ob}/OS<={100-rsi_ob}, стоп {sk}×ATR ---")
            for mode in ("SHORT","LONG","BOTH"):
                tr=[]; ho=[]
                for s in syms:
                    rows=load_all(s)
                    for t in trades(rows,mode,rsi_ob,sk):
                        day=datetime.utcfromtimestamp(t[0]/1000).strftime("%Y-%m-%d")
                        (tr if day<=TRAIN_END else ho).append(t)
                def stat(x):
                    if not x: return "n=0"
                    nets=[v[1] for v in x]; ocs=[v[2] for v in x]
                    return f"n={len(x):4} NET ср{mean(nets):+.2f}% сум{sum(nets):+6.1f}% WR{100*sum(1 for v in nets if v>0)//len(nets)}% TP{100*ocs.count('TP')//len(ocs)}/SL{100*ocs.count('SL')//len(ocs)}"
                print(f"   {mode:6} TRAIN {stat(tr)}")
                print(f"   {mode:6} HOLD  {stat(ho)}")
            print()

if __name__=="__main__":
    main()
