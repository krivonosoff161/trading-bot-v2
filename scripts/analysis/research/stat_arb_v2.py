# -*- coding: utf-8 -*-
"""
stat_arb_v2.py — ПРАВИЛЬНЫЙ стат-арб (по спеке агента A). Read-only.

Чинит лумпи/cost-fragile crude-версии тремя несущими частями:
  1) ГЕЙТ КОИНТЕГРАЦИИ (statsmodels.coint, Engle-Granger) — отбор на ФОРМАЦИИ, не корреляция.
  2) Окно z = HALF-LIFE (OU) + фильтр tradeable half-life — лекарство от лумпи.
  3) β из формации (frozen, причинно на OOS). Стопы |z|>=3 + time-stop 2.5×HL. Cost на каждую ногу.
Отбор пар на ФОРМАЦИИ (1-я половина) → тест на OOS (2-я половина, невидимая при отборе).
СРАВНЕНИЕ: с гейтом vs без — устраняет ли коинтеграция лумпи и даёт ли реальный OOS-эдж.
"""
import sys, io, json, time, urllib.request, itertools
import numpy as np
from statsmodels.tsa.stattools import coint
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
SYMS=["BTC","ETH","SOL","XRP","DOGE","ADA","BNB","LINK","AVAX","LTC","DOT","TRX","FIL","ATOM","UNI"]
ENTRY=2.0; EXIT=0.0; STOP=3.0; COINT_P=0.05; HL_MIN=6; HL_MAX=120; BARS_YR=6*365; FUND=0.00005

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

def eg_beta(la,lb):
    X=np.column_stack([np.ones(len(lb)),lb]); b=np.linalg.lstsq(X,la,rcond=None)[0]; return b[1]
def half_life(s):
    lag=s[:-1]; d=np.diff(s)
    X=np.column_stack([np.ones(len(lag)),lag]); b=np.linalg.lstsq(X,d,rcond=None)[0]; lam=b[1]
    return (-np.log(2)/lam) if lam<0 else None

def oos_contrib(la,lb,beta,W,HL,oos0,fee):
    """позиции только в OOS (idx>=oos0); z трейлинг, β frozen. вернёт массив бар-вкладов на OOS."""
    s=la-beta*lb; n=len(s); ri=np.diff(np.exp(la))/np.exp(la)[:-1]; rj=np.diff(np.exp(lb))/np.exp(lb)[:-1]
    contrib=[]; pos=0; bars_in=0; tstop=int(2.5*HL)
    for t in range(oos0,n-1):
        if t<W: contrib.append(0.0); continue
        mu=s[t-W:t].mean(); sd=s[t-W:t].std(); z=(s[t]-mu)/sd if sd>0 else 0
        want=pos
        if pos==0:
            if z>ENTRY: want=-1; bars_in=0
            elif z<-ENTRY: want=1; bars_in=0
        else:
            bars_in+=1
            if abs(z)<=EXIT or abs(z)>STOP or (pos==1 and z>0) or (pos==-1 and z<0) or bars_in>tstop: want=0
        c=pos*(ri[t]-rj[t])
        if want!=pos: c-=fee*2
        if pos!=0: c-=FUND
        contrib.append(c); pos=want
    return np.array(contrib)

def metrics(port):
    if len(port)==0 or port.std()==0: return 0,0,0
    eq=np.cumprod(1+port); peak=np.maximum.accumulate(eq)
    return (eq[-1]-1)*100, ((eq-peak)/peak).min()*100, port.mean()/port.std()*np.sqrt(BARS_YR)

def lumpiness(port, k=3):
    n=len(port)//k; rets=[]
    for w in range(k):
        seg=port[w*n:(w+1)*n] if w<k-1 else port[w*n:]
        rets.append((np.prod(1+seg)-1)*100)
    return rets

def main():
    print(f"тяну 4H по {len(SYMS)} майорам...",flush=True)
    data={}
    for s in SYMS:
        d=fetch_4h(s+"-USDT-SWAP")
        if len(d)>=400: data[s]=d
    syms=sorted(data); common=sorted(set.intersection(*[set(data[s]) for s in syms]))
    px={s:np.log(np.array([data[s][t] for t in common])) for s in syms}
    N=len(common); form=N//2; oos0=form
    print(f"загружено {len(syms)}, общих баров {N} (формация {form}, OOS {N-form})\n")

    pairs=list(itertools.combinations(syms,2))
    gated=[]; selinfo={}
    for a,b in pairs:
        la,lb=px[a][:form],px[b][:form]
        try: _,pval,_=coint(la,lb)
        except Exception: continue
        beta=eg_beta(la,lb); hl=half_life(la-beta*lb)
        if pval<COINT_P and hl and HL_MIN<=hl<=HL_MAX:
            gated.append((a,b)); selinfo[(a,b)]=(beta,hl,pval)
    print(f"=== ГЕЙТ КОИНТЕГРАЦИИ: {len(gated)}/{len(pairs)} пар прошли (p<{COINT_P}, half-life {HL_MIN}-{HL_MAX} баров) ===")
    if gated:
        print("    отобранные:", ", ".join(f"{a}/{b}(hl{selinfo[(a,b)][1]:.0f})" for a,b in gated[:14]))

    for tag,fee in [("мейкер 0.02%",0.0002),("тейкер 0.05%",0.0005)]:
        # GATED портфель
        cg=[]
        for a,b in gated:
            beta,hl,_=selinfo[(a,b)]; W=int(np.clip(round(hl),10,80))
            cg.append(oos_contrib(px[a],px[b],beta,W,hl,oos0,fee))
        # БЕЗ гейта (все пары, дефолт W=42, HL=42)
        cn=[]
        for a,b in pairs:
            beta=eg_beta(px[a][:form],px[b][:form])
            cn.append(oos_contrib(px[a],px[b],beta,42,42,oos0,fee))
        L=min(len(x) for x in cn)
        def port(cs):
            cs=[x[:L] for x in cs]; return np.array(cs).mean(axis=0) if cs else np.zeros(L)
        pg=port(cg); pn=port(cn)
        tg,dg,sg=metrics(pg); tn,dn,sn=metrics(pn)
        print(f"\n--- {tag} (OOS) ---")
        print(f"  С ГЕЙТОМ ({len(gated)} пар):  total {tg:+.1f}%  maxDD {dg:.1f}%  Sharpe {sg:+.2f}  | окна {['%+.1f'%x for x in lumpiness(pg)]}")
        print(f"  БЕЗ гейта ({len(pairs)} пар):  total {tn:+.1f}%  maxDD {dn:.1f}%  Sharpe {sn:+.2f}  | окна {['%+.1f'%x for x in lumpiness(pn)]}")
    print(f"\n  Гейт работает, если С ГЕЙТОМ Sharpe выше И окна ровнее (лумпи меньше), чем БЕЗ.")
    print(f"  Это прямой тест тезиса агента: коинтеграция+half-life лечат лумпи crude-версии.")

if __name__=="__main__":
    main()
