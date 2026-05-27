# -*- coding: utf-8 -*-
"""
hunt_btcdom.py — третий боец: BTC-доминанс (лонг BTC / шорт корзина альтов). Read-only.

Драйвер — режим доминанса (в медведе альты падают быстрее BTC). Дельта-нейтрально (лонг одна нога/шорт другая).
Сигнал: dominance-momentum (BTC обгонял корзину за окно → продолжаем). Меряю стэндалон + корреляцию с ОБОИМИ
бойцами (якорь стат-арб, тренд) + 3-стратегный бленд. Цель: +EV И ⊥ обоим → третья нога делает портфель ещё глаже.
"""
import sys, io
import numpy as np
from hunt_trend_v3 import trend_ensemble, fetch_1d, statarb_daily, day, met, UNIV
FEE=0.0005

def btcdom_stream(data, look):
    syms=sorted(data)
    if "BTC" not in syms: return {}
    common=sorted(set.intersection(*[set(data[s]) for s in syms]))
    bi=syms.index("BTC"); P=np.array([[data[s][t] for s in syms] for t in common]); nd,nc=P.shape
    rets=np.zeros((nd,nc)); rets[1:]=P[1:]/P[:-1]-1
    others=[i for i in range(nc) if i!=bi]; out={}; pos=0
    for t in range(look+1,nd-1):
        dom_mom=(P[t,bi]/P[t-look,bi]-1) - np.mean([P[t,i]/P[t-look,i]-1 for i in others])
        want=1 if dom_mom>0 else -1     # +1 = лонг BTC/шорт альты
        btc_n=rets[t+1,bi]; basket_n=np.nanmean(rets[t+1,others])
        r=want*(btc_n-basket_n)
        if want!=pos: r-=FEE*2; pos=want
        out[day(common[t])]=float(r)
    return out

def main():
    print("тяну дневки...",flush=True)
    data={s:fetch_1d(s+"-USDT-SWAP") for s in UNIV}; data={s:d for s,d in data.items() if len(d)>=200}
    print(f"загружено {len(data)}",flush=True)
    print("якорь...",flush=True); sa=statarb_daily()
    print("тренд...",flush=True); tr=trend_ensemble(data)
    # лучший look для доминанса (короткий перебор, но сравним честно по корреляции/бленду)
    print(f"\n=== ТРЕТИЙ БОЕЦ: BTC-доминанс (дельта-нейтр), стэндалон + корреляции ===")
    print(f"  {'look':>6}{'total':>9}{'maxDD':>9}{'Sharpe':>9}{'corr→якорь':>12}{'corr→тренд':>12}")
    best=None
    for look in (10,20,40):
        bd=btcdom_stream(data,look)
        common=sorted(set(bd)&set(sa)&set(tr))
        if len(common)<40: continue
        C=np.array([bd[d] for d in common]); A=np.array([sa[d] for d in common]); B=np.array([tr[d]-FEE*0.3 for d in common])
        t1,d1,s1=met(C)
        ca=np.corrcoef(C,A)[0,1] if C.std()>0 else 0; cb=np.corrcoef(C,B)[0,1] if C.std()>0 else 0
        print(f"  {look:>6}{t1:+8.1f}%{d1:8.1f}%{s1:+8.2f}{ca:+11.2f}{cb:+11.2f}")
        if best is None or s1>best[0]: best=(s1,look,common,C,A,B)
    if best:
        s1,look,common,C,A,B=best
        # 3-стратегный risk-parity бленд
        wa=1/(A.std() or 1); wb=1/(B.std() or 1); wc=1/(C.std() or 1); s=wa+wb+wc
        bl3=(wa*A+wb*B+wc*C)/s; t3,d3,s3=met(bl3)
        bl2=(wa*A+wb*B)/(wa+wb); _,d2,s2=met(bl2)
        _,da,sa_=met(A)
        print(f"\n  Sharpe: якорь {sa_:+.2f} | бленд 2 (якорь+тренд) {s2:+.2f} | БЛЕНД 3 (+доминанс) {s3:+.2f}")
        print(f"  maxDD:  якорь {da:.1f}% | бленд2 {d2:.1f}% | бленд3 {d3:.1f}%")
        print(f"  Третий боец помогает, если бленд3 Sharpe > бленд2 ({s2:+.2f}) и/или DD ниже.")

if __name__=="__main__":
    main()
