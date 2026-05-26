# -*- coding: utf-8 -*-
"""
hunt_pairs_statarb.py — ОХОТА: стат-арбитраж пар (рыночно-нейтрально). Read-only.

Вселенная в медвежьем рынке (−58% B&H) → лонг-смещённое обречено. Нейтральная пара плевать на дрейф.
Тянем 4H OKX по майорам, для каждой пары: log-спред, ПРИЧИННЫЙ rolling z-score (трейлинг окно), вход на
расхождении (|z|>entry), выход на возврате (|z|<exit), стоп |z|>stop. Дельта-нейтрально, $1000, комса 4 филла/раунд.
Walk-forward: 1-я/2-я половина. Гейт: + на капитал И + в ОБЕИХ половинах.
"""
import sys, io, json, time, urllib.request, itertools
import numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
FEE=0.0005; CAP=1000.0; LEG=500.0
W=42; ENTRY=2.0; EXIT=0.5; STOP=4.0   # окно z ~7 дней (4H), пороги
SYMS=["BTC","ETH","SOL","XRP","DOGE","ADA","BNB","LINK","AVAX","LTC","DOT","TRX"]

def fetch_4h(inst, want=1400):
    out={}; cursor=int(time.time()*1000)
    for _ in range(18):
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
    return [v for _,v in sorted(out.items())]

def sim_pair(pi, pj):
    """вернёт (ret_%_total, ret_1h, ret_2h, n_trades, wr)."""
    n=min(len(pi),len(pj)); pi=np.array(pi[-n:]); pj=np.array(pj[-n:])
    if n<W+40: return None
    s=np.log(pi)-np.log(pj)
    ri=np.diff(pi)/pi[:-1]; rj=np.diff(pj)/pj[:-1]      # бар-возвраты
    eq=1.0; pos=0; trades=0; wins=0; entry_eq=1.0; eqs=[1.0]
    for t in range(W, n-1):
        mu=s[t-W:t].mean(); sd=s[t-W:t].std()
        z=(s[t]-mu)/sd if sd>0 else 0
        want=pos
        if pos==0:
            if z>ENTRY: want=-1            # шорт спред (шорт i, лонг j)
            elif z<-ENTRY: want=1
        else:
            if abs(z)<EXIT or abs(z)>STOP or (pos==1 and z>0) or (pos==-1 and z<0): want=0
        if want!=pos:
            eq*=(1-FEE*2)                  # смена = 2 ноги × комса
            if pos!=0:                     # закрыли сделку
                trades+=1
                if eq>entry_eq: wins+=1
            if want!=0: entry_eq=eq
            pos=want
        # PnL за бар t->t+1: long spread = +(ri - rj)
        pnl = pos*(ri[t]-rj[t])
        eq*=(1+pnl); eqs.append(eq)
    half=len(eqs)//2
    r1=(eqs[half]/eqs[0]-1)*100; r2=(eqs[-1]/eqs[half]-1)*100
    wr=100*wins/trades if trades else 0
    return (eq-1)*100, r1, r2, trades, wr

def main():
    print(f"[1/2] тяну 4H OKX по {len(SYMS)} майорам...",flush=True)
    data={}
    for s in SYMS:
        cl=fetch_4h(s+"-USDT-SWAP")
        if len(cl)>=200: data[s]=cl
    print(f"      загружено {len(data)} монет, медиана {int(np.median([len(v) for v in data.values()]))} баров (4H)\n")
    rows=[]
    for a,b in itertools.combinations(sorted(data),2):
        r=sim_pair(data[a],data[b])
        if r: rows.append((f"{a}/{b}",)+r)
    print(f"=== ОХОТА: стат-арб пар (нейтрально), 4H, z[{W}] entry{ENTRY}/exit{EXIT}/stop{STOP}, ${CAP}, комса {FEE*100}%×2/смена ===")
    print(f"  {'пара':12}{'весь':>9}{'1-я пол':>9}{'2-я пол':>9}{'сделок':>8}{'WR':>6}  робаст")
    robust=[]
    for name,tot,r1,r2,tr,wr in sorted(rows,key=lambda x:-x[1]):
        rob = tot>0 and r1>0 and r2>0 and tr>=8
        if rob: robust.append(name)
        flag="✅" if rob else ""
        print(f"  {name:12}{tot:+8.1f}%{r1:+8.1f}%{r2:+8.1f}%{tr:8}{wr:5.0f}% {flag}")
    print(f"\n  РОБАСТНЫЕ пары (+ весь период И + обе половины, n>=8): {robust if robust else 'НЕТ'}")
    print(f"  (нейтрально = не зависит от −58% медведя; если робастных пар нет — и тут эджа нет)")

if __name__=="__main__":
    main()
