# -*- coding: utf-8 -*-
"""
hunt_btc_leadlag.py — единственная сошедшаяся ниточка: BTC→альт lead-lag. Read-only.

Все агенты: единственный причинно-ОПЕРЕЖАЮЩИЙ сигнал — BTC двинул → мелкие альты догоняют ~1 мин.
Меряю на тейпе (1m): (1) кросс-корреляция alt_ret[t] vs btc_ret[t-lag], lag 0..5 — есть ли реальный лаг;
(2) вход «BTC двинул >k·σ → догоняющий альт в ту же сторону, держим H мин» — бьёт ли РЕАЛИСТИЧНЫЕ издержки альта.
Если после спреда/комсы тонких монет минус — ниточка тоже мертва (агенты предупредили: эдж там, где косты max).
"""
import sys, io, os, gzip, csv
from datetime import datetime, timedelta
import numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
TAPE=r"E:\trading-data\ticks"
ALTS=["DOGE","ADA","GALA","MEME","PENGU","NOT","TURBO","BOME","SATS","SHIB","FLOKI","NEIRO","MEW",
      "PEPE","BONK","HMSTR","LINEA","BSB","EDEN","CHZ","SPACE","PEOPLE","SAHARA","TRUTH"]

def bars1m(path):
    op=gzip.open if path.endswith(".gz") else open
    out={}
    with op(path,"rt",encoding="utf-8",errors="ignore") as fh:
        r=csv.reader(fh); next(r,None)
        for row in r:
            if len(row)<5: continue
            try: t=int(row[0]); p=float(row[4])
            except: continue
            out[(t//60000)*60000]=p     # close = последний принт в минуте
    return out
def load(sym):
    d=datetime(2026,5,11); e=datetime(2026,5,26); m={}
    base=os.path.join(TAPE,sym+"-USDT-SWAP")
    if not os.path.isdir(base): return {}
    while d<=e:
        p=os.path.join(base,d.strftime("%Y-%m-%d")+".csv.gz")
        if os.path.exists(p):
            try: m.update(bars1m(p))
            except Exception: pass
        d+=timedelta(days=1)
    return m

def main():
    print("гружу 1m из тейпа (BTC + альты)...",flush=True)
    btc=load("BTC")
    if not btc: print("нет BTC тейпа"); return
    print(f"BTC: {len(btc)} минут")
    # CCF + lead-lag вход
    print(f"\n=== (1) КРОСС-КОРРЕЛЯЦИЯ alt_ret[t] vs btc_ret[t-lag] (есть ли лаг) ===")
    print(f"  {'альт':8}{'lag0':>8}{'lag1':>8}{'lag2':>8}{'lag3':>8}  пик")
    laggers=[]; data={}
    for a in ALTS:
        m=load(a)
        if len(m)<2000: continue
        common=sorted(set(btc)&set(m))
        if len(common)<2000: continue
        bt=np.array([btc[t] for t in common]); at=np.array([m[t] for t in common])
        br=np.diff(bt)/bt[:-1]; ar=np.diff(at)/at[:-1]
        data[a]=(common,at,ar,br)
        cc=[]
        for lag in range(0,4):
            if lag==0: x,y=br,ar
            else: x,y=br[:-lag],ar[lag:]
            n=min(len(x),len(y));
            cc.append(np.corrcoef(x[:n],y[:n])[0,1] if n>50 and x[:n].std()>0 and y[:n].std()>0 else 0)
        peak=int(np.argmax(cc))
        if peak>=1 and cc[peak]>cc[0]+0.01: laggers.append(a)
        print(f"  {a:8}"+"".join(f"{c:>8.2f}" for c in cc)+f"  lag{peak}")
    print(f"\n  лаггеры (пик корреляции на lag>=1): {laggers if laggers else 'НЕТ — все синхронны (лага нет)'}")

    print(f"\n=== (2) LEAD-LAG ВХОД: BTC двинул >k·σ → догоняющий альт, hold H мин, после издержек ===")
    print(f"  {'издержки/круг':>14}{'ср.NET':>9}{'WR':>6}{'сделок':>8}")
    use=laggers if laggers else list(data.keys())   # если лаггеров нет — проверим на всех (докажем что и так пусто)
    for cost in (0.0010,0.0020,0.0030):              # тонкие альты: 0.1-0.3%/круг реалистично
        for H in (2,3):
            nets=[]
            for a in use:
                common,at,ar,br=data[a]
                sig=br.std()*2.5
                for t in range(20,len(br)-H-1):
                    if abs(br[t])>sig:
                        sgn=1 if br[t]>0 else -1
                        e=at[t+1]; x=at[t+1+H]
                        if e and x: nets.append(sgn*(x-e)/e*100-cost*100)
            if nets:
                wr=100*sum(1 for v in nets if v>0)/len(nets)
                print(f"  {cost*100:>11.2f}% H{H}{np.mean(nets):>9.3f}%{wr:>5.0f}%{len(nets):>8}")
    print(f"\n  Ниточка живая, если NET>0 после реалистичных издержек тонких альтов. Иначе — и она мертва.")

if __name__=="__main__":
    main()
