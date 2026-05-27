# -*- coding: utf-8 -*-
"""
stat_arb_v4_universe.py — углубление якоря: Kalman стат-арб на БОЛЬШОЙ вселенной. Read-only.

v3 был на 15 майорах (8 пар). Расширяем до ~28 монет → больше коинтегрированных пар → лучше диверсификация,
меньше концентрации. Гейт коинтеграции (формация) + Kalman (δ1e-5/k1.5) + ребаланс-кост. Walk-forward 5 окон OOS.
Цель: держится/улучшается ли якорь на широком юниверсе. Возвращает дневной поток доходности для портфель-бленда.
"""
import sys, io, json, time, urllib.request, itertools
import numpy as np
from statsmodels.tsa.stattools import coint
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
SYMS=["BTC","ETH","SOL","XRP","DOGE","ADA","BNB","LINK","AVAX","LTC","BCH","DOT","TRX","ATOM","FIL",
      "UNI","NEAR","APT","ARB","OP","INJ","SUI","SEI","TIA","RUNE","AAVE","ALGO","GALA"]
D=1e-5; K=1.5; COINT_P=0.05; BARS_YR=6*365; FUND=0.00005

def fetch_4h(inst, want=1600):
    out={}; cursor=int(time.time()*1000)
    for _ in range(20):
        url=f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar=4H&after={cursor}&limit=100"
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req,timeout=15) as r: j=json.loads(r.read())
        except Exception: break
        d=j.get("data",[])
        if not d: break
        for row in d: out[int(row[0])]=float(row[4])
        old=min(int(row[0]) for row in d)
        if old>=cursor or len(out)>=want: break
        cursor=old; time.sleep(0.08)
    return out

def kalman(y,x,oos_lo,oos_hi,fee):
    n=len(y); Vw=D/(1-D)*np.eye(2); Ve=1e-3; beta=np.zeros(2); R=np.zeros((2,2))
    ry=np.diff(np.exp(y))/np.exp(y)[:-1]; rx=np.diff(np.exp(x))/np.exp(x)[:-1]
    out=np.zeros(oos_hi-oos_lo-1 if oos_hi-1>oos_lo else 0); pos=0; prevb=0.0; idx=0
    for t in range(n-1):
        A=np.array([1.0,x[t]]); R=R+Vw
        e=y[t]-A@beta; Q=A@R@A+Ve; sq=np.sqrt(max(Q,1e-12))
        Kg=(R@A)/Q; beta=beta+Kg*e; R=R-np.outer(Kg,A)@R
        if not (oos_lo<=t<oos_hi-1): prevb=beta[1]; continue
        want=pos
        if pos==0:
            if e>K*sq: want=-1
            elif e<-K*sq: want=1
        elif (pos==1 and e>=0) or (pos==-1 and e<=0): want=0
        c=pos*(ry[t]-beta[1]*rx[t])
        if want!=pos: c-=fee*2
        if pos!=0: c-=FUND+fee*abs(beta[1]-prevb)
        if idx<len(out): out[idx]=c
        idx+=1; pos=want; prevb=beta[1]
    return out

def metr(p):
    if len(p)==0 or p.std()==0: return 0,0,0
    eq=np.cumprod(1+p); pk=np.maximum.accumulate(eq)
    return (eq[-1]-1)*100, ((eq-pk)/pk).min()*100, p.mean()/p.std()*np.sqrt(BARS_YR)

def main():
    print(f"тяну 4H по {len(SYMS)} монетам...",flush=True)
    data={}
    for s in SYMS:
        d=fetch_4h(s+"-USDT-SWAP")
        if len(d)>=400: data[s]=d
    syms=sorted(data); common=sorted(set.intersection(*[set(data[s]) for s in syms]))
    px={s:np.log(np.array([data[s][t] for t in common])) for s in syms}
    N=len(common); half=N//2; pairs=list(itertools.combinations(syms,2))
    print(f"загружено {len(syms)} монет, общих баров {N}, кандидатов-пар {len(pairs)}\n")
    print("гейт коинтеграции (формация)...",flush=True)
    gated=[]
    for a,b in pairs:
        try:
            if coint(px[a][:half],px[b][:half])[1]<COINT_P: gated.append((a,b))
        except Exception: pass
    print(f"коинтегрировано: {len(gated)} пар\n")
    for tag,fee in [("мейкер 0.02%",0.0002),("тейкер 0.05%",0.0005)]:
        cs=[kalman(px[a],px[b],half,N,fee) for a,b in gated]
        L=min(len(c) for c in cs); port=np.array([c[:L] for c in cs]).mean(axis=0)
        tot,dd,sh=metr(port)
        wn=[]; k5=len(port)//5
        for w in range(5):
            seg=port[w*k5:(w+1)*k5] if w<4 else port[w*k5:]; wn.append((np.prod(1+seg)-1)*100)
        pos=sum(1 for x in wn if x>0)
        print(f"  [{tag}] total {tot:+.1f}%  maxDD {dd:.1f}%  Sharpe {sh:+.2f}  | окна {['%+.1f'%x for x in wn]}  ({pos}/5 +)")
    print(f"\n  Якорь устойчив, если на {len(gated)} парах остаётся Sharpe>1.5, DD низкий, 4-5/5 окон +.")
    # сохранить дневной поток для бленда
    cs=[kalman(px[a],px[b],half,N,0.0002) for a,b in gated]
    L=min(len(c) for c in cs); port=np.array([c[:L] for c in cs]).mean(axis=0)
    np.save("scripts/analysis/research/_stream_statarb.npy", port)
    print(f"  поток доходности сохранён (_stream_statarb.npy, {len(port)} баров) для портфель-бленда.")

if __name__=="__main__":
    main()
