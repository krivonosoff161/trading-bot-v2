# -*- coding: utf-8 -*-
"""
hunt_trend_v3.py — второй боец, ЧЕСТНАЯ версия: ансамбль окон (без cherry-pick). Read-only.

v2 выбрал лучшее окно (look80) = selection bias (агент предупредил). Канон (MOP/AQR): АНСАМБЛЬ lookback'ов
{7,14,30,60,90}, vol-нормировка сигнала + tanh, размер ∝1/vol, портфельный vol-target, L/S. Если ансамбль (не
подгонка) даёт + И корреляцию с якорем ~0 И бленд глаже — второй боец настоящий. Якорь читаю, не трогаю.
"""
import sys, io, json, time, urllib.request
from datetime import datetime
import numpy as np
from hunt_portfolio_blend import statarb_daily
UNIV=["BTC","ETH","SOL","XRP","DOGE","ADA","BNB","LINK","AVAX","LTC","BCH","DOT","TRX","ATOM","FIL",
      "UNI","NEAR","APT","ARB","OP","INJ","SUI","SEI","TIA","RUNE","AAVE","ALGO","GALA","SAND","CRV"]
FEE=0.0005; LOOKS=[7,14,30,60,90]; KTANH=1.75; STR_THR=0.10; LEV_CAP=2.0

def fetch_1d(inst, want=460):
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
        cursor=old; time.sleep(0.08)
    return out
def day(ts): return datetime.utcfromtimestamp(ts/1000).strftime("%Y-%m-%d")
def met(r):
    r=np.array(r)
    if len(r)==0 or r.std()==0: return 0,0,0
    eq=np.cumprod(1+r); pk=np.maximum.accumulate(eq)
    return (eq[-1]-1)*100, ((eq-pk)/pk).min()*100, r.mean()/r.std()*np.sqrt(365)

def trend_ensemble(data):
    syms=sorted(data); common=sorted(set.intersection(*[set(data[s]) for s in syms]))
    P=np.array([[data[s][t] for s in syms] for t in common]); nd,nc=P.shape
    rets=np.zeros((nd,nc)); rets[1:]=P[1:]/P[:-1]-1
    out={}; mx=max(LOOKS)
    for t in range(mx+21,nd-1):
        vol=np.array([rets[max(0,t-20):t,i].std()*np.sqrt(365) or 1e9 for i in range(nc)])
        # ансамбль: средн tanh(норм. трейлинг-доходности) по окнам
        sig=np.zeros(nc)
        for L in LOOKS:
            raw=(P[t]/P[t-L]-1)/(vol/np.sqrt(365/ L) + 1e-9)   # нормировка на vol соответств. горизонта
            sig+=np.tanh(raw/KTANH)
        sig/=len(LOOKS)
        sig[np.abs(sig)<STR_THR]=0
        w=sig/vol
        gw=np.sum(np.abs(w))
        if gw==0: continue
        w=w/gw*min(LEV_CAP,1.0)         # gross ~1x (консервативно)
        out[day(common[t])]=float(np.sum(w*rets[t+1]))
    return out

def main():
    print("тяну дневки...",flush=True)
    data={s:fetch_1d(s+"-USDT-SWAP") for s in UNIV}; data={s:d for s,d in data.items() if len(d)>=200}
    print(f"загружено {len(data)}",flush=True)
    print("читаю якорь...",flush=True); sa=statarb_daily()
    tr=trend_ensemble(data)
    common=sorted(set(tr)&set(sa))
    A=np.array([tr[d]-FEE*0.3 for d in common]); B=np.array([sa[d] for d in common])
    ta,da,sa_=met(A); tb,db,sb=met(B)
    corr=np.corrcoef(A,B)[0,1] if A.std()>0 and B.std()>0 else 0
    print(f"\n=== ТРЕНД v3 АНСАМБЛЬ (без cherry-pick), общее окно {len(common)} дней ===")
    print(f"  {'стратегия':22}{'total':>9}{'maxDD':>9}{'Sharpe':>9}")
    print(f"  {'Тренд-ансамбль':22}{ta:+8.1f}%{da:8.1f}%{sa_:+8.2f}")
    print(f"  {'Якорь (стат-арб)':22}{tb:+8.1f}%{db:8.1f}%{sb:+8.2f}")
    print(f"\n  КОРРЕЛЯЦИЯ тренд↔якорь: {corr:+.2f}")
    for wa_name,wa,wb in [("50/50",0.5,0.5),("risk-parity",1/(B.std() or 1),1/(A.std() or 1))]:
        s=wa+wb; bl=(wa*B+wb*A)/s; tbl,dbl,sbl=met(bl)
        print(f"  БЛЕНД {wa_name:12}: total {tbl:+.1f}%  maxDD {dbl:.1f}%  Sharpe {sbl:+.2f}")
    print(f"\n  Второй боец настоящий, если: ансамбль +EV (или хотя бы ~0), corr~0, и бленд Sharpe > якоря ({sb:+.2f}).")

if __name__=="__main__":
    main()
