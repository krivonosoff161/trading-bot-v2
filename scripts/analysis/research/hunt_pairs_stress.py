# -*- coding: utf-8 -*-
"""
hunt_pairs_stress.py — ЛОМАЕМ стат-арб пар (анти-эйфория). Read-only.

Базовый прогон показал много + пар. Но 66 пар = риск selection bias; филлы оптимистичны. Три удара:
  1) COST: комса 0.05/0.10/0.15%/нога — сколько пар выживает.
  2) PARAM: окно/пороги — феномен устойчив или подгонка.
  3) TRUE-OOS: выбрать пары по 1-й половине (что знал бы заранее) → измерить их 2-ю половину ВСЛЕПУЮ.
     Если отбор по прошлому предсказывает будущее (+) → реально. Если нет → был мираж перебора.
"""
import sys, io, json, time, urllib.request, itertools
import numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
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

def sim_pair(pi,pj,fee,W,entry,exit_,stop):
    n=min(len(pi),len(pj)); pi=np.array(pi[-n:]); pj=np.array(pj[-n:])
    if n<W+40: return None
    s=np.log(pi)-np.log(pj); ri=np.diff(pi)/pi[:-1]; rj=np.diff(pj)/pj[:-1]
    eq=1.0; pos=0; trades=0; eqs=[1.0]
    for t in range(W,n-1):
        mu=s[t-W:t].mean(); sd=s[t-W:t].std(); z=(s[t]-mu)/sd if sd>0 else 0
        want=pos
        if pos==0:
            if z>entry: want=-1
            elif z<-entry: want=1
        else:
            if abs(z)<exit_ or abs(z)>stop or (pos==1 and z>0) or (pos==-1 and z<0): want=0
        if want!=pos:
            eq*=(1-fee*2)
            if pos!=0: trades+=1
            pos=want
        eq*=(1+pos*(ri[t]-rj[t])); eqs.append(eq)
    half=len(eqs)//2
    return (eq-1)*100, (eqs[half]/eqs[0]-1)*100, (eqs[-1]/eqs[half]-1)*100, trades

def allpairs(data,fee,W,entry,exit_,stop):
    out={}
    for a,b in itertools.combinations(sorted(data),2):
        r=sim_pair(data[a],data[b],fee,W,entry,exit_,stop)
        if r: out[f"{a}/{b}"]=r
    return out

def main():
    print(f"тяну 4H по {len(SYMS)} майорам...",flush=True)
    data={}
    for s in SYMS:
        cl=fetch_4h(s+"-USDT-SWAP")
        if len(cl)>=200: data[s]=cl
    print(f"загружено {len(data)}\n")

    print("=== УДАР 1: ИЗДЕРЖКИ (W=42,entry2/exit0.5/stop4) ===")
    print(f"  {'комса/нога':>11}{'пар +':>8}{'ср.ret +пар':>13}{'медиана всех':>14}")
    for fee in (0.0005,0.0010,0.0015,0.0020):
        ap=allpairs(data,fee,42,2.0,0.5,4.0); tots=[v[0] for v in ap.values()]
        pos=[t for t in tots if t>0]
        print(f"  {fee*100:>9.2f}% {len(pos):>7}/{len(tots)}{np.mean(pos) if pos else 0:>+12.1f}%{np.median(tots):>+13.1f}%")

    print("\n=== УДАР 2: ПОРОГИ/ОКНО (комса 0.10%/нога — реалистично) ===")
    print(f"  {'W/entry/exit':>16}{'пар +':>8}{'медиана':>10}")
    for W,en,ex in [(30,2.0,0.5),(42,2.0,0.5),(60,2.0,0.5),(42,1.5,0.5),(42,2.5,0.75)]:
        ap=allpairs(data,0.0010,W,en,ex,4.0); tots=[v[0] for v in ap.values()]
        pos=sum(1 for t in tots if t>0)
        print(f"  W{W}/e{en}/x{ex:<4}{pos:>9}/{len(tots)}{np.median(tots):>+9.1f}%")

    print("\n=== УДАР 3: TRUE-OOS ОТБОР (комса 0.10%/нога) — РЕШАЮЩИЙ ===")
    ap=allpairs(data,0.0010,42,2.0,0.5,4.0)
    r1={k:v[1] for k,v in ap.items()}; r2={k:v[2] for k,v in ap.items()}
    allr2=list(r2.values())
    for topk in (8,12,20):
        sel=sorted(ap,key=lambda k:-r1[k])[:topk]     # лучшие по 1-й половине
        oos=[r2[k] for k in sel]
        hit=100*sum(1 for x in oos if x>0)//len(oos)
        print(f"  топ-{topk} по 1-й половине → их 2-я (OOS): ср {np.mean(oos):+.1f}%, медиана {np.median(oos):+.1f}%, +доля {hit}%")
    print(f"  база (все пары) 2-я половина: ср {np.mean(allr2):+.1f}%, медиана {np.median(allr2):+.1f}%")
    print(f"\n  ВЕРДИКТ: если топ-по-1й-половине дают + во 2-й (OOS) И заметно выше базы → отбор по прошлому")
    print(f"  предсказывает будущее = РЕАЛЬНЫЙ нейтральный эдж. Если ~база/минус → был перебор.")

if __name__=="__main__":
    main()
