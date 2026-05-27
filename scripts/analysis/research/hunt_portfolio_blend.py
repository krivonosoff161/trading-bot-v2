# -*- coding: utf-8 -*-
"""
hunt_portfolio_blend.py — МУЛЬТИ-СТРАТЕГНЫЙ портфель (идея трейдера). Read-only.

Не класть всё в один якорь. Свожу 2 нейтральные стратегии к ДНЕВНОМУ потоку на общем окне:
  - Kalman стат-арб (4H→дневной агрегат), коинтеграция-гейт.
  - Cross-sectional momentum LS (дневной).
Меряю стэндалон-метрики, КОРРЕЛЯЦИЮ потоков и vol-таргет (risk-parity) бленд. Цель: бленд глаже якоря
(выше Sharpe / ниже DD) при низкой корреляции = диверсификация работает.
"""
import sys, io, json, time, urllib.request, itertools
from datetime import datetime
import numpy as np
from statsmodels.tsa.stattools import coint
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
MAJ=["BTC","ETH","SOL","XRP","DOGE","ADA","BNB","LINK","AVAX","LTC","DOT","TRX","FIL","ATOM","UNI"]
UNIV=["BTC","ETH","SOL","XRP","DOGE","ADA","BNB","LINK","AVAX","LTC","BCH","DOT","TRX","ATOM","FIL",
      "UNI","NEAR","APT","ARB","OP","INJ","SUI","SEI","TIA","RUNE","AAVE","ALGO","GALA","SAND","CRV"]
D=1e-5; KK=1.5; COINT_P=0.05

def fetch(inst,bar,want):
    out={}; cursor=int(time.time()*1000)
    for _ in range(22):
        url=f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar={bar}&after={cursor}&limit=100"
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
def day(ts): return datetime.utcfromtimestamp(ts/1000).strftime("%Y-%m-%d")

def statarb_daily():
    data={s:fetch(s+"-USDT-SWAP","4H",1600) for s in MAJ}
    data={s:d for s,d in data.items() if len(d)>=400}
    syms=sorted(data); common=sorted(set.intersection(*[set(data[s]) for s in syms]))
    px={s:np.log(np.array([data[s][t] for t in common])) for s in syms}
    N=len(common); half=N//2
    gated=[p for p in itertools.combinations(syms,2) if coint(px[p[0]][:half],px[p[1]][:half])[1]<COINT_P]
    Vw=D/(1-D)*np.eye(2); Ve=1e-3
    bybar={}  # ts -> list of contrib
    for a,b in gated:
        y,x=px[a],px[b]; beta=np.zeros(2); R=np.zeros((2,2)); pos=0; prevb=0.0
        ry=np.diff(np.exp(y))/np.exp(y)[:-1]; rx=np.diff(np.exp(x))/np.exp(x)[:-1]
        for t in range(N-1):
            A=np.array([1.0,x[t]]); R=R+Vw; e=y[t]-A@beta; Q=A@R@A+Ve; sq=np.sqrt(max(Q,1e-12))
            Kg=(R@A)/Q; beta=beta+Kg*e; R=R-np.outer(Kg,A)@R
            if t<half: prevb=beta[1]; continue
            want=pos
            if pos==0:
                if e>KK*sq: want=-1
                elif e<-KK*sq: want=1
            elif (pos==1 and e>=0) or (pos==-1 and e<=0): want=0
            c=pos*(ry[t]-beta[1]*rx[t])
            if want!=pos: c-=0.0002*2
            if pos!=0: c-=0.00005+0.0002*abs(beta[1]-prevb)
            bybar.setdefault(common[t],[]).append(c); pos=want; prevb=beta[1]
    # дневной агрегат: средн по парам в баре → компаунд по дню
    daily={}
    for ts in sorted(bybar):
        r=np.mean(bybar[ts]); daily.setdefault(day(ts),[]).append(r)
    return {d:(np.prod([1+x for x in v])-1) for d,v in daily.items()}

def crosssec_daily():
    data={s:fetch(s+"-USDT-SWAP","1Dutc",460) for s in UNIV}
    data={s:d for s,d in data.items() if len(d)>=200}
    syms=sorted(data); common=sorted(set.intersection(*[set(data[s]) for s in syms]))
    P=np.array([[data[s][t] for s in syms] for t in common]); nd,nc=P.shape
    look=30; REB=7; out={}
    cl=set(); cs=set()
    for t in range(look+2,nd):
        if (t-(look+2))%REB==0:
            sig=P[t-1]/P[t-look]-1; v=np.where(np.isfinite(sig))[0]
            if len(v)>=9:
                o=v[np.argsort(sig[v])]; k=max(1,len(v)//3); cs=set(o[:k]); cl=set(o[-k:])
        if not cl: continue
        d=P[t]/P[t-1]-1
        rl=np.nanmean([d[i] for i in cl]); rs=np.nanmean([d[i] for i in cs])
        r=rl-rs
        if (t-(look+2))%REB==0: r-=0.0002*2
        out[day(common[t])]=r
    return out

def met(r):
    r=np.array(r)
    if len(r)==0 or r.std()==0: return 0,0,0
    eq=np.cumprod(1+r); pk=np.maximum.accumulate(eq)
    return (eq[-1]-1)*100, ((eq-pk)/pk).min()*100, r.mean()/r.std()*np.sqrt(365)

def main():
    print("строю поток стат-арба (4H→дневной)...",flush=True); sa=statarb_daily()
    print("строю поток cross-sectional (дневной)...",flush=True); cs=crosssec_daily()
    common=sorted(set(sa)&set(cs))
    print(f"\nобщее окно: {len(common)} дней ({common[0]}..{common[-1]})\n")
    A=np.array([sa[d] for d in common]); B=np.array([cs[d] for d in common])
    ta,da,sha=met(A); tb,db,shb=met(B)
    print(f"  {'стратегия':22}{'total':>9}{'maxDD':>9}{'Sharpe':>9}")
    print(f"  {'Kalman стат-арб':22}{ta:+8.1f}%{da:8.1f}%{sha:+8.2f}")
    print(f"  {'Cross-sectional LS':22}{tb:+8.1f}%{db:8.1f}%{shb:+8.2f}")
    corr=np.corrcoef(A,B)[0,1] if A.std()>0 and B.std()>0 else 0
    print(f"\n  КОРРЕЛЯЦИЯ потоков: {corr:+.2f}  ({'низкая → диверсификация работает' if abs(corr)<0.3 else 'высокая → дублируют'})")
    # risk-parity бленд (вес ∝ 1/vol)
    wa=1/(A.std() or 1); wb=1/(B.std() or 1); s=wa+wb; wa/=s; wb/=s
    bl=wa*A+wb*B; tbl,dbl,shbl=met(bl)
    print(f"\n  БЛЕНД risk-parity (вес стат-арб {wa:.2f} / cross-sec {wb:.2f}):")
    print(f"  {'ПОРТФЕЛЬ':22}{tbl:+8.1f}%{dbl:8.1f}%{shbl:+8.2f}")
    print(f"\n  Диверсификация работает, если Sharpe бленда > max(стэндалонов) ИЛИ DD бленда заметно ниже.")
    print(f"  (на общем окне ~{len(common)} дней; short-MR подключим, когда дотянем его на длинный период)")

if __name__=="__main__":
    main()
