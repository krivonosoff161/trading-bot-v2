# -*- coding: utf-8 -*-
"""
stat_arb_v5_optimize.py — углубление якоря: ROLLING walk-forward + переотбор пар + карта робастности. Read-only.

v4 показал «свежие окна слабеют». Гипотеза: пары отбирались ОДИН раз на формации и протухали. Здесь —
СКОЛЬЗЯЩИЙ walk-forward: каждое окно заново отбираем коинтегрированные пары на трейлинг-формации, торгуем
следующий кусок Kalman'ом, склеиваем непрерывный OOS. + карта робастности (δ × k): плато или пик?
Честная проверка «выжать максимум без оверфита».
"""
import sys, io, json, time, urllib.request, itertools
import numpy as np
from statsmodels.tsa.stattools import coint
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
SYMS=["BTC","ETH","SOL","XRP","DOGE","ADA","BNB","LINK","AVAX","LTC","BCH","DOT","TRX","ATOM","FIL",
      "UNI","NEAR","APT","ARB","OP","INJ","SUI","SEI","TIA","RUNE","AAVE","ALGO","GALA"]
COINT_P=0.05; FORM=300; TRADE=150; STEP=150; FEE=0.0002; FUND=0.00005; BARS_YR=6*365

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

def kalman_win(y,x,delta,k,f0,f1,t1):
    """Kalman c f0; торгуем только в [f1,t1); вернёт список бар-вкладов."""
    Vw=delta/(1-delta)*np.eye(2); Ve=1e-3; beta=np.zeros(2); R=np.zeros((2,2)); pos=0; prevb=0.0
    ry=np.diff(np.exp(y))/np.exp(y)[:-1]; rx=np.diff(np.exp(x))/np.exp(x)[:-1]
    out=[]
    for t in range(f0,min(t1,len(y)-1)):
        A=np.array([1.0,x[t]]); R=R+Vw; e=y[t]-A@beta; Q=A@R@A+Ve; sq=np.sqrt(max(Q,1e-12))
        Kg=(R@A)/Q; beta=beta+Kg*e; R=R-np.outer(Kg,A)@R
        if t<f1: prevb=beta[1]; continue
        want=pos
        if pos==0:
            if e>k*sq: want=-1
            elif e<-k*sq: want=1
        elif (pos==1 and e>=0) or (pos==-1 and e<=0): want=0
        c=pos*(ry[t]-beta[1]*rx[t])
        if want!=pos: c-=FEE*2
        if pos!=0: c-=FUND+FEE*abs(beta[1]-prevb)
        out.append(c); pos=want; prevb=beta[1]
    return out

def metr(p):
    p=np.array(p)
    if len(p)==0 or p.std()==0: return 0,0,0
    eq=np.cumprod(1+p); pk=np.maximum.accumulate(eq)
    return (eq[-1]-1)*100, ((eq-pk)/pk).min()*100, p.mean()/p.std()*np.sqrt(BARS_YR)

def main():
    print(f"тяну 4H по {len(SYMS)}...",flush=True)
    data={}
    for s in SYMS:
        d=fetch_4h(s+"-USDT-SWAP")
        if len(d)>=FORM+TRADE+10: data[s]=d
    syms=sorted(data); common=sorted(set.intersection(*[set(data[s]) for s in syms]))
    px={s:np.log(np.array([data[s][t] for t in common])) for s in syms}
    N=len(common); allp=list(itertools.combinations(syms,2))
    wins=[(s,s+FORM,s+FORM+TRADE) for s in range(0,N-FORM-TRADE+1,STEP)]
    print(f"монет {len(syms)}, баров {N}, окон walk-forward {len(wins)} (форма {FORM}/трейд {TRADE} 4H-баров)\n")
    print("переотбор коинтегрированных пар по окнам...",flush=True)
    winpairs=[]
    for f0,f1,t1 in wins:
        g=[(a,b) for a,b in allp if coint(px[a][f0:f1],px[b][f0:f1])[1]<COINT_P]
        winpairs.append((f0,f1,t1,g))
    print("  пар по окнам:", [len(w[3]) for w in winpairs],"\n")
    print(f"=== РОБАСТНОСТЬ (rolling walk-forward, склеенный OOS, мейкер) ===")
    hdr="delta vs k"
    print(f"  {hdr:>10}{'k=1.0':>20}{'k=1.5':>20}{'k=2.0':>20}  (total/Sharpe/DD)")
    best=None
    for delta in (1e-5,3e-5,1e-4):
        row=[f"  δ{delta:.0e}".ljust(10)]
        for k in (1.0,1.5,2.0):
            stream=[]
            for f0,f1,t1,g in winpairs:
                if not g: continue
                bybar={}
                for a,b in g:
                    c=kalman_win(px[a],px[b],delta,k,f0,f1,t1)
                    for i,v in enumerate(c): bybar.setdefault(i,[]).append(v)
                stream+=[np.mean(bybar[i]) for i in sorted(bybar)]
            tot,dd,sh=metr(stream)
            row.append(f"{tot:+.0f}%/{sh:+.1f}/{dd:.0f}%".rjust(20))
            if best is None or sh>best[0]: best=(sh,delta,k,tot,dd,stream)
        print("".join(row))
    sh,delta,k,tot,dd,stream=best
    # стабильность по окнам у лучшего
    nwin=len(wins); per=[]; off=0
    for f0,f1,t1,g in winpairs:
        ln=max(0,min(t1,N-1)-f1)
        seg=stream[off:off+ln]; off+=ln
        if seg: per.append((np.prod([1+x for x in seg])-1)*100)
    print(f"\n  ЛУЧШИЙ (робастный): δ{delta:.0e}/k{k} → total {tot:+.0f}%, Sharpe {sh:+.2f}, maxDD {dd:.0f}%")
    print(f"  по окнам walk-forward: {['%+.1f'%x for x in per]}  ({sum(1 for x in per if x>0)}/{len(per)} +)")
    print(f"\n  Якорь силён, если плато (соседние δ/k близки) И окна walk-forward ровные (переотбор лечит фейдинг).")

if __name__=="__main__":
    main()
