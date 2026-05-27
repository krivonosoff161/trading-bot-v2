# -*- coding: utf-8 -*-
"""
hunt_trend_v2.py — кандидат №2: ПРАВИЛЬНОЕ трендследование (TS-momentum, vol-targeted, L/S). Read-only.

Crude Donchian/SMA пал в медведе. Правильная версия: сигнал = знак трейлинг-доходности; РАЗМЕР ∝ 1/vol
(risk-parity, vol-targeting — крупно-волатильные не доминируют); LONG И SHORT (шорты профитят в −58% медведе).
Меряю стэндалон-EV + КОРРЕЛЯЦИЮ с якорем (стат-арб). Трендследование ⊥ mean-reversion → если +EV, идеальный 2-й боец.
Якорь НЕ трогаю — только читаю его дневной поток через statarb_daily().
"""
import sys, io, json, time, urllib.request
from datetime import datetime
import numpy as np
from hunt_portfolio_blend import statarb_daily   # импорт оборачивает stdout utf-8
UNIV=["BTC","ETH","SOL","XRP","DOGE","ADA","BNB","LINK","AVAX","LTC","BCH","DOT","TRX","ATOM","FIL",
      "UNI","NEAR","APT","ARB","OP","INJ","SUI","SEI","TIA","RUNE","AAVE","ALGO","GALA","SAND","CRV"]
FEE=0.0005

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

def trend_stream(data, look, str_thr):
    syms=sorted(data); common=sorted(set.intersection(*[set(data[s]) for s in syms]))
    P=np.array([[data[s][t] for s in syms] for t in common]); nd,nc=P.shape
    rets=np.zeros((nd,nc)); rets[1:]=P[1:]/P[:-1]-1
    out={}
    for t in range(look+21,nd-1):
        mom=P[t]/P[t-look]-1                       # трейлинг-доходность (сигнал)
        vol=np.array([rets[max(0,t-20):t,i].std() or 1e9 for i in range(nc)])  # 20д vol для нормировки
        sign=np.sign(mom)
        sign[np.abs(mom)<str_thr]=0                # фильтр силы тренда
        w=sign/vol                                 # risk-parity
        gw=np.sum(np.abs(w))
        if gw==0: continue
        w=w/gw                                     # gross 1x
        ret=np.sum(w*rets[t+1])                    # доход на след. день
        out[day(common[t])]=ret                    # комса учтём грубо ниже
    return out

def main():
    print("тяну дневки...",flush=True)
    data={}
    for s in UNIV:
        d=fetch_1d(s+"-USDT-SWAP")
        if len(d)>=200: data[s]=d
    print(f"загружено {len(data)} монет",flush=True)
    print("читаю поток якоря (стат-арб)...",flush=True); sa=statarb_daily()

    print(f"\n=== ТРЕНД v2 (vol-targeted L/S risk-parity), стэндалон + корреляция с якорем ===")
    print(f"  {'параметры':22}{'total':>9}{'maxDD':>9}{'Sharpe':>9}{'corr→якорь':>12}")
    best=None
    for look in (20,40,80):
        for thr in (0.0,0.10):
            tr=trend_stream(data,look,thr)
            common=sorted(set(tr)&set(sa))
            if len(common)<40: continue
            A=np.array([tr[d] for d in common])
            # грубая комса: трендовый портфель ~ребаланс ежедневно, заложим FEE*оборот≈FEE*0.3/день
            A=A-FEE*0.3
            t1,d1,s1=met(A)
            B=np.array([sa[d] for d in common])
            corr=np.corrcoef(A,B)[0,1] if A.std()>0 and B.std()>0 else 0
            tag=f"look{look}/thr{thr}"
            print(f"  {tag:22}{t1:+8.1f}%{d1:8.1f}%{s1:+8.2f}{corr:+11.2f}")
            if t1>0 and (best is None or s1>best[1]): best=(tag,s1,corr,common,A,B)
    if best:
        tag,s1,corr,common,A,B=best
        wa=1/(B.std() or 1); wb=1/(A.std() or 1); s=wa+wb
        bl=(wa*B+wb*A)/s; tb,db,sb=met(bl); _,_,sba=met(B)
        print(f"\n  ЛУЧШИЙ +трендовый: {tag} (Sharpe {s1:+.2f}, corr {corr:+.2f})")
        print(f"  БЛЕНД якорь+тренд: Sharpe {sb:+.2f} (якорь один {sba:+.2f}) — {'помогает' if sb>sba else 'не помогает'}")
    else:
        print(f"\n  Прибыльной трендовой конфигурации не найдено (в этом медведе/юниверсе) — тренд не 2-й боец.")
    print(f"  Цель: трендовый +EV И corr с якорем ~0/минус → тогда бленд реально глаже.")

if __name__=="__main__":
    main()
