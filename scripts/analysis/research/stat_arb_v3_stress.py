# -*- coding: utf-8 -*-
"""
stat_arb_v3_stress.py — ЛОМАЕМ Kalman-стат-арб (анти-эйфория). Read-only.

Kalman дал +10%/Sharpe2.5/DD−6% OOS — первый сильный результат. Три удара перед верой:
  1) РЕБАЛАНС-КОСТ: Kalman двигает β каждый бар → реальная комса на подстройку x-ноги (я её не заложил).
  2) КОНТРОЛЬ не-коинтегрированных пар: если случайные пары тоже плюсуют → эдж в механике Kalman, не в коинтеграции (тревога).
  3) ПЕРЕВЁРНУТЫЙ сплит (OOS=1-я половина): вдруг это свойство конкретного периода.
"""
import sys, io, json, time, urllib.request, itertools
import numpy as np
from statsmodels.tsa.stattools import coint
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
SYMS=["BTC","ETH","SOL","XRP","DOGE","ADA","BNB","LINK","AVAX","LTC","DOT","TRX","FIL","ATOM","UNI"]
BARS_YR=6*365; FUND=0.00005

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
        cursor=old; time.sleep(0.1)
    return out

def kalman(y,x,delta,k,oos_lo,oos_hi,fee,rebal_cost):
    n=len(y); Vw=delta/(1-delta)*np.eye(2); Ve=1e-3
    beta=np.zeros(2); R=np.zeros((2,2)); contrib=[]; pos=0; prevb=0.0
    ry=np.diff(np.exp(y))/np.exp(y)[:-1]; rx=np.diff(np.exp(x))/np.exp(x)[:-1]
    for t in range(n-1):
        A=np.array([1.0,x[t]]); R=R+Vw
        e=y[t]-A@beta; Q=A@R@A+Ve; sq=np.sqrt(max(Q,1e-12))
        K=(R@A)/Q; beta=beta+K*e; R=R-np.outer(K,A)@R
        if not (oos_lo<=t<oos_hi): prevb=beta[1]; continue
        want=pos
        if pos==0:
            if e>k*sq: want=-1
            elif e<-k*sq: want=1
        else:
            if (pos==1 and e>=0) or (pos==-1 and e<=0): want=0
        c=pos*(ry[t]-beta[1]*rx[t])
        if want!=pos: c-=fee*2
        if pos!=0:
            c-=FUND
            if rebal_cost: c-=fee*abs(beta[1]-prevb)   # подстройка x-ноги под дрейф β
        contrib.append(c); pos=want; prevb=beta[1]
    return np.array(contrib)

def metr(p):
    if len(p)==0 or p.std()==0: return 0,0,0
    eq=np.cumprod(1+p); peak=np.maximum.accumulate(eq)
    return (eq[-1]-1)*100, ((eq-peak)/peak).min()*100, p.mean()/p.std()*np.sqrt(BARS_YR)
def port(cs):
    if not cs: return np.zeros(1)
    L=min(len(c) for c in cs); return np.array([c[:L] for c in cs]).mean(axis=0)

def main():
    print(f"тяну 4H...",flush=True)
    data={s:fetch_4h(s+"-USDT-SWAP") for s in SYMS}
    data={s:d for s,d in data.items() if len(d)>=400}
    syms=sorted(data); common=sorted(set.intersection(*[set(data[s]) for s in syms]))
    px={s:np.log(np.array([data[s][t] for t in common])) for s in syms}
    N=len(common); half=N//2; pairs=list(itertools.combinations(syms,2))
    # коинтеграция на 1-й половине
    pv={p:coint(px[p[0]][:half],px[p[1]][:half])[1] for p in pairs}
    gated=[p for p in pairs if pv[p]<0.05]
    control=sorted(pairs,key=lambda p:-pv[p])[:len(gated)]   # самые НЕ-коинтегрированные, столько же
    print(f"коинтегр {len(gated)} пар; контроль (не-коинт) {len(control)} пар\n")

    D,K=1e-5,1.5
    print(f"=== УДАРЫ (δ{D:.0e}/k{K}) ===")
    print(f"  {'сценарий':40}{'total':>9}{'maxDD':>9}{'Sharpe':>9}")
    def show(tag,cs):
        t,d,s=metr(port(cs)); print(f"  {tag:40}{t:+8.1f}%{d:8.1f}%{s:+8.2f}")
    # удар 1: ребаланс-кост, мейкер
    show("коинтегр БЕЗ ребаланс-коста (как было)", [kalman(px[a],px[b],D,K,half,N,0.0002,False) for a,b in gated])
    show("коинтегр + РЕБАЛАНС-КОСТ (мейкер)",       [kalman(px[a],px[b],D,K,half,N,0.0002,True) for a,b in gated])
    show("коинтегр + ребаланс-кост (ТЕЙКЕР)",       [kalman(px[a],px[b],D,K,half,N,0.0005,True) for a,b in gated])
    # удар 2: контроль не-коинтегрированных (с ребаланс-костом, мейкер)
    show("КОНТРОЛЬ не-коинт + ребаланс (мейкер)",   [kalman(px[a],px[b],D,K,half,N,0.0002,True) for a,b in control])
    # удар 3: перевёрнутый сплит (OOS = 1-я половина; коинтеграция считаем на 2-й)
    pv2={p:coint(px[p[0]][half:],px[p[1]][half:])[1] for p in pairs}
    gated2=[p for p in pairs if pv2[p]<0.05]
    show(f"ПЕРЕВЁРНУТЫЙ сплит OOS=1я ({len(gated2)} пар, ребаланс)", [kalman(px[a],px[b],D,K,0,half,0.0002,True) for a,b in gated2])
    print(f"\n  Реально, если: коинт+ребаланс остаётся +, коинт >> контроль, и перевёрнутый сплит тоже +.")

if __name__=="__main__":
    main()
