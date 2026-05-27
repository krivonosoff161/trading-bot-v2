# -*- coding: utf-8 -*-
"""
positioning_alpha.py — НОВЫЙ бассейн: данные, которых у майна НЕТ (OI/LS/taker). Read-only PROBE.

Майн видит только цену. Крипто-ходы ведёт ПОЗИЦИОНИРОВАНИЕ. OKX rubik отдаёт ~2 последних дня 5m:
  ΔOpen-Interest, long/short account ratio, taker buy/sell imbalance, funding.
Тест: есть ли у них значимый IC к будущему ходу, КОТОРОГО НЕТ в цене (partial corr контролируя цен-моментум).
Если да даже на 2 днях → ставим форвард-сбор (исторически их нет). Если нет → и этот бассейн пуст.
"""
import sys, io, json, time, urllib.request
import numpy as np
from scipy.stats import spearmanr
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
CCYS=["BTC","ETH","SOL","XRP","DOGE","ADA","BNB","LINK","AVAX","LTC","DOT","TRX","SUI","APT","NEAR"]

def get(url):
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req,timeout=15) as r: return json.loads(r.read())
    except Exception: return {}
def series(url,vmap):
    j=get(url); d=j.get("data") or []
    return {int(r[0]):vmap(r) for r in d}

def main():
    Z_oi=[];Z_ls=[];Z_tk=[];Z_pm=[];F3=[]   # фичи + цен-моментум + forward
    for c in CCYS:
        inst=f"{c}-USDT-SWAP"
        oi=series(f"https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume?ccy={c}&period=5m",lambda r:float(r[1]))
        ls=series(f"https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy={c}&period=5m",lambda r:float(r[1]))
        tk=series(f"https://www.okx.com/api/v5/rubik/stat/taker-volume?ccy={c}&instType=SPOT&period=5m",lambda r:(float(r[2])-float(r[1]))/((float(r[1])+float(r[2])) or 1e-9))
        # цена: 5m свечи (recent)
        jc=get(f"https://www.okx.com/api/v5/market/candles?instId={inst}&bar=5m&limit=300")
        pc={int(r[0]):float(r[4]) for r in (jc.get("data") or [])}
        time.sleep(0.1)
        ts=sorted(set(oi)&set(ls)&set(pc))                     # ядро: OI+LS+цена (taker опционально)
        if len(ts)<100: continue
        for i in range(6,len(ts)-4):
            t=ts[i]; tp=ts[i-3]; tf=ts[i+3]
            doi=(oi[t]-oi[ts[i-1]])/(oi[ts[i-1]] or 1e-9)*100
            pm=(pc[t]-pc[tp])/pc[tp]*100                       # цен-моментум за 3 бара (что майн и видит)
            f3=(pc[tf]-pc[t])/pc[t]*100
            Z_oi.append(doi); Z_ls.append(ls[t]); Z_tk.append(tk.get(t,np.nan)); Z_pm.append(pm); F3.append(f3)
    n=len(F3)
    print(f"=== PROBE ПОЗИЦИОНИРОВАНИЯ: {n} точек (~2 дня × {len(CCYS)} монет) ===\n")
    if n<200: print("мало данных"); return
    F3=np.array(F3); Z_oi=np.array(Z_oi); Z_ls=np.array(Z_ls); Z_tk=np.array(Z_tk); Z_pm=np.array(Z_pm)
    def ic(x,y,nm):
        m=np.isfinite(x)&np.isfinite(y); xx,yy=x[m],y[m]
        r=spearmanr(xx,yy)[0]; t=r*np.sqrt((len(xx)-2)/(1-r**2+1e-12))
        print(f"  {nm:22} IC={r:+.3f}  t={t:+.1f}  {'ЗНАЧИМ' if abs(t)>3 else 'шум'}")
        return r
    print("IC к будущему ходу (fwd3):")
    ic(Z_oi,F3,"ΔOI")
    ic(Z_ls,F3,"long/short ratio")
    ic(Z_tk,F3,"taker buy-sell imbal")
    rpm=ic(Z_pm,F3,"цен-моментум(майн видит)")
    print(f"\nPARTIAL — добавляют ли позиц-данные инфу СВЕРХ цены (контроль цен-моментума):")
    def pcorr(x):
        m=np.isfinite(x)&np.isfinite(F3)&np.isfinite(Z_pm); X,Y,Zc=x[m],F3[m],Z_pm[m]
        rxy=spearmanr(X,Y)[0]; rxz=spearmanr(X,Zc)[0]; rzy=spearmanr(Zc,Y)[0]
        den=np.sqrt((1-rxz**2)*(1-rzy**2)); return (rxy-rxz*rzy)/den if den>0 else 0
    for nm,x in (("ΔOI",Z_oi),("long/short",Z_ls),("taker imbal",Z_tk)):
        print(f"  {nm:14} partial|цена = {pcorr(x):+.3f}")
    print(f"\n  Если позиц-фича значима И partial>0 (сверх цены) → НОВЫЙ сигнал, ставим форвард-сбор.")
    print(f"  Если всё шум — и этот бассейн пуст на ритейл-таймфрейме (но n=2 дня мал, это лишь probe).")

if __name__=="__main__":
    main()
