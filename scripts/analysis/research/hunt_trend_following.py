# -*- coding: utf-8 -*-
"""
hunt_trend_following.py — ОХОТА: трендследование на старших ТФ (managed-futures). Read-only.

Все наши провалы были на краткосроке. Деньги в крипте — в ТРЕНДАХ (крупные ходы, к которым скальп слеп).
Тянем дневки OKX (~год) по широкой вселенной, симулируем классические трендовые системы с $1000 + комса,
walk-forward (первая/вторая половина истории). Системы: Donchian breakout, SMA-cross, TS-momentum.
Цель: есть ли РОБАСТНЫЙ положительный возврат на капитал в другом классе.
"""
import sys, io, os, json, time, urllib.request
from datetime import datetime
import numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
FEE=0.0005   # 0.05%/сторона (OKX taker perp)
CAP=1000.0
UNIVERSE=["BTC","ETH","SOL","XRP","DOGE","ADA","BNB","LINK","AVAX","LTC","BCH","DOT",
          "TRX","TON","NEAR","APT","ARB","OP","SUI","INJ","SEI","TIA","LDO","ATOM","FIL","RUNE","AAVE","UNI"]

def fetch_1d(inst, want=420):
    out={}; cursor=int(time.time()*1000)
    for _ in range(8):
        url=f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar=1Dutc&after={cursor}&limit=100"
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req,timeout=15) as r: j=json.loads(r.read())
        except Exception: break
        data=j.get("data",[])
        if not data: break
        for row in data: out[int(row[0])]=float(row[4])  # close
        old=min(int(row[0]) for row in data)
        if old>=cursor or len(out)>=want: break
        cursor=old; time.sleep(0.12)
    items=sorted(out.items())
    return [v for _,v in items]

def sim(closes, signal_fn):
    """signal_fn(closes, i)->desired position {-1,0,1} (causal, на закрытии i торгуем по close[i]).
    вернёт (total_return%, n_trades, equity_list)."""
    pos=0; eq=1.0; eqs=[1.0]; trades=0
    for i in range(len(closes)-1):
        want=signal_fn(closes,i)
        if want!=pos:
            eq*=(1-FEE)            # переворот/смена = комса (упрощённо одна сторона за смену)
            trades+=1
            pos=want
        ret=(closes[i+1]-closes[i])/closes[i]
        eq*=(1+pos*ret)
        eqs.append(eq)
    return (eq-1)*100, trades, eqs

def donchian(n_in=20,n_out=10,allow_short=False):
    def f(c,i):
        if i<n_in: return 0
        hi=max(c[i-n_in:i]); lo=min(c[i-n_in:i]); lo_out=min(c[i-n_out:i])
        # состояние не храним — аппроксимация по уровням: long если выше n_in-макс, выход ниже n_out-мин
        if c[i]>hi: return 1
        if allow_short and c[i]<lo: return -1
        if c[i]<lo_out: return 0
        return None  # держим прежнюю — обработаем снаружи
    return f

def make_stateful(raw):
    """donchian возвращает None = держать; превратим в stateful позицию."""
    def f(c,i):
        v=raw(c,i);
        return v
    return f

def run_stateful(closes, raw_fn):
    pos=0; eq=1.0; trades=0
    for i in range(len(closes)-1):
        v=raw_fn(closes,i)
        want = pos if v is None else v
        if want!=pos: eq*=(1-FEE); trades+=1; pos=want
        ret=(closes[i+1]-closes[i])/closes[i]; eq*=(1+pos*ret)
    return (eq-1)*100, trades

def sma_cross(fast=20,slow=50,allow_short=False):
    def f(c,i):
        if i<slow: return 0
        sf=np.mean(c[i-fast:i]); ss=np.mean(c[i-slow:i])
        if sf>ss: return 1
        return -1 if allow_short else 0
    return f
def tsmom(n=30,allow_short=False):
    def f(c,i):
        if i<n: return 0
        if c[i]>c[i-n]: return 1
        return -1 if allow_short else 0
    return f
def buyhold():
    def f(c,i): return 1
    return f

def main():
    print(f"[1/2] тяну дневки OKX (~{420} дней) по {len(UNIVERSE)} монетам...",flush=True)
    data={}
    for s in UNIVERSE:
        cl=fetch_1d(s+"-USDT-SWAP")
        if len(cl)>=120: data[s]=cl
    print(f"      загружено: {len(data)} монет, медиана длины {int(np.median([len(v) for v in data.values()]))} дней\n",flush=True)

    systems=[
        ("Buy&Hold (база)", buyhold(), False),
        ("Donchian 20/10 LongOnly", donchian(20,10,False), True),
        ("Donchian 20/10 L/S", donchian(20,10,True), True),
        ("SMA 20/50 LongOnly", sma_cross(20,50,False), False),
        ("SMA 20/50 L/S", sma_cross(20,50,True), False),
        ("TS-mom 30d LongOnly", tsmom(30,False), False),
        ("TS-mom 30d L/S", tsmom(30,True), False),
    ]
    print(f"=== ОХОТА: трендследование, портфель equal-weight, ${CAP}, комса {FEE*100}%/смена ===")
    print(f"  {'система':26}{'весь период':>13}{'1-я пол':>10}{'2-я пол':>10}{'сделок/мон':>12}")
    for name,fn,stateful in systems:
        rets=[];r1=[];r2=[];tr=[]
        for s,cl in data.items():
            half=len(cl)//2
            if stateful:
                tot,t=run_stateful(cl,fn); _,_=0,0
                a,_=run_stateful(cl[:half],fn); b,_=run_stateful(cl[half:],fn)
            else:
                tot,t,_=sim(cl,fn)
                a,_,_=sim(cl[:half],fn); b,_,_=sim(cl[half:],fn)
            rets.append(tot);r1.append(a);r2.append(b);tr.append(t)
        # портфель = среднее по монетам (equal-weight ребаланс упрощённо)
        print(f"  {name:26}{np.mean(rets):+12.1f}%{np.mean(r1):+9.1f}%{np.mean(r2):+9.1f}%{np.mean(tr):11.1f}")
    print(f"\n  Читать: система должна бить Buy&Hold И быть + в ОБЕИХ половинах (робастность во времени).")
    print(f"  L/S = лонг и шорт; LongOnly = только лонг/флэт. Комса за смену позиции.")

if __name__=="__main__":
    main()
