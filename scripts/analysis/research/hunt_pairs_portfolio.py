# -*- coding: utf-8 -*-
"""
hunt_pairs_portfolio.py — итерация 2: ПОРТФЕЛЬ корзины пар (валидация лида). Read-only.

Selection не работает → торгуем ВСЕ пары равновесно как один портфель. Реалистично: мейкер-комса
(лимитки для MR), funding-драг, одна equity-кривая на $1000. Метрики: total%, maxDD, Sharpe.
Walk-forward 5 окон + чувствительность к комсе (мейкер 0.02 vs тейкер 0.05). Гейт: + в большинстве окон,
приемлемый DD, и не разваливается на тейкере целиком.
"""
import sys, io, json, time, urllib.request, itertools
import numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
SYMS=["BTC","ETH","SOL","XRP","DOGE","ADA","BNB","LINK","AVAX","LTC","DOT","TRX"]
W=42; ENTRY=2.0; EXIT=0.5; STOP=4.0
FUND=0.00005   # ~funding/slip драг за 4H-бар в позиции (консервативно)
BARS_YR=6*365

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

def pair_series(pi,pj,fee):
    """вернёт массивы (contrib[t]) = вклад пары в бар-доход портфеля (с комсой/funding)."""
    n=len(pi); s=np.log(pi)-np.log(pj)
    ri=np.diff(pi)/pi[:-1]; rj=np.diff(pj)/pj[:-1]
    contrib=np.zeros(n-1); pos=0
    for t in range(W,n-1):
        mu=s[t-W:t].mean(); sd=s[t-W:t].std(); z=(s[t]-mu)/sd if sd>0 else 0
        want=pos
        if pos==0:
            if z>ENTRY: want=-1
            elif z<-ENTRY: want=1
        else:
            if abs(z)<EXIT or abs(z)>STOP or (pos==1 and z>0) or (pos==-1 and z<0): want=0
        c=pos*(ri[t]-rj[t])
        if want!=pos: c-=fee*2
        if pos!=0: c-=FUND
        contrib[t]=c; pos=want
    return contrib

def portfolio(data, fee):
    syms=sorted(data)
    common=set.intersection(*[set(data[s]) for s in syms])
    ts=sorted(common)
    if len(ts)<W+60: return None
    px={s:np.array([data[s][t] for t in ts]) for s in syms}
    contribs=[]
    for a,b in itertools.combinations(syms,2):
        contribs.append(pair_series(px[a],px[b],fee))
    M=np.array(contribs)                  # [npairs, nbars]
    port=M.mean(axis=0)                   # равновесный портфель
    port=port[W:]                         # отрезать прогрев
    eq=np.cumprod(1+port)
    total=(eq[-1]-1)*100
    peak=np.maximum.accumulate(eq); dd=((eq-peak)/peak).min()*100
    sharpe=port.mean()/port.std()*np.sqrt(BARS_YR) if port.std()>0 else 0
    return total, dd, sharpe, port, len(ts)

def main():
    print(f"тяну 4H по {len(SYMS)} майорам (длинная история)...",flush=True)
    data={}
    for s in SYMS:
        d=fetch_4h(s+"-USDT-SWAP")
        if len(d)>=200: data[s]=d
    print(f"загружено {len(data)}\n")

    print(f"=== ПОРТФЕЛЬ корзины пар (равновес, {len(list(itertools.combinations(sorted(data),2)))} пар), $1000 ===")
    print(f"  {'комса/нога':>12}{'total':>9}{'maxDD':>9}{'Sharpe':>9}{'баров':>8}")
    res={}
    for tag,fee in [("мейкер 0.02%",0.0002),("0.05%",0.0005),("тейкер 0.10%",0.0010)]:
        r=portfolio(data,fee)
        if not r: print(f"  {tag:>12}  нет данных"); continue
        total,dd,sh,port,nb=r; res[fee]=r
        print(f"  {tag:>12}{total:+8.1f}%{dd:8.1f}%{sh:+8.2f}{nb:8}")

    # walk-forward 5 окон на мейкере
    if 0.0002 in res:
        port=res[0.0002][3]; k=len(port)//5
        print(f"\n=== WALK-FORWARD 5 окон (мейкер 0.02%) ===")
        pos_win=0
        for w in range(5):
            seg=port[w*k:(w+1)*k] if w<4 else port[w*k:]
            ret=(np.prod(1+seg)-1)*100
            if ret>0: pos_win+=1
            print(f"  окно {w+1}: {ret:+.1f}%")
        print(f"  → положительных окон: {pos_win}/5  {'✅ робастно' if pos_win>=4 else ('🟡 средне' if pos_win==3 else '❌ нет')}")
    print(f"\n  Гейт: + в 4-5/5 окон, DD приемлемый, Sharpe>1 на мейкере = реальный нейтральный кандидат.")
    print(f"  Оговорки: мейкер-филл не гарантирован; 1 биржа; ~{res.get(0.0002,[0,0,0,0,0])[4]} баров (~{res.get(0.0002,[0,0,0,0,0])[4]//6} дней) — не годы.")

if __name__=="__main__":
    main()
