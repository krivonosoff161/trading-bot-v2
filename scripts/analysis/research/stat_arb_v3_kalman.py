# -*- coding: utf-8 -*-
"""
stat_arb_v3_kalman.py — рычаг 1: стат-арб с ДИНАМИЧЕСКИМ хеджем Kalman (спека агента A). Read-only.

v2 брал frozen β (протухает за 9мес OOS, −3.4%). Kalman = причинный online β, без look-ahead, сам калибрует
пороги через forecast-error e_t и √Q_t. Гейт коинтеграции на формации → Kalman на OOS → вход |e|>k·√Q, выход e→0.
Перебор δ (гладкость β) и k (порог). Сравнение с v2. Гейт: OOS net+ И ровнее (не лумпи).
"""
import sys, io, json, time, urllib.request, itertools
import numpy as np
from statsmodels.tsa.stattools import coint
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
SYMS=["BTC","ETH","SOL","XRP","DOGE","ADA","BNB","LINK","AVAX","LTC","DOT","TRX","FIL","ATOM","UNI"]
COINT_P=0.05; BARS_YR=6*365; FUND=0.00005

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

def kalman_signal(y, x, delta, k, oos0, fee):
    """Kalman online хедж: y=logA, x=logB. вернёт массив бар-вкладов на OOS."""
    n=len(y); Vw=delta/(1-delta)*np.eye(2); Ve=1e-3
    beta=np.zeros(2); R=np.zeros((2,2)); contrib=[]; pos=0
    ry=np.diff(np.exp(y))/np.exp(y)[:-1]; rx=np.diff(np.exp(x))/np.exp(x)[:-1]
    for t in range(n-1):
        A=np.array([1.0,x[t]]); R=R+Vw
        yhat=A@beta; e=y[t]-yhat; Q=A@R@A+Ve; sq=np.sqrt(max(Q,1e-12))
        K=(R@A)/Q; beta=beta+K*e; R=R-np.outer(K,A)@R
        if t<oos0: continue
        want=pos
        if pos==0:
            if e>k*sq: want=-1          # spread дорог → шорт спред
            elif e<-k*sq: want=1
        else:
            if (pos==1 and e>=0) or (pos==-1 and e<=0): want=0
        c=pos*(ry[t]-beta[1]*rx[t])
        if want!=pos: c-=fee*2
        if pos!=0: c-=FUND
        contrib.append(c); pos=want
    return np.array(contrib)

def metrics(p):
    if len(p)==0 or p.std()==0: return 0,0,0
    eq=np.cumprod(1+p); peak=np.maximum.accumulate(eq)
    return (eq[-1]-1)*100, ((eq-peak)/peak).min()*100, p.mean()/p.std()*np.sqrt(BARS_YR)
def windows(p,k=3):
    n=len(p)//k; return [ (np.prod(1+(p[w*n:(w+1)*n] if w<k-1 else p[w*n:]))-1)*100 for w in range(k)]

def main():
    print(f"тяну 4H по {len(SYMS)} майорам...",flush=True)
    data={}
    for s in SYMS:
        d=fetch_4h(s+"-USDT-SWAP")
        if len(d)>=400: data[s]=d
    syms=sorted(data); common=sorted(set.intersection(*[set(data[s]) for s in syms]))
    px={s:np.log(np.array([data[s][t] for t in common])) for s in syms}
    N=len(common); form=N//2; oos0=form
    pairs=list(itertools.combinations(syms,2))
    gated=[p for p in pairs if (lambda r: r[1]<COINT_P)(coint(px[p[0]][:form],px[p[1]][:form]))]
    print(f"загружено {len(syms)}, баров {N}, гейт коинтеграции: {len(gated)}/{len(pairs)} пар (формация)\n")
    print(f"=== KALMAN стат-арб (OOS), сравнение с v2 frozen-β (был −3.4%/Sharpe−0.48 мейкер) ===")
    print(f"  {'δ / k':>14}{'комса':>10}{'total':>9}{'maxDD':>9}{'Sharpe':>9}  окна")
    for delta in (1e-5,1e-4,1e-3):
        for k in (1.0,1.5):
            for tag,fee in [("мейкер",0.0002),("тейкер",0.0005)]:
                cs=[]
                for a,b in gated:
                    cs.append(kalman_signal(px[a],px[b],delta,k,oos0,fee))
                L=min(len(c) for c in cs) if cs else 0
                if not L: continue
                port=np.array([c[:L] for c in cs]).mean(axis=0)
                tot,dd,sh=metrics(port)
                print(f"  δ{delta:.0e}/k{k:<4}{tag:>10}{tot:+8.1f}%{dd:8.1f}%{sh:+8.2f}  {['%+.1f'%x for x in windows(port)]}")
        print()
    print("  Kalman работает, если бьёт v2 (−3.4%) И выходит в OOS-плюс с ровными окнами.")

if __name__=="__main__":
    main()
