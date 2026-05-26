# -*- coding: utf-8 -*-
"""
hunt_cross_sectional.py — ОХОТА: cross-sectional momentum long-short (нейтрально). Read-only.

Лучше всего задокументированный крипто-фактор (Liu-Tsyvinski JoF; Starkiller 30д/7д). Ранжируем вселенную
по прошлой доходности, ЛОНГ топ-терциль / ШОРТ низ-терциль, равновес, дельта-нейтрально → убирает −58% бету.
Дневки OKX (~год), ребаланс 7д, skip последний день, комса на оборот. Метрики: total/Sharpe/maxDD + БЕТА к BTC.
Сравнение long-short (нейтр) vs long-only (несёт бету) + walk-forward. Гейт: + на OOS, beta≈0, Sharpe>1.
"""
import sys, io, json, time, urllib.request
import numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
UNIV=["BTC","ETH","SOL","XRP","DOGE","ADA","BNB","LINK","AVAX","LTC","BCH","DOT","TRX","TON","NEAR",
      "APT","ARB","OP","SUI","INJ","SEI","TIA","ATOM","FIL","RUNE","AAVE","UNI","PEPE","FLOKI","GALA",
      "SAND","CRV","LDO","ALGO","EGLD","FTM","GMT","CHZ"]
REBAL=7; SKIP=1

def fetch_1d(inst, want=440):
    out={}; cursor=int(time.time()*1000)
    for _ in range(8):
        url=f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar=1Dutc&after={cursor}&limit=100"
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

def sharpe(r): return r.mean()/r.std()*np.sqrt(365) if len(r)>1 and r.std()>0 else 0
def maxdd(eq): peak=np.maximum.accumulate(eq); return ((eq-peak)/peak).min()*100

def run(P, dates, look, fee, mode):
    """P: [ndays, ncoins] цены (выровнены). вернёт daily returns портфеля."""
    nd,nc=P.shape; ret=np.zeros(nd)
    cur_long=set(); cur_short=set()
    for t in range(look+1,nd):
        if (t-(look+1))%REBAL==0:
            sig=P[t-SKIP]/P[t-look]-1                     # доходность за окно (skip посл. день)
            valid=np.where(np.isfinite(sig))[0]
            if len(valid)>=9:
                order=valid[np.argsort(sig[valid])]
                k=max(1,len(valid)//3)
                cur_short=set(order[:k].tolist()); cur_long=set(order[-k:].tolist())
        if not cur_long: continue
        day=P[t]/P[t-1]-1
        rl=np.nanmean([day[i] for i in cur_long if np.isfinite(day[i])])
        rs=np.nanmean([day[i] for i in cur_short if np.isfinite(day[i])])
        if mode=="LS": ret[t]=rl-rs
        else: ret[t]=rl                                    # long-only
        if (t-(look+1))%REBAL==0: ret[t]-= (fee*2 if mode=="LS" else fee)  # оборот на ребалансе
    return ret[look+2:]

def main():
    print(f"тяну дневки по {len(UNIV)} монетам...",flush=True)
    data={}
    for s in UNIV:
        d=fetch_1d(s+"-USDT-SWAP")
        if len(d)>=200: data[s]=d
    syms=sorted(data); common=sorted(set.intersection(*[set(data[s]) for s in syms]))
    P=np.array([[data[s][t] for s in syms] for t in common])    # [ndays, ncoins]
    btc=np.array([data["BTC"][t] for t in common]) if "BTC" in data else None
    print(f"загружено {len(syms)} монет, общих дней {len(common)}\n")

    for look in (20,30):
        print(f"=== 30д≈{look}-day lookback, ребаланс {REBAL}д, терцили, $1000 ===")
        for tag,fee in [("мейкер 0.02%",0.0002),("тейкер 0.05%",0.0005)]:
            ls=run(P,common,look,fee,"LS"); lo=run(P,common,look,fee,"LO")
            eqls=np.cumprod(1+ls); eqlo=np.cumprod(1+lo)
            # beta long-short к BTC
            btcr=np.diff(btc)/btc[:-1] if btc is not None else None
            beta=np.nan
            if btcr is not None:
                m=min(len(ls),len(btcr)); x=btcr[-m:]; y=ls[-m:]
                if x.std()>0: beta=np.cov(x,y)[0,1]/x.var()
            # walk-forward половины
            h=len(ls)//2
            r1=(np.prod(1+ls[:h])-1)*100; r2=(np.prod(1+ls[h:])-1)*100
            print(f"  [{tag}] LONG-SHORT: total {(eqls[-1]-1)*100:+.1f}%  Sharpe {sharpe(ls):+.2f}  maxDD {maxdd(eqls):.1f}%  beta→BTC {beta:+.2f}  | 1я{r1:+.0f}% 2я{r2:+.0f}%")
            print(f"           LONG-ONLY:  total {(eqlo[-1]-1)*100:+.1f}%  Sharpe {sharpe(lo):+.2f}  maxDD {maxdd(eqlo):.1f}%  (несёт бету медведя)")
        print()
    print("  Гейт: LONG-SHORT в + на ОБЕИХ половинах, Sharpe>1, beta≈0 (нейтрально) = реальный кандидат.")
    print("  Если LS≈0/минус, а long-only глубокий минус — фактор не работает на нашем юниверсе/периоде.")

if __name__=="__main__":
    main()
